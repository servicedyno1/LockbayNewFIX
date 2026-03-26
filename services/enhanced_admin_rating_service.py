"""
Enhanced Admin Rating Service
Advanced rating management, fraud detection, and moderation tools
Addresses Issues: #5, #6, #8, #10, #11, #16
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, or_, text
from collections import defaultdict

from database import SessionLocal
from models import Rating, User, Escrow, AuditLog
from services.enhanced_reputation_service import EnhancedReputationService
from services.email import EmailService

logger = logging.getLogger(__name__)


@dataclass
class RatingModerationCase:
    """Rating moderation case data structure"""
    rating_id: int
    rated_user_id: int
    rater_user_id: int
    rating_value: int
    comment: str
    escrow_id: int
    created_at: datetime
    flags: List[str]
    severity: str
    investigation_notes: str
    status: str  # 'pending', 'approved', 'flagged', 'removed'


@dataclass
class FraudDetectionResult:
    """Fraud detection analysis result"""
    user_id: int
    fraud_score: int
    risk_level: str
    detected_patterns: List[str]
    evidence: Dict[str, Any]
    recommended_actions: List[str]
    analysis_date: datetime


class EnhancedAdminRatingService:
    """Comprehensive admin rating management and fraud detection service"""
    
    def __init__(self):
        self.email_service = EmailService()
    
    @staticmethod
    def get_advanced_rating_analytics(days: int = 30, session: Optional[Session] = None) -> Dict[str, Any]:
        """
        Get advanced rating analytics for admin dashboard
        Addresses Issue: #10 - Insufficient Rating Analytics
        """
        if session is None:
            session = SessionLocal()
            close_session = True
        else:
            close_session = False
            
        try:
            start_date = datetime.utcnow() - timedelta(days=days)
            
            # Basic metrics
            basic_analytics = EnhancedReputationService.get_rating_analytics(days, session)
            
            # Advanced metrics
            
            # 1. Rating velocity (ratings per day trend)
            daily_velocity = session.execute(text("""
                SELECT DATE(created_at) as date, COUNT(*) as count
                FROM ratings 
                WHERE created_at >= :start_date
                GROUP BY DATE(created_at)
                ORDER BY date
            """), {"start_date": start_date}).fetchall()
            
            # 2. Average time between trade completion and rating
            rating_delays = session.execute(text("""
                SELECT AVG(EXTRACT(EPOCH FROM (r.created_at - e.completed_at))/3600) as avg_hours
                FROM ratings r
                JOIN escrows e ON r.escrow_id = e.id
                WHERE e.completed_at IS NOT NULL 
                AND r.created_at >= :start_date
            """), {"start_date": start_date}).scalar()
            
            # 3. Comment rate (percentage of ratings with comments)
            total_recent_ratings = session.query(Rating).filter(Rating.created_at >= start_date).count()
            commented_ratings = session.query(Rating).filter(
                Rating.created_at >= start_date,
                Rating.comment.isnot(None),
                Rating.comment != ''
            ).count()
            comment_rate = commented_ratings / max(1, total_recent_ratings)
            
            # 4. Reciprocal rating rate
            reciprocal_count = session.execute(text("""
                SELECT COUNT(*) FROM ratings r1
                JOIN ratings r2 ON r1.rater_id = r2.rated_id AND r1.rated_id = r2.rater_id
                WHERE r1.created_at >= :start_date
                AND r1.id < r2.id
            """), {"start_date": start_date}).scalar()
            reciprocal_rate = (reciprocal_count * 2) / max(1, total_recent_ratings)
            
            # 5. Rating distribution by trust level
            trust_level_distribution = {}
            for level in ['new', 'bronze', 'silver', 'gold', 'platinum', 'diamond']:
                count = session.execute(text("""
                    SELECT COUNT(DISTINCT r.rated_id) FROM ratings r
                    JOIN users u ON r.rated_id = u.id
                    WHERE r.created_at >= :start_date
                """), {"start_date": start_date}).scalar()
                trust_level_distribution[level] = count or 0
            
            # 6. Manipulation indicators
            manipulation_flags = {
                'rapid_ratings': session.execute(text("""
                    SELECT COUNT(*) FROM (
                        SELECT rater_id, COUNT(*) as rating_count
                        FROM ratings 
                        WHERE created_at >= :start_date
                        GROUP BY rater_id
                        HAVING COUNT(*) >= 10
                    ) x
                """), {"start_date": start_date}).scalar(),
                
                'new_account_ratings': session.execute(text("""
                    SELECT COUNT(*) FROM ratings r
                    JOIN users u ON r.rater_id = u.id
                    WHERE r.created_at >= :start_date
                    AND u.created_at >= :start_date - INTERVAL '7 days'
                """), {"start_date": start_date}).scalar(),
                
                'perfect_score_clusters': session.execute(text("""
                    SELECT COUNT(*) FROM (
                        SELECT rated_id, COUNT(*) as perfect_count
                        FROM ratings 
                        WHERE created_at >= :start_date AND rating = 5
                        GROUP BY rated_id
                        HAVING COUNT(*) >= 5
                    ) x
                """), {"start_date": start_date}).scalar()
            }
            
            # 7. Platform health metrics
            health_metrics = {
                'average_rating_trend': _calculate_rating_trend(session, days),
                'user_engagement': commented_ratings / max(1, total_recent_ratings),
                'trust_building_rate': session.execute(text("""
                    SELECT COUNT(*) FROM ratings r
                    WHERE r.created_at >= :start_date AND r.rating >= 4
                """), {"start_date": start_date}).scalar() / max(1, total_recent_ratings),
                'dispute_correlation': _calculate_dispute_correlation(session, start_date)
            }
            
            # 8. Geographic/temporal patterns
            hourly_distribution = defaultdict(int)
            for rating in session.query(Rating).filter(Rating.created_at >= start_date):
                hour = rating.created_at.hour
                hourly_distribution[hour] += 1
            
            return {
                **basic_analytics,
                'advanced_metrics': {
                    'rating_velocity': [{'date': str(d), 'count': c} for d, c in daily_velocity],
                    'avg_rating_delay_hours': round(rating_delays or 0, 2),
                    'comment_rate': round(comment_rate, 3),
                    'reciprocal_rate': round(reciprocal_rate, 3),
                    'trust_level_distribution': trust_level_distribution,
                    'manipulation_flags': manipulation_flags,
                    'health_metrics': health_metrics,
                    'hourly_distribution': dict(hourly_distribution)
                },
                'analysis_date': datetime.utcnow()
            }
            
        except Exception as e:
            logger.error(f"Error generating advanced rating analytics: {e}")
            return basic_analytics or {}
        finally:
            if close_session:
                session.close()
    
    @staticmethod
    def detect_rating_fraud_patterns(session: Optional[Session] = None) -> List[FraudDetectionResult]:
        """
        Comprehensive fraud detection across all users
        Addresses Issue: #8 - Security Vulnerabilities
        """
        if session is None:
            session = SessionLocal()
            close_session = True
        else:
            close_session = False
            
        try:
            fraud_results = []
            
            # Get all users with ratings
            rated_users = session.query(
                Rating.rated_id,
                func.count(Rating.id).label('rating_count')
            ).group_by(Rating.rated_id).having(
                func.count(Rating.id) >= 3
            ).all()
            
            for user_id, rating_count in rated_users:
                try:
                    # Run comprehensive fraud detection
                    fraud_analysis = EnhancedReputationService.detect_rating_manipulation(user_id, session)
                    
                    if fraud_analysis['score'] >= 25:  # Threshold for concern
                        # Get additional suspicious patterns
                        additional_patterns = _detect_advanced_fraud_patterns(user_id, session)
                        
                        # Calculate comprehensive fraud score
                        total_score = fraud_analysis['score'] + additional_patterns['score']
                        
                        # Determine risk level
                        if total_score >= 75:
                            risk_level = 'critical'
                        elif total_score >= 50:
                            risk_level = 'high'
                        elif total_score >= 25:
                            risk_level = 'medium'
                        else:
                            risk_level = 'low'
                        
                        # Combine all detected patterns
                        all_patterns = fraud_analysis['flags'] + additional_patterns['patterns']
                        
                        # Generate evidence
                        evidence = {
                            'basic_analysis': fraud_analysis,
                            'advanced_analysis': additional_patterns,
                            'total_ratings': rating_count,
                            'investigation_priority': _calculate_investigation_priority(total_score, risk_level)
                        }
                        
                        # Generate recommended actions
                        recommended_actions = _generate_fraud_response_actions(total_score, risk_level, all_patterns)
                        
                        fraud_results.append(FraudDetectionResult(
                            user_id=user_id,
                            fraud_score=total_score,
                            risk_level=risk_level,
                            detected_patterns=all_patterns,
                            evidence=evidence,
                            recommended_actions=recommended_actions,
                            analysis_date=datetime.utcnow()
                        ))
                        
                except Exception as user_error:
                    logger.error(f"Error analyzing user {user_id} for fraud: {user_error}")
                    continue
            
            # Sort by fraud score (highest first)
            fraud_results.sort(key=lambda x: x.fraud_score, reverse=True)
            
            return fraud_results
            
        except Exception as e:
            logger.error(f"Error in fraud pattern detection: {e}")
            return []
        finally:
            if close_session:
                session.close()
    
    @staticmethod
    def create_moderation_case(rating_id: int, flags: List[str], notes: str = "",
                              admin_user_id: int = None, session: Optional[Session] = None) -> Optional[RatingModerationCase]:
        """
        Create a rating moderation case
        Addresses Issue: #16 - Missing Rating Policies
        """
        if session is None:
            session = SessionLocal()
            close_session = True
        else:
            close_session = False
            
        try:
            # Get rating details
            rating = session.query(Rating).filter(Rating.id == rating_id).first()
            if not rating:
                return None
            
            # Determine severity based on flags
            severity = 'low'
            if any(flag in ['manipulation', 'fake_review', 'bot_activity'] for flag in flags):
                severity = 'high'
            elif any(flag in ['inappropriate_comment', 'spam', 'revenge_rating'] for flag in flags):
                severity = 'medium'
            
            # Create moderation case
            case = RatingModerationCase(
                rating_id=rating_id,
                rated_user_id=rating.rated_id,
                rater_user_id=rating.rater_id,
                rating_value=rating.rating,
                comment=rating.comment or "",
                escrow_id=rating.escrow_id,
                created_at=rating.created_at,
                flags=flags,
                severity=severity,
                investigation_notes=notes,
                status='pending'
            )
            
            # Log the moderation action
            if admin_user_id:
                audit_log = AuditLog(
                    event_type='rating_moderation_case_created',
                    description=f"Moderation case created for rating {rating_id}",
                    user_id=admin_user_id,
                    entity_type='rating',
                    entity_id=str(rating_id),
                    details={'flags': flags, 'severity': severity}
                )
                session.add(audit_log)
                session.commit()
            
            return case
            
        except Exception as e:
            logger.error(f"Error creating moderation case: {e}")
            return None
        finally:
            if close_session:
                session.close()
    
    @staticmethod
    def moderate_rating_action(rating_id: int, action: str, reason: str,
                              admin_user_id: int, session: Optional[Session] = None) -> bool:
        """
        Take moderation action on a rating
        Addresses Issue: #16 - Missing Rating Policies
        """
        if session is None:
            session = SessionLocal()
            close_session = True
        else:
            close_session = False
            
        try:
            rating = session.query(Rating).filter(Rating.id == rating_id).first()
            if not rating:
                return False
            
            original_rating = rating.rating
            original_comment = rating.comment
            
            # Perform moderation action
            if action == 'remove_rating':
                session.delete(rating)
                action_description = "Rating removed"
                
            elif action == 'remove_comment':
                rating.comment = "[Comment removed by moderator]"
                action_description = "Comment removed"
                
            elif action == 'flag_inappropriate':
                # Add flag to rating (you might want to add a flags column to ratings table)
                action_description = "Rating flagged as inappropriate"
                
            elif action == 'warn_user':
                # Send warning to the user who gave the rating
                action_description = "User warned about rating behavior"
                # TODO: Implement user warning system
                
            elif action == 'approve':
                action_description = "Rating approved after review"
                
            else:
                logger.error(f"Unknown moderation action: {action}")
                return False
            
            # Log the moderation action
            audit_log = AuditLog(
                event_type='rating_moderation_action',
                description=f"{action_description} for rating {rating_id}",
                user_id=admin_user_id,
                entity_type='rating',
                entity_id=str(rating_id),
                details={
                    'action': action,
                    'reason': reason,
                    'original_rating': original_rating,
                    'original_comment': original_comment,
                    'admin_user_id': admin_user_id
                }
            )
            session.add(audit_log)
            session.commit()
            
            # Notify affected users if appropriate
            if action in ['remove_rating', 'remove_comment']:
                _notify_rating_moderation(rating, action, reason, session)
            
            return True
            
        except Exception as e:
            logger.error(f"Error performing moderation action: {e}")
            session.rollback()
            return False
        finally:
            if close_session:
                session.close()
    
    @staticmethod
    def generate_rating_report(user_id: int, report_type: str = 'comprehensive',
                              session: Optional[Session] = None) -> Dict[str, Any]:
        """
        Generate comprehensive rating report for a user
        Addresses Issue: #11 - Missing Reporting Features
        """
        if session is None:
            session = SessionLocal()
            close_session = True
        else:
            close_session = False
            
        try:
            user = session.query(User).filter(User.id == user_id).first()
            if not user:
                return {}
            
            # Get comprehensive reputation data
            reputation = EnhancedReputationService.get_comprehensive_reputation(user_id, session)
            if not reputation:
                return {}
            
            # Get all ratings
            received_ratings = session.query(Rating).filter(Rating.rated_id == user_id).all()
            given_ratings = session.query(Rating).filter(Rating.rater_id == user_id).all()
            
            # Calculate detailed metrics
            report = {
                'user_info': {
                    'user_id': user_id,
                    'username': user.username,
                    'display_name': user.first_name or user.username,
                    'member_since': user.created_at.isoformat() if user.created_at else None,
                    'email_verified': getattr(user, 'email_verified', False),
                    'phone_verified': getattr(user, 'phone_verified', False)
                },
                'reputation_summary': {
                    'overall_rating': reputation.overall_rating,
                    'total_ratings_received': reputation.total_ratings,
                    'trust_level': reputation.trust_level,
                    'trust_score': reputation.trust_score,
                    'completion_rate': reputation.completion_rate,
                    'dispute_rate': reputation.dispute_rate,
                    'total_volume': float(reputation.total_volume),
                    'verification_status': reputation.verification_status,
                    'reputation_trend': reputation.reputation_trend,
                    'badges': reputation.badges,
                    'risk_level': reputation.risk_level
                },
                'rating_details': {
                    'ratings_received': len(received_ratings),
                    'ratings_given': len(given_ratings),
                    'rating_distribution': reputation.rating_distribution,
                    'average_given': sum(r.rating for r in given_ratings) / len(given_ratings) if given_ratings else 0,
                    'comment_rate_received': len([r for r in received_ratings if r.comment]) / max(1, len(received_ratings)),
                    'comment_rate_given': len([r for r in given_ratings if r.comment]) / max(1, len(given_ratings))
                },
                'temporal_analysis': {
                    'first_rating_received': min(r.created_at for r in received_ratings).isoformat() if received_ratings else None,
                    'latest_rating_received': max(r.created_at for r in received_ratings).isoformat() if received_ratings else None,
                    'first_rating_given': min(r.created_at for r in given_ratings).isoformat() if given_ratings else None,
                    'latest_rating_given': max(r.created_at for r in given_ratings).isoformat() if given_ratings else None,
                    'ratings_last_30_days': len([r for r in received_ratings if (datetime.utcnow() - r.created_at).days <= 30]),
                    'ratings_last_90_days': len([r for r in received_ratings if (datetime.utcnow() - r.created_at).days <= 90])
                },
                'generated_at': datetime.utcnow().isoformat(),
                'report_type': report_type
            }
            
            # Add detailed analysis for comprehensive reports
            if report_type == 'comprehensive':
                # Fraud analysis
                fraud_analysis = EnhancedReputationService.detect_rating_manipulation(user_id, session)
                report['fraud_analysis'] = fraud_analysis
                
                # Recent reviews detail
                recent_reviews = session.query(Rating).filter(
                    Rating.rated_id == user_id
                ).order_by(desc(Rating.created_at)).limit(10).all()
                
                report['recent_reviews'] = []
                for rating in recent_reviews:
                    rater = session.query(User).filter(User.id == rating.rater_id).first()
                    report['recent_reviews'].append({
                        'rating': rating.rating,
                        'comment': rating.comment,
                        'rater_name': rater.first_name if rater else 'Anonymous',
                        'created_at': rating.created_at.isoformat(),
                        'days_ago': (datetime.utcnow() - rating.created_at).days
                    })
                
                # Trading performance correlation
                escrows = session.query(Escrow).filter(
                    or_(Escrow.buyer_id == user_id, Escrow.seller_id == user_id)
                ).all()
                
                report['trading_correlation'] = {
                    'total_trades': len(escrows),
                    'trades_with_ratings': len(received_ratings),
                    'rating_coverage': len(received_ratings) / max(1, len(escrows)),
                    'avg_trade_amount': sum(float(e.amount) for e in escrows) / max(1, len(escrows))
                }
            
            return report
            
        except Exception as e:
            logger.error(f"Error generating rating report for user {user_id}: {e}")
            return {}
        finally:
            if close_session:
                session.close()
    
    @staticmethod
    def export_rating_data(user_id: int, format: str = 'json', session: Optional[Session] = None) -> Optional[str]:
        """
        Export user rating data in various formats
        Addresses Issue: #11 - Missing Reporting Features
        """
        if session is None:
            session = SessionLocal()
            close_session = True
        else:
            close_session = False
            
        try:
            # Generate comprehensive report
            report = EnhancedAdminRatingService.generate_rating_report(user_id, 'comprehensive', session)
            
            if format == 'json':
                import json
                return json.dumps(report, indent=2, default=str)
            
            elif format == 'csv':
                import csv
                import io
                
                output = io.StringIO()
                
                # Export ratings received
                writer = csv.writer(output)
                writer.writerow(['Rating', 'Comment', 'Rater', 'Date', 'Days Ago'])
                
                for review in report.get('recent_reviews', []):
                    writer.writerow([
                        review['rating'],
                        review['comment'] or '',
                        review['rater_name'],
                        review['created_at'],
                        review['days_ago']
                    ])
                
                return output.getvalue()
            
            elif format == 'summary':
                # Generate human-readable summary
                summary = f"""Rating Report for {report['user_info']['display_name']}
