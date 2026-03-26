"""
Seller Profile Handler
Comprehensive seller profile pages with ratings, analytics, and trust information
Addresses Issues: #3, #4, #5, #14, #15
"""

import logging
from datetime import datetime
from typing import Dict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler
from sqlalchemy import desc, select

from database import SessionLocal, async_managed_session
from models import User, Rating, Escrow, EscrowStatus
from services.enhanced_reputation_service import EnhancedReputationService, ReputationScore
from utils.callback_utils import safe_edit_message_text, safe_answer_callback_query

logger = logging.getLogger(__name__)


async def handle_view_seller_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Main seller profile handler
    Addresses Issue: #3 - No Seller Profile/Reputation Pages
    """
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "👤 Loading profile")
    
    try:
        # Extract user ID from callback data
        if query and query.data.startswith('seller_profile:'):
            user_id = int(query.data.split(':')[1])
        else:
            if query:
                await query.edit_message_text("❌ Invalid profile request")
            return ConversationHandler.END
        
        async with async_managed_session() as session:
            # Get user and reputation
            user_stmt = select(User).where(User.id == user_id)
            user_result = await session.execute(user_stmt)
            user = user_result.scalar_one_or_none()
            if not user:
                if query:
                    await query.edit_message_text("❌ User not found")
                return ConversationHandler.END
            
            reputation = EnhancedReputationService.get_comprehensive_reputation(user_id, session)
            if not reputation:
                if query:
                    await query.edit_message_text("❌ Unable to load reputation data")
                return ConversationHandler.END
            
            # Build comprehensive profile message
            message = await _build_seller_profile_message(user, reputation, session)
            
            keyboard = [
                [
                    InlineKeyboardButton("📊 Full Statistics", callback_data=f"seller_stats:{user_id}"),
                    InlineKeyboardButton("💬 All Reviews", callback_data=f"seller_reviews:{user_id}")
                ],
                [
                    InlineKeyboardButton("📈 Rating History", callback_data=f"seller_history:{user_id}"),
                    InlineKeyboardButton("🏆 Achievements", callback_data=f"seller_badges:{user_id}")
                ],
                [
                    InlineKeyboardButton("🛡️ Start Trade", callback_data=f"create_escrow_with:{user.username or user.email}"),
                    InlineKeyboardButton("📨 Contact", callback_data=f"contact_seller:{user_id}")
                ],
                [
                    InlineKeyboardButton("🔙 Back", callback_data="browse_sellers"),
                    InlineKeyboardButton("🏠 Home", callback_data="main_menu")
                ]
            ]
            
            if query:
                await safe_edit_message_text(
                    query,
                    message,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            
    except Exception as e:
        logger.error(f"Error displaying seller profile: {e}")
        if query:
            await query.edit_message_text("❌ Error loading profile. Please try again.")
        return ConversationHandler.END
    
    return ConversationHandler.END


async def handle_seller_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Detailed seller statistics view
    Addresses Issue: #10 - Insufficient Rating Analytics
    """
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📊 Loading stats")
    
    try:
        user_id = int(query.data.split(':')[1])
        
        session = SessionLocal()
        try:
            user = session.query(User).filter(User.id == user_id).first()
            reputation = EnhancedReputationService.get_comprehensive_reputation(user_id, session)
            
            if not user or not reputation:
                await query.edit_message_text("❌ Unable to load statistics")
                return ConversationHandler.END
            
            # Get detailed trading statistics
            stats = await _get_detailed_trading_stats(user_id, session)
            
            message = f"""📊 {user.first_name or user.username} - Detailed Statistics

🏆 Overall Performance
• Rating: {reputation.overall_rating:.2f}/5.0 ⭐
• Trust Score: {reputation.trust_score}/100 🎯
• Trust Level: {reputation.trust_level.title()} 🏅
• Risk Level: {reputation.risk_level.title()} ⚠️

📈 Trading Activity
• Total Trades: {stats['total_trades']:,}
• Completed: {stats['completed_trades']:,}
• Completion Rate: {stats['completion_rate']:.1%}
• Total Volume: ${stats['total_volume']:,.2f} USD
• Average Trade: ${stats['avg_trade_size']:,.2f} USD

💬 Rating Breakdown
• Total Ratings: {reputation.total_ratings}
• 5 ⭐: {reputation.rating_distribution[5]} ({reputation.rating_distribution[5]/max(1,reputation.total_ratings)*100:.1f}%)
• 4 ⭐: {reputation.rating_distribution[4]} ({reputation.rating_distribution[4]/max(1,reputation.total_ratings)*100:.1f}%)
• 3 ⭐: {reputation.rating_distribution[3]} ({reputation.rating_distribution[3]/max(1,reputation.total_ratings)*100:.1f}%)
• 2 ⭐: {reputation.rating_distribution[2]} ({reputation.rating_distribution[2]/max(1,reputation.total_ratings)*100:.1f}%)
• 1 ⭐: {reputation.rating_distribution[1]} ({reputation.rating_distribution[1]/max(1,reputation.total_ratings)*100:.1f}%)

🔍 Quality Metrics
• Dispute Rate: {reputation.dispute_rate:.1%}
• Recent Activity: {reputation.recent_activity} ratings (30 days)
• Trend: {reputation.reputation_trend.title()}
• Member Since: {user.created_at.strftime('%B %Y') if user.created_at else 'Unknown'}

🛡️ Trust Indicators
• Verification: {reputation.verification_status.replace('_', ' ').title()}
• Badges: {len(reputation.badges)} earned
• Platform Standing: Good"""

            keyboard = [
                [
                    InlineKeyboardButton("💬 Reviews", callback_data=f"seller_reviews:{user_id}"),
                    InlineKeyboardButton("📈 History", callback_data=f"seller_history:{user_id}")
                ],
                [
                    InlineKeyboardButton("🔙 Profile", callback_data=f"seller_profile:{user_id}"),
                    InlineKeyboardButton("🏠 Home", callback_data="main_menu")
                ]
            ]
            
            await safe_edit_message_text(
                query,
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Error displaying seller statistics: {e}")
        await query.edit_message_text("❌ Error loading statistics")
        return ConversationHandler.END
    
    return ConversationHandler.END


async def handle_seller_reviews(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    All seller reviews view
    Addresses Issue: #5 - Limited User Experience
    """
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💬 Loading reviews")
    
    try:
        user_id = int(query.data.split(':')[1])
        
        session = SessionLocal()
        try:
            user = session.query(User).filter(User.id == user_id).first()
            if not user:
                await query.edit_message_text("❌ User not found")
                return ConversationHandler.END
            
            # Get all reviews for this seller
            reviews = session.query(Rating).filter(
                Rating.rated_id == user_id,
                Rating.category == 'seller'
            ).order_by(desc(Rating.created_at)).limit(10).all()
            
            if not reviews:
                message = f"💬 {user.first_name or user.username} - Reviews\n\n📝 No reviews yet"
            else:
                avg_rating = sum(r.rating for r in reviews) / len(reviews)
                
                message = f"""💬 {user.first_name or user.username} - Reviews

⭐ {avg_rating:.1f}/5.0 • {len(reviews)} reviews (showing recent 10)

"""
                
                for i, review in enumerate(reviews, 1):
                    # Get reviewer info
                    reviewer = session.query(User).filter(User.id == review.rater_id).first()
                    reviewer_name = reviewer.first_name if reviewer else "Anonymous"
                    
                    stars = "⭐" * review.rating + "☆" * (5 - review.rating)
                    days_ago = (datetime.utcnow() - review.created_at).days
                    
                    message += f"{i}. {stars} ({review.rating}/5)\n"
                    message += f"👤 {reviewer_name} • {days_ago} days ago\n"
                    
                    if review.comment:
                        comment = review.comment[:150] + "..." if len(review.comment) > 150 else review.comment
                        message += f"💭 \"{comment}\"\n"
                    else:
                        message += "💭 No comment provided\n"
                    
                    message += "\n"
            
            keyboard = [
                [
                    InlineKeyboardButton("📊 Statistics", callback_data=f"seller_stats:{user_id}"),
                    InlineKeyboardButton("📈 History", callback_data=f"seller_history:{user_id}")
                ],
                [
                    InlineKeyboardButton("🔙 Profile", callback_data=f"seller_profile:{user_id}"),
                    InlineKeyboardButton("🏠 Home", callback_data="main_menu")
                ]
            ]
            
            await safe_edit_message_text(
                query,
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Error displaying seller reviews: {e}")
        await query.edit_message_text("❌ Error loading reviews")
        return ConversationHandler.END
    
    return ConversationHandler.END


async def handle_seller_rating_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Seller rating history and trends
    Addresses Issue: #10 - Insufficient Rating Analytics
    """
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📈 Loading history")
    
    try:
        user_id = int(query.data.split(':')[1])
        
        session = SessionLocal()
        try:
            user = session.query(User).filter(User.id == user_id).first()
            reputation = EnhancedReputationService.get_comprehensive_reputation(user_id, session)
            
            if not user or not reputation:
                await query.edit_message_text("❌ Unable to load history")
                return ConversationHandler.END
            
            # Get rating history by month
            ratings = session.query(Rating).filter(
                Rating.rated_id == user_id,
                Rating.category == 'seller'
            ).order_by(Rating.created_at).all()
            
            if not ratings:
                message = f"📈 {user.first_name or user.username} - Rating History\n\n📝 No rating history available"
            else:
                # Calculate monthly averages
                monthly_data = {}
                for rating in ratings:
                    month_key = rating.created_at.strftime('%Y-%m')
                    if month_key not in monthly_data:
                        monthly_data[month_key] = []
                    monthly_data[month_key].append(rating.rating)
                
                # Calculate trends
                monthly_averages = {
                    month: sum(ratings) / len(ratings) 
                    for month, ratings in monthly_data.items()
                }
                
                message = f"""📈 {user.first_name or user.username} - Rating History

📊 Overall Trend: {reputation.reputation_trend.title()}
⭐ Current Rating: {reputation.overall_rating:.2f}/5.0
📈 Improvement: {_calculate_improvement(monthly_averages)}

🗓️ Monthly Performance:
"""
                
                # Show last 6 months
                sorted_months = sorted(monthly_averages.keys())[-6:]
                for month in sorted_months:
                    avg = monthly_averages[month]
                    count = len(monthly_data[month])
                    stars = "⭐" * int(round(avg))
                    
                    month_name = datetime.strptime(month, '%Y-%m').strftime('%b %Y')
                    message += f"• {month_name}: {avg:.1f}/5.0 {stars} ({count} reviews)\n"
                
                # Add performance insights
                message += "\n🎯 Performance Insights:\n"
                if reputation.reputation_trend == 'improving':
                    message += "• 📈 Rating trend is improving\n"
                elif reputation.reputation_trend == 'declining':
                    message += "• 📉 Recent ratings have declined\n"
                else:
                    message += "• 📊 Consistent performance\n"
                
                # Recent activity
                recent_30_days = len([r for r in ratings if (datetime.utcnow() - r.created_at).days <= 30])
                message += f"• 🕒 {recent_30_days} ratings in last 30 days\n"
                
                # Best and worst months
                if len(monthly_averages) > 1:
                    best_month = max(monthly_averages, key=monthly_averages.get)
                    worst_month = min(monthly_averages, key=monthly_averages.get)
                    
                    best_month_name = datetime.strptime(best_month, '%Y-%m').strftime('%b %Y')
                    worst_month_name = datetime.strptime(worst_month, '%Y-%m').strftime('%b %Y')
                    
                    message += f"• 🌟 Best month: {best_month_name} ({monthly_averages[best_month]:.1f}/5.0)\n"
                    if monthly_averages[best_month] != monthly_averages[worst_month]:
                        message += f"• 📉 Lowest month: {worst_month_name} ({monthly_averages[worst_month]:.1f}/5.0)\n"
            
            keyboard = [
                [
                    InlineKeyboardButton("💬 Reviews", callback_data=f"seller_reviews:{user_id}"),
                    InlineKeyboardButton("📊 Statistics", callback_data=f"seller_stats:{user_id}")
                ],
                [
                    InlineKeyboardButton("🔙 Profile", callback_data=f"seller_profile:{user_id}"),
                    InlineKeyboardButton("🏠 Home", callback_data="main_menu")
                ]
            ]
            
            await safe_edit_message_text(
                query,
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Error displaying rating history: {e}")
        await query.edit_message_text("❌ Error loading history")
        return ConversationHandler.END
    
    return ConversationHandler.END


async def handle_seller_badges(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Seller badges and achievements
    Addresses Issue: #4 - Missing Rating-Based Features
    """
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🏆 Loading achievements")
    
    try:
        user_id = int(query.data.split(':')[1])
        
        session = SessionLocal()
        try:
            user = session.query(User).filter(User.id == user_id).first()
            reputation = EnhancedReputationService.get_comprehensive_reputation(user_id, session)
            
            if not user or not reputation:
                await query.edit_message_text("❌ Unable to load achievements")
                return ConversationHandler.END
            
            message = f"""🏆 {user.first_name or user.username} - Achievements

🎖️ Current Badges ({len(reputation.badges)})
"""
            
            if reputation.badges:
                for badge in reputation.badges:
                    message += f"• {badge}\n"
            else:
                message += "• No badges earned yet\n"
            
            message += f"\n🏅 Trust Level: {reputation.trust_level.title()}\n"
            
            # Show progress to next level
            trust_levels = EnhancedReputationService.TRUST_LEVELS
            current_level = reputation.trust_level
            
            # Find next level
            level_order = ['new', 'bronze', 'silver', 'gold', 'platinum', 'diamond']
            try:
                current_index = level_order.index(current_level)
                if current_index < len(level_order) - 1:
                    next_level = level_order[current_index + 1]
                    next_requirements = trust_levels[next_level]
                    
                    message += f"\n📈 Progress to {next_level.title()}:\n"
                    message += f"• Ratings: {reputation.total_ratings}/{next_requirements[0]} ✅\n" if reputation.total_ratings >= next_requirements[0] else f"• Ratings: {reputation.total_ratings}/{next_requirements[0]} ❌\n"
                    message += f"• Average: {reputation.overall_rating:.1f}/{next_requirements[1]} ✅\n" if reputation.overall_rating >= next_requirements[1] else f"• Average: {reputation.overall_rating:.1f}/{next_requirements[1]} ❌\n"
                    message += f"• Volume: ${float(reputation.total_volume):,.0f}/${next_requirements[2]} ✅\n" if float(reputation.total_volume) >= next_requirements[2] else f"• Volume: ${float(reputation.total_volume):,.0f}/${next_requirements[2]} ❌\n"
                else:
                    message += "\n🎯 Maximum level achieved!\n"
            except ValueError:
                pass
            
            # Achievement opportunities
            message += "\n🎯 Achievement Opportunities:\n"
            
            # Potential badges they can earn
            potential_badges = []
            
            if reputation.overall_rating >= 4.5 and reputation.total_ratings >= 5 and '⭐ Highly Rated' not in reputation.badges:
                potential_badges.append("⭐ Highly Rated (4.5+ avg, 5+ ratings)")
            
            if reputation.completion_rate >= 0.95 and reputation.total_ratings >= 5 and '✅ Reliable' not in reputation.badges:
                potential_badges.append("✅ Reliable (95%+ completion, 5+ ratings)")
            
            if float(reputation.total_volume) >= 1000 and '💰 Volume Trader' not in reputation.badges:
                potential_badges.append("💰 Volume Trader ($1,000+ USD volume)")
            
            if reputation.dispute_rate == 0 and reputation.total_ratings >= 10 and '🕊️ Dispute Free' not in reputation.badges:
                potential_badges.append("🕊️ Dispute Free (0% disputes, 10+ trades)")
            
            if potential_badges:
                for badge in potential_badges[:3]:  # Show top 3
                    message += f"• {badge}\n"
            else:
                message += "• Keep trading to unlock more achievements!\n"
            
            keyboard = [
                [
                    InlineKeyboardButton("📊 Statistics", callback_data=f"seller_stats:{user_id}"),
                    InlineKeyboardButton("📈 History", callback_data=f"seller_history:{user_id}")
                ],
                [
                    InlineKeyboardButton("🔙 Profile", callback_data=f"seller_profile:{user_id}"),
                    InlineKeyboardButton("🏠 Home", callback_data="main_menu")
                ]
            ]
            
            await safe_edit_message_text(
                query,
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Error displaying seller badges: {e}")
        await query.edit_message_text("❌ Error loading achievements")
        return ConversationHandler.END
    
    return ConversationHandler.END


async def handle_browse_sellers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Browse top-rated sellers
    Addresses Issue: #4 - Missing Rating-Based Features
    """
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🔍 Finding sellers")
    
    try:
        # Get top rated sellers
        top_sellers = EnhancedReputationService.search_sellers_by_rating(
            min_rating=4.0, min_ratings=3
        )
        
        if not top_sellers:
            message = "🔍 Top Rated Sellers\n\n📝 No qualified sellers found.\n\nSellers need 3+ ratings with 4.0+ average to appear here."
            keyboard = [
                [InlineKeyboardButton("🏠 Home", callback_data="main_menu")]
            ]
        else:
            message = f"🔍 Top Rated Sellers ({len(top_sellers)} found)\n\n"
            
            keyboard = []
            for i, seller in enumerate(top_sellers[:10], 1):  # Show top 10
                "⭐" * min(5, max(1, int(round(seller['rating']))))
                trust_emoji = {'diamond': '💎', 'platinum': '🏆', 'gold': '🥇', 'silver': '🥈', 'bronze': '🥉', 'new': '🆕'}.get(seller['trust_level'], '📊')
                
                message += f"{i}. {seller['display_name']}\n"
                message += f"⭐ {seller['rating']:.1f}/5.0 • {seller['total_ratings']} reviews\n"
                message += f"{trust_emoji} {seller['trust_level'].title()} • {seller['completion_rate']*100:.0f}% completion\n"
                
                if seller['badges']:
                    message += f"🏆 {', '.join(seller['badges'][:2])}\n"
                
                message += "\n"
                
                # Add button for this seller
                keyboard.append([
                    InlineKeyboardButton(
                        f"👤 {seller['display_name']} ({seller['rating']:.1f}⭐)",
                        callback_data=f"seller_profile:{seller['user_id']}"
                    )
                ])
            
            # Add filter options
            keyboard.extend([
                [
                    InlineKeyboardButton("🌟 4.5+ Stars Only", callback_data="browse_sellers_4_5"),
                    InlineKeyboardButton("🏆 Gold+ Only", callback_data="browse_sellers_gold")
                ],
                [
                    InlineKeyboardButton("🔍 Search by Username", callback_data="search_seller"),
                    InlineKeyboardButton("🏠 Home", callback_data="main_menu")
                ]
            ])
        
        if query:
            await safe_edit_message_text(
                query,
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        
    except Exception as e:
        logger.error(f"Error browsing sellers: {e}")
        if query:
            await query.edit_message_text("❌ Error loading sellers")
        return ConversationHandler.END
    
    return ConversationHandler.END


# Helper functions

async def _build_seller_profile_message(user: User, reputation: ReputationScore, session) -> str:
    """Build comprehensive seller profile message"""
    
    display_name = user.first_name or user.username or "Unknown"
    
    # Header with basic info
    message = f"👤 {display_name} - Seller Profile\n\n"
    
    # Reputation overview
    if reputation.total_ratings > 0:
        stars = "⭐" * min(5, max(1, int(round(reputation.overall_rating))))
        trust_emoji = {
            'diamond': '💎', 'platinum': '🏆', 'gold': '🥇', 
            'silver': '🥈', 'bronze': '🥉', 'new': '🆕'
        }.get(reputation.trust_level, '📊')
        
        message += f"⭐ {reputation.overall_rating:.1f}/5.0 ({reputation.total_ratings} reviews)\n"
        message += f"{trust_emoji} {reputation.trust_level.title()} Member\n"
        message += f"🎯 Trust Score: {reputation.trust_score}/100\n"
        message += f"✅ Completion Rate: {reputation.completion_rate*100:.1f}%\n"
        
        # Verification status
        if reputation.verification_status != 'unverified':
            message += f"🔐 {reputation.verification_status.replace('_', ' ').title()}\n"
        
        message += "\n"
        
        # Badges
        if reputation.badges:
            message += f"🏆 Achievements: {', '.join(reputation.badges[:3])}\n\n"
        
        # Recent activity
        if reputation.recent_activity > 0:
            message += f"📈 Recent Activity: {reputation.recent_activity} ratings (30 days)\n"
        
        # Trading volume
        if reputation.total_volume > 0:
            message += f"💰 Total Volume: ${float(reputation.total_volume):,.2f}\n"
        
        message += f"📅 Member Since: {user.created_at.strftime('%B %Y') if user.created_at else 'Unknown'}\n\n"
        
        # Risk assessment
        risk_colors = {'low': '🟢', 'medium': '🟡', 'high': '🔴'}
        risk_color = risk_colors.get(reputation.risk_level, '⚪')
        message += f"🛡️ Risk Level: {risk_color} {reputation.risk_level.title()}\n"
        
        # Trend
        trend_emoji = {'improving': '📈', 'declining': '📉', 'stable': '📊'}
        trend = trend_emoji.get(reputation.reputation_trend, '📊')
        message += f"{trend} Trend: {reputation.reputation_trend.title()}\n\n"
        
        # Recent reviews preview
        recent_ratings = session.query(Rating).filter(
            Rating.rated_id == user.id,
            Rating.category == 'seller'
        ).order_by(desc(Rating.created_at)).limit(3).all()
        
        if recent_ratings:
            message += "💬 Recent Reviews:\n"
            for rating in recent_ratings:
                rater = session.query(User).filter(User.id == rating.rater_id).first()
                rater_name = rater.first_name if rater else "Anonymous"
                stars = "⭐" * rating.rating
                days_ago = (datetime.utcnow() - rating.created_at).days
                
                message += f"• {stars} {rater_name} ({days_ago}d ago)\n"
                if rating.comment:
                    comment = rating.comment[:80] + "..." if len(rating.comment) > 80 else rating.comment
                    message += f"  💭 \"{comment}\"\n"
    else:
        message += "🆕 New Seller - No ratings yet\n\n"
        message += f"📅 Joined: {user.created_at.strftime('%B %Y') if user.created_at else 'Recently'}\n"
        message += "💡 Tip: Consider starting with smaller trades\n"
    
    return message


async def _get_detailed_trading_stats(user_id: int, session) -> Dict:
    """Get detailed trading statistics"""
    from sqlalchemy import or_
    
    # Get all completed escrows (both as buyer AND seller)
    completed_escrows = session.query(Escrow).filter(
        or_(Escrow.buyer_id == user_id, Escrow.seller_id == user_id),
        Escrow.status == EscrowStatus.COMPLETED.value
    ).all()
    
    # Get all escrows for completion rate (both as buyer AND seller)
    all_escrows = session.query(Escrow).filter(
        or_(Escrow.buyer_id == user_id, Escrow.seller_id == user_id)
    ).all()
    
    total_trades = len(all_escrows)
    completed_trades = len(completed_escrows)
    completion_rate = completed_trades / total_trades if total_trades > 0 else 0.0
    
    total_volume = sum(float(e.amount) for e in completed_escrows)
    avg_trade_size = total_volume / len(completed_escrows) if completed_escrows else 0.0
    
    return {
        'total_trades': total_trades,
        'completed_trades': completed_trades,
        'completion_rate': completion_rate,
        'total_volume': total_volume,
        'avg_trade_size': avg_trade_size
    }


def _calculate_improvement(monthly_averages: Dict[str, float]) -> str:
    """Calculate rating improvement over time"""
    if len(monthly_averages) < 2:
        return "Insufficient data"
    
    sorted_months = sorted(monthly_averages.keys())
    first_month = monthly_averages[sorted_months[0]]
    last_month = monthly_averages[sorted_months[-1]]
    
    improvement = last_month - first_month
    
    if improvement > 0.5:
        return f"+{improvement:.1f} points (Significant improvement)"
    elif improvement > 0.1:
        return f"+{improvement:.1f} points (Improving)"
    elif improvement < -0.5:
        return f"{improvement:.1f} points (Declining)"
    elif improvement < -0.1:
        return f"{improvement:.1f} points (Slight decline)"
    else:
        return "Stable performance"


# Callback handlers to register
SELLER_PROFILE_HANDLERS = [
    CallbackQueryHandler(handle_view_seller_profile, pattern="^seller_profile:"),
    CallbackQueryHandler(handle_seller_statistics, pattern="^seller_stats:"),
    CallbackQueryHandler(handle_seller_reviews, pattern="^seller_reviews:"),
    CallbackQueryHandler(handle_seller_rating_history, pattern="^seller_history:"),
    CallbackQueryHandler(handle_seller_badges, pattern="^seller_badges:"),
    CallbackQueryHandler(handle_browse_sellers, pattern="^browse_sellers")
]