"""
Rating UI Enhancements
User experience improvements, rating discovery, and educational features
Addresses Issues: #9, #14, #15, #16, #18, #19
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters

from database import SessionLocal
from models import User, Rating
from services.enhanced_reputation_service import EnhancedReputationService
from utils.callback_utils import safe_edit_message_text, safe_answer_callback_query
from utils.markdown_escaping import escape_markdown

logger = logging.getLogger(__name__)


async def handle_rating_discovery_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Main rating system discovery menu
    Addresses Issue: #14 - Poor Rating Discovery
    """
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "⭐ Rating system")
    
    try:
        message = """⭐ Rating & Trust System

🌟 Browse top-rated sellers
🏆 Trust levels & badges earned
📊 Fair rating guidelines
🛡️ Trade with confidence

✅ Escrow protected
✅ Reputation tracked
✅ Community trusted"""

        keyboard = [
            [
                InlineKeyboardButton("🔍 Browse Top Sellers", callback_data="browse_sellers"),
                InlineKeyboardButton("🏆 Trust Levels Guide", callback_data="trust_levels_guide")
            ],
            [
                InlineKeyboardButton("📊 Rating Guidelines", callback_data="rating_guidelines"),
                InlineKeyboardButton("📈 Platform Statistics", callback_data="platform_rating_stats")
            ],
            [
                InlineKeyboardButton("🔍 Search Seller", callback_data="search_seller_by_username"),
                InlineKeyboardButton("❓ Rating FAQ", callback_data="rating_faq")
            ],
            [
                InlineKeyboardButton("🔙 Back to Menu", callback_data="main_menu")
            ]
        ]
        
        if query:
            await safe_edit_message_text(
                query,
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text(
                message,
                parse_mode="Markdown", 
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    except Exception as e:
        logger.error(f"Error showing rating discovery menu: {e}")
        if query:
            await query.edit_message_text("❌ Error loading rating system menu")
    
    return 0


async def handle_trust_levels_guide(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Trust levels and badges explanation
    Addresses Issue: #16 - Missing Rating Policies
    """
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🏆 Trust guide")
    
    try:
        message = """🏆 Trust Levels

🆕 New: 0-2 ratings
🥉 Bronze: 3+ • 4.0+ • $100+
🥈 Silver: 10+ • 4.2+ • $500+
🥇 Gold: 25+ • 4.5+ • $2K+
🏆 Platinum: 50+ • 4.7+ • $10K+
💎 Diamond: 100+ • 4.8+ • $50K+

🏅 Badges:
⭐ Highly Rated • ✅ Reliable
💰 Volume Trader • 🕊️ Dispute Free
🔐 Verified • 🏆 Veteran

✅ Recent trades weighted higher
✅ Trade volume matters
✅ Completion rate tracked"""

        keyboard = [
            [
                InlineKeyboardButton("🔍 Browse by Trust Level", callback_data="browse_by_trust_level"),
                InlineKeyboardButton("🏆 Top Achievers", callback_data="top_achievers")
            ],
            [
                InlineKeyboardButton("📊 My Rating Progress", callback_data="my_rating_progress"),
                InlineKeyboardButton("🔙 Back", callback_data="rating_discovery")
            ]
        ]
        
        await safe_edit_message_text(
            query,
            message,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    except Exception as e:
        logger.error(f"Error showing trust levels guide: {e}")
        await query.edit_message_text("❌ Error loading trust levels guide")
    
    return 0


async def handle_rating_guidelines(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Rating guidelines and policies
    Addresses Issue: #16 - Missing Rating Policies
    """
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📊 Guidelines")
    
    try:
        message = """📊 Rating Guidelines

⭐ Scale:
5⭐ Excellent • 4⭐ Good • 3⭐ Average • 2⭐ Below avg • 1⭐ Poor

✅ Rate on: Communication • Reliability • Professionalism • Problem resolution

🚫 No personal attacks, fake ratings, or discriminatory content

💡 Be specific, factual & professional"""

        keyboard = [
            [
                InlineKeyboardButton("📤 Report Rating Issue", callback_data="report_rating_issue"),
                InlineKeyboardButton("❓ Rating FAQ", callback_data="rating_faq")
            ],
            [
                InlineKeyboardButton("📞 Contact Support", callback_data="contact_support"),
                InlineKeyboardButton("🔙 Back", callback_data="rating_discovery")
            ]
        ]
        
        await safe_edit_message_text(
            query,
            message,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    except Exception as e:
        logger.error(f"Error showing rating guidelines: {e}")
        await query.edit_message_text("❌ Error loading rating guidelines")
    
    return 0


async def handle_platform_rating_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Platform-wide rating statistics
    Addresses Issue: #18 - Reduced Platform Trust
    """
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📈 Statistics")
    
    try:
        session = SessionLocal()
        try:
            # Get platform statistics
            analytics = EnhancedReputationService.get_rating_analytics(30, session)
            
            if not analytics:
                await query.edit_message_text("❌ Unable to load statistics")
                return 0
            
            # Calculate additional stats
            total_users = session.query(User).count()
            rated_users = session.query(Rating.rated_id).distinct().count()
            
            # Rating distribution summary
            total = analytics.get('total_ratings', 0)
            dist = analytics.get('rating_distribution', {})
            stars_5 = dist.get(5, 0)
            stars_4 = dist.get(4, 0)
            
            message = f"""📈 Platform Statistics

📊 {analytics.get('total_ratings', 0):,} ratings • {analytics.get('overall_average', 0):.1f}/5.0 avg
👥 {rated_users:,} of {total_users:,} users rated

⭐ Distribution:
⭐⭐⭐⭐⭐ {stars_5} ({stars_5 / max(1, total) * 100:.0f}%) 
⭐⭐⭐⭐ {stars_4} ({stars_4 / max(1, total) * 100:.0f}%)"""
            
            # Add top 3 users only if available
            top_users = analytics.get('top_users', [])
            if top_users:
                message += "\n\n🏆 Top Sellers:"
                for i, user_data in enumerate(top_users[:3], 1):
                    user = session.query(User).filter(User.id == user_data['user_id']).first()
                    display_name = user.first_name or user.username or f"User {user.id}" if user else "Unknown"
                    message += f"\n{i}. {escape_markdown(display_name)} {user_data['avg_rating']:.1f}⭐"
            
            message += "\n\n✅ Real-time updates"

        finally:
            session.close()
        
        keyboard = [
            [
                InlineKeyboardButton("🔍 Browse Top Sellers", callback_data="browse_sellers"),
                InlineKeyboardButton("🏆 Trust Levels", callback_data="trust_levels_guide")
            ],
            [
                InlineKeyboardButton("🔙 Back", callback_data="rating_discovery")
            ]
        ]
        
        await safe_edit_message_text(
            query,
            message,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    except Exception as e:
        logger.error(f"Error showing platform statistics: {e}")
        await query.edit_message_text("❌ Error loading statistics")
    
    return 0


async def handle_my_rating_progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Show user's own rating progress and achievements
    Addresses Issue: #15 - Inadequate Rating Feedback
    """
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "📊 My progress")
    
    try:
        session = SessionLocal()
        try:
            # Get current user
            user = session.query(User).filter(
                User.telegram_id == int(update.effective_user.id)
            ).first()
            
            if not user:
                await query.edit_message_text("❌ User not found")
                return 0
            
            # Get user's reputation
            reputation = EnhancedReputationService.get_comprehensive_reputation(user.id, session)
            
            if not reputation:
                message = """📊 Your Rating Progress

🆕 No ratings yet

Build reputation:
✅ Complete trades successfully
✅ Communicate clearly
✅ Meet commitments

🏆 Unlock badges:
⭐ First Rating
✅ Reliable (95%+)
💰 Volume Trader ($1K+)
🔐 Verified

💡 Stay responsive
💡 Follow through
💡 Ask for ratings"""
            else:
                trust_levels = EnhancedReputationService.TRUST_LEVELS
                current_level = reputation.trust_level
                
                # Find next level
                level_order = ['new', 'bronze', 'silver', 'gold', 'platinum', 'diamond']
                try:
                    current_index = level_order.index(current_level)
                    next_level = level_order[current_index + 1] if current_index < len(level_order) - 1 else None
                except ValueError:
                    next_level = None
                
                badges_text = " • ".join(reputation.badges[:3]) if reputation.badges else "None yet"
                
                message = f"""📊 Your Rating Progress

🏅 {reputation.overall_rating:.1f}/5.0 ⭐ ({reputation.total_ratings} reviews)
🏆 {reputation.trust_level.title()} • {reputation.trust_score}/100
✅ {reputation.completion_rate*100:.0f}% completion

🏅 Badges: {badges_text}"""
                
                if next_level:
                    next_requirements = trust_levels[next_level]
                    message += f"\n\n📈 Next: {next_level.title()}"
                    message += f"\nRatings: {reputation.total_ratings}/{next_requirements[0]} {'✅' if reputation.total_ratings >= next_requirements[0] else '❌'}"
                    message += f"\nAvg: {reputation.overall_rating:.1f}/{next_requirements[1]} {'✅' if reputation.overall_rating >= next_requirements[1] else '❌'}"
                    message += f"\nVolume: ${float(reputation.total_volume):,.0f}/${next_requirements[2]:,} {'✅' if float(reputation.total_volume) >= next_requirements[2] else '❌'}"
                else:
                    message += "\n\n🎯 Max level achieved!"
                
                message += f"\n\n💼 ${float(reputation.total_volume):,.0f} volume"
                message += f"\n🛡️ {reputation.dispute_rate*100:.0f}% disputes"
        
        finally:
            session.close()
        
        keyboard = [
            [
                InlineKeyboardButton("📊 View My Reviews", callback_data="my_reviews"),
                InlineKeyboardButton("🏆 All Achievements", callback_data="all_achievements")
            ],
            [
                InlineKeyboardButton("💡 Improvement Tips", callback_data="improvement_tips"),
                InlineKeyboardButton("🔙 Back", callback_data="trust_levels_guide")
            ]
        ]
        
        await safe_edit_message_text(
            query,
            message,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    except Exception as e:
        logger.error(f"Error showing rating progress: {e}")
        await query.edit_message_text("❌ Error loading rating progress")
    
    return 0


async def handle_rating_faq(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Rating system FAQ
    Addresses Issue: #15 - Inadequate Rating Feedback
    """
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "❓ FAQ")
    
    try:
        message = """❓ Rating FAQ

**When can I rate?**
After completing escrow trades

**Can I change ratings?**
No, ratings are permanent

**Unfair rating?**
Contact support with evidence

**Trust levels?**
Based on count, average & volume

**Rating calculation?**
Recent trades weighted higher

**Cancelled trades?**
No ratings for cancelled trades

**Improve rating?**
Communicate well • Be reliable • Meet deadlines

**Anonymous ratings?**
No, first names shown"""

        keyboard = [
            [
                InlineKeyboardButton("📞 Contact Support", callback_data="contact_support"),
                InlineKeyboardButton("📊 Rating Guidelines", callback_data="rating_guidelines")
            ],
            [
                InlineKeyboardButton("🔙 Back", callback_data="rating_discovery")
            ]
        ]
        
        await safe_edit_message_text(
            query,
            message,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    except Exception as e:
        logger.error(f"Error showing rating FAQ: {e}")
        await query.edit_message_text("❌ Error loading FAQ")
    
    return 0


async def handle_search_seller_by_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Search for seller by username/email
    Addresses Issue: #4 - Missing Rating-Based Features
    """
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🔍 Search")
    
    try:
        # Set conversation state in database for persistence
        session = SessionLocal()
        try:
            user = session.query(User).filter(
                User.telegram_id == int(update.effective_user.id)
            ).first()
            if user:
                user.conversation_state = 'rating_search'
                session.commit()
                # PERFORMANCE: Invalidate RouteGuard cache when conversation_state changes
                from utils.route_guard import RouteGuard
                RouteGuard.invalidate_conversation_cache(update.effective_user.id)
        finally:
            session.close()
        
        message = """🔍 Search for Seller

Enter username or email:
• @username
• user@example.com

You'll see ratings, trust level, badges & trading stats.

Type below:"""
        
        keyboard = [
            [InlineKeyboardButton("🔙 Cancel", callback_data="rating_discovery")]
        ]
        
        await safe_edit_message_text(
            query,
            message,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    except Exception as e:
        logger.error(f"Error starting seller search: {e}")
        await query.edit_message_text("❌ Error starting search")
    
    return 0


async def handle_seller_search_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Handle seller search input
    Addresses Issue: #4 - Missing Rating-Based Features
    """
    if not update.message or not update.message.text:
        return 0
    
    # Check if user is in search mode (from database state)
    session = SessionLocal()
    try:
        user = session.query(User).filter(
            User.telegram_id == int(update.effective_user.id)
        ).first()
        
        if not user or user.conversation_state != 'rating_search':
            return 0
        
        # Clear search state
        user.conversation_state = ''
        session.commit()
        # PERFORMANCE: Invalidate RouteGuard cache when conversation_state changes
        from utils.route_guard import RouteGuard
        RouteGuard.invalidate_conversation_cache(update.effective_user.id)
    finally:
        session.close()
    
    try:
        search_term = update.message.text.strip()
        
        # Determine search type
        seller_type = None
        if search_term.startswith('@'):
            seller_type = 'username'
            search_term = search_term[1:]  # Remove @ symbol
        elif '@' in search_term:
            seller_type = 'email'
        else:
            seller_type = 'username'
        
        # Get seller profile
        seller_profile = EnhancedReputationService.get_seller_profile_for_escrow(
            search_term, seller_type
        )
        
        if not seller_profile:
            message = f"""🔍 Search Results

❌ Seller not found: {escape_markdown(search_term)}

This seller either:
• Doesn't exist on the platform
• Has a different username/email
• Hasn't completed any trades yet

**Try:**
• Double-check the spelling
• Search for their email instead
• Ask them for their exact username

Or browse our top-rated sellers instead."""
            
            keyboard = [
                [
                    InlineKeyboardButton("🔍 Browse Top Sellers", callback_data="browse_sellers"),
                    InlineKeyboardButton("🔄 Search Again", callback_data="search_seller_by_username")
                ],
                [
                    InlineKeyboardButton("🔙 Back", callback_data="rating_discovery")
                ]
            ]
        else:
            # Show seller profile
            reputation = seller_profile.reputation_score
            
            message = f"""🔍 **Search Results**

👤 **{escape_markdown(seller_profile.display_name)}**

⭐ **{reputation.overall_rating:.1f}/5.0** ({reputation.total_ratings} reviews)
🏅 **{reputation.trust_level.title()}** member
✅ **{reputation.completion_rate*100:.1f}%** completion rate"""

            if reputation.badges:
                message += f"\n🏆 {', '.join(reputation.badges[:3])}"
            
            if seller_profile.trust_indicators:
                message += "\n\n✅ **Trust Indicators:**"
                for indicator in seller_profile.trust_indicators[:3]:
                    message += f"\n• {escape_markdown(indicator)}"
            
            if seller_profile.warnings:
                message += "\n\n⚠️ **Considerations:**"
                for warning in seller_profile.warnings[:2]:
                    message += f"\n• {escape_markdown(warning)}"
            
            keyboard = [
                [
                    InlineKeyboardButton("👤 View Full Profile", callback_data=f"seller_profile:{seller_profile.user_id}"),
                    InlineKeyboardButton("🛡️ Start Trade", callback_data=f"create_escrow_with:{seller_profile.user_id}")
                ],
                [
                    InlineKeyboardButton("🔄 Search Again", callback_data="search_seller_by_username"),
                    InlineKeyboardButton("🔙 Back", callback_data="rating_discovery")
                ]
            ]
        
        await update.message.reply_text(
            message,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    except Exception as e:
        logger.error(f"Error handling seller search: {e}")
        await update.message.reply_text(
            "❌ Error searching for seller. Please try again.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data="rating_discovery")]
            ])
        )
    
    return 0


# Handler list for registration
RATING_UI_HANDLERS = [
    CallbackQueryHandler(handle_rating_discovery_menu, pattern="^rating_discovery$"),
    CallbackQueryHandler(handle_trust_levels_guide, pattern="^trust_levels_guide$"),
    CallbackQueryHandler(handle_rating_guidelines, pattern="^rating_guidelines$"),
    CallbackQueryHandler(handle_platform_rating_stats, pattern="^platform_rating_stats$"),
    CallbackQueryHandler(handle_my_rating_progress, pattern="^my_rating_progress$"),
    CallbackQueryHandler(handle_rating_faq, pattern="^rating_faq$"),
    CallbackQueryHandler(handle_search_seller_by_username, pattern="^search_seller_by_username$"),
    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_seller_search_input),
]