Generated: {report['generated_at']}

REPUTATION SUMMARY:
• Overall Rating: {report['reputation_summary']['overall_rating']}/5.0
• Total Ratings: {report['reputation_summary']['total_ratings_received']}
• Trust Level: {report['reputation_summary']['trust_level'].title()}
• Completion Rate: {report['reputation_summary']['completion_rate']*100:.1f}%
• Risk Level: {report['reputation_summary']['risk_level'].title()}

RATING DISTRIBUTION:
• 5 Stars: {report['reputation_summary']['rating_distribution'][5]}
• 4 Stars: {report['reputation_summary']['rating_distribution'][4]}
• 3 Stars: {report['reputation_summary']['rating_distribution'][3]}
• 2 Stars: {report['reputation_summary']['rating_distribution'][2]}
• 1 Star: {report['reputation_summary']['rating_distribution'][1]}

BADGES: {', '.join(report['reputation_summary']['badges']) if report['reputation_summary']['badges'] else 'None'}

RECENT ACTIVITY:
• Last 30 days: {report['temporal_analysis']['ratings_last_30_days']} ratings
• Last 90 days: {report['temporal_analysis']['ratings_last_90_days']} ratings
"""
                return summary
            
            else:
                logger.error(f"Unsupported export format: {format}")
                return None
                
        except Exception as e:
            logger.error(f"Error exporting rating data: {e}")
            return None
        finally:
            if close_session:
                session.close()


# Helper functions

def _calculate_rating_trend(session: Session, days: int) -> str:
    """Calculate overall platform rating trend"""
    try:
        mid_point = days // 2
        recent_avg = session.execute(text("""
            SELECT AVG(rating) FROM ratings 
            WHERE created_at >= NOW() - INTERVAL '%s days'
        """ % mid_point)).scalar()
        
        older_avg = session.execute(text("""
            SELECT AVG(rating) FROM ratings 
            WHERE created_at >= NOW() - INTERVAL '%s days'
            AND created_at < NOW() - INTERVAL '%s days'
        """ % (days, mid_point))).scalar()
        
        if recent_avg and older_avg:
            diff = recent_avg - older_avg
            if diff > 0.1:
                return 'improving'
            elif diff < -0.1:
                return 'declining'
        
        return 'stable'
    except Exception as e:
        logger.error(f"Error calculating rating trend: {e}")
        return 'unknown'


def _calculate_dispute_correlation(session: Session, start_date: datetime) -> float:
    """Calculate correlation between low ratings and disputes"""
    try:
        low_ratings_with_disputes = session.execute(text("""
            SELECT COUNT(*) FROM ratings r
            JOIN escrows e ON r.escrow_id = e.id
            JOIN disputes d ON e.id = d.escrow_id
            WHERE r.created_at >= :start_date AND r.rating <= 2
        """), {"start_date": start_date}).scalar()
        
        total_low_ratings = session.execute(text("""
            SELECT COUNT(*) FROM ratings 
            WHERE created_at >= :start_date AND rating <= 2
        """), {"start_date": start_date}).scalar()
        
        return low_ratings_with_disputes / max(1, total_low_ratings)
    except Exception as e:
        logger.error(f"Error calculating dispute correlation: {e}")
        return 0.0


def _detect_advanced_fraud_patterns(user_id: int, session: Session) -> Dict[str, Any]:
    """Detect advanced fraud patterns beyond basic analysis"""
    patterns = []
    score = 0
    
    try:
        # Pattern 1: Account age vs rating velocity
        user = session.query(User).filter(User.id == user_id).first()
        if user and user.created_at:
            account_age_days = (datetime.utcnow() - user.created_at).days
            rating_count = session.query(Rating).filter(Rating.rated_id == user_id).count()
            
            if account_age_days > 0 and rating_count / account_age_days > 0.5:  # More than 1 rating every 2 days
                patterns.append("High rating velocity for account age")
                score += 15
        
        # Pattern 2: Geographic clustering (if you have IP data)
        # This would require IP tracking in ratings
        
        # Pattern 3: Comment similarity analysis
        ratings_with_comments = session.query(Rating).filter(
            Rating.rated_id == user_id,
            Rating.comment.isnot(None),
            Rating.comment != ''
        ).all()
        
        if len(ratings_with_comments) >= 3:
            # Simple similarity check (could be enhanced with NLP)
            comments = [r.comment.lower() for r in ratings_with_comments]
            similar_pairs = 0
            
            for i in range(len(comments)):
                for j in range(i + 1, len(comments)):
                    # Basic similarity: shared words
                    words1 = set(comments[i].split())
                    words2 = set(comments[j].split())
                    similarity = len(words1 & words2) / len(words1 | words2) if words1 | words2 else 0
                    
                    if similarity > 0.6:  # 60% word overlap
                        similar_pairs += 1
            
            if similar_pairs >= 2:
                patterns.append("Similar comment patterns detected")
                score += 20
        
        # Pattern 4: Trading pattern analysis
        escrows_as_seller = session.query(Escrow).filter(Escrow.seller_id == user_id).count()
        ratings_as_seller = session.query(Rating).filter(
            Rating.rated_id == user_id,
            Rating.category == 'seller'
        ).count()
        
        # Suspiciously high rating rate
        if escrows_as_seller > 0 and ratings_as_seller / escrows_as_seller > 0.9:
            if ratings_as_seller >= 10:  # Only flag if significant volume
                patterns.append("Unusually high rating rate per trade")
                score += 10
        
        return {
            'patterns': patterns,
            'score': score,
            'details': {
                'account_age_days': account_age_days if 'user' in locals() and user else 0,
                'rating_velocity': rating_count / max(1, account_age_days) if 'account_age_days' in locals() and account_age_days > 0 else 0,
                'comment_analysis': len(ratings_with_comments),
                'trading_analysis': {
                    'escrows_as_seller': escrows_as_seller,
                    'ratings_as_seller': ratings_as_seller,
                    'rating_rate': ratings_as_seller / max(1, escrows_as_seller)
                }
            }
        }
        
    except Exception as e:
        logger.error(f"Error in advanced fraud detection for user {user_id}: {e}")
        return {'patterns': [], 'score': 0, 'details': {}}


def _calculate_investigation_priority(score: int, risk_level: str) -> str:
    """Calculate investigation priority"""
    if risk_level == 'critical' or score >= 75:
        return 'urgent'
    elif risk_level == 'high' or score >= 50:
        return 'high'
    elif score >= 25:
        return 'medium'
    else:
        return 'low'


def _generate_fraud_response_actions(score: int, risk_level: str, patterns: List[str]) -> List[str]:
    """Generate recommended actions based on fraud analysis"""
    actions = []
    
    if score >= 75:
        actions.extend([
            "Immediately investigate all ratings",
            "Consider temporary rating suspension",
            "Review all associated trades",
            "Flag account for manual review"
        ])
    elif score >= 50:
        actions.extend([
            "Detailed investigation required",
            "Review recent ratings and comments",
            "Monitor future rating activity closely",
            "Consider warning the user"
        ])
    elif score >= 25:
        actions.extend([
            "Monitor rating patterns",
            "Review flagged activities",
            "Watch for escalating behavior"
        ])
    
    # Pattern-specific actions
    if "High rating velocity" in ' '.join(patterns):
        actions.append("Implement rate limiting for this user")
    
    if "Similar comment patterns" in ' '.join(patterns):
        actions.append("Review all comments for authenticity")
    
    if "new accounts" in ' '.join(patterns):
        actions.append("Verify identity of rating accounts")
    
    return actions


def _notify_rating_moderation(rating: Rating, action: str, reason: str, session: Session):
    """Notify users about rating moderation actions"""
    try:
        # Notify the person whose rating was moderated
        rater = session.query(User).filter(User.id == rating.rater_id).first()
        session.query(User).filter(User.id == rating.rated_id).first()
        
        if rater and rater.email:
            email_service = EmailService()
            
            subject = f"Rating Moderation - {action.replace('_', ' ').title()}"
            
            message = f"""
            Dear {rater.first_name or 'User'},
            
            We're writing to inform you that one of your ratings has been moderated.
            
            Action Taken: {action.replace('_', ' ').title()}
            Reason: {reason}
            
            Original Rating: {rating.rating}/5 stars
            {'Comment: "' + rating.comment + '"' if rating.comment else 'No comment provided'}
            
            If you believe this action was taken in error, please contact our support team.
            
            Thank you for helping maintain the quality of our rating system.
            
            Best regards,
            The Moderation Team
            """
            
            email_service.send_email(
                to_email=rater.email,
                subject=subject,
                body=message
            )
            
    except Exception as e:
        logger.error(f"Error sending moderation notification: {e}")