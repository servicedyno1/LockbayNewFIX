"""Referral system handlers"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select

from models import User
from database import SessionLocal, AsyncSessionLocal
from utils.referral import ReferralSystem
from utils.callback_utils import safe_answer_callback_query, safe_edit_message_text
from utils.referral_prefetch import (
    prefetch_referral_context,
    get_cached_referral_data,
    cache_referral_data
)

logger = logging.getLogger(__name__)

async def handle_invite_friends(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle invite friends button press with optimized prefetch (7→2 queries, 71% reduction)"""
    query = update.callback_query
    if not query or not update.effective_user:
        return

    # SINGLE CALLBACK ANSWER: Invite friends
    await safe_answer_callback_query(query, "🎁")

    async with AsyncSessionLocal() as session:
        try:
            # Get user from database
            result = await session.execute(
                select(User).filter(User.telegram_id == int(update.effective_user.id))
            )
            user = result.scalar_one_or_none()

            if not user:
                await safe_edit_message_text(
                    query, "❌ User not found. Please start with /start"
                )
                return

            # OPTIMIZATION: Prefetch all referral data in 2 queries instead of 7+
            prefetch_data = await prefetch_referral_context(user.id, session)
            if prefetch_data:
                cache_referral_data(context.user_data, prefetch_data)

            # Store user.id for potential sync fallback
            user_id = user.id

        except Exception as e:
            logger.error(f"Error in invite friends handler: {e}")
            await safe_edit_message_text(
                query, "❌ Error loading referral information. Please try again."
            )
            return

    # Use cached data (from prefetch or previous cache)
    cached = get_cached_referral_data(context.user_data)
    
    try:
        if cached:
            # Use prefetch data
            referral_code = cached['referral_code']
            total_referrals = cached['total_referrals']
            active_referrals = cached['active_referrals']
            total_earned = float(cached['total_rewards_earned'])
            
            # Build referral link
            from config import Config
            referral_link = f"https://t.me/{Config.BOT_USERNAME}?start=ref_{referral_code}"
        else:
            # FALLBACK: Use sync session (CRITICAL FIX for sync/async mismatch)
            # ReferralSystem.get_referral_stats expects synchronous Session
            with SessionLocal() as sync_session:
                # Re-fetch user in sync session
                sync_user = sync_session.query(User).filter(User.id == user_id).first()
                if not sync_user:
                    await safe_edit_message_text(
                        query, "❌ User not found. Please start with /start"
                    )
                    return
                
                stats = ReferralSystem.get_referral_stats(sync_user, sync_session)
                referral_code = stats['referral_code']
                total_referrals = stats['total_referrals']
                active_referrals = stats['active_referrals']
                total_earned = stats['total_earned']
                referral_link = stats['referral_link']

        # Get public profile URL
        from utils.helpers import get_public_profile_url
        # Re-fetch user for profile URL in sync session
        with SessionLocal() as sync_session:
            sync_user = sync_session.query(User).filter(User.id == user_id).first()
            if sync_user:
                profile_url = get_public_profile_url(sync_user)
            else:
                profile_url = f"@{update.effective_user.username}" if update.effective_user.username else "User"

        # Create compact mobile-friendly sharing message
        # Format currency amounts without unnecessary decimals
        def format_currency(amount):
            return f"${amount:g}"
        
        referral_text = f"""🎁 <b>Invite & Earn</b>

<code>{referral_code}</code>

💰 <b>Rewards</b>
Friends: {format_currency(ReferralSystem.REFEREE_REWARD_USD)} instant • You: {format_currency(ReferralSystem.REFERRER_REWARD_USD)} (when they trade {format_currency(ReferralSystem.MIN_ACTIVITY_FOR_REWARD)}+)

📊 <b>Stats:</b> {total_referrals} invited • {active_referrals} active • ${total_earned:.2f} earned

🔗 <b>Share:</b> {referral_link}

👤 <b>Your Profile:</b> {profile_url}"""

        keyboard = [
            [
                InlineKeyboardButton(
                    "🔗 Share Link",
                    url=f"https://t.me/share/url?url={referral_link}&text=Join me on this secure crypto trading platform! Get ${ReferralSystem.REFEREE_REWARD_USD:g} welcome bonus!",
                )
            ],
            [
                InlineKeyboardButton(
                    "🏆 Leaderboard", callback_data="referral_leaderboard"
                ),
                InlineKeyboardButton("📈 My Stats", callback_data="referral_stats"),
            ],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ]

        await safe_edit_message_text(
            query, referral_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML"
        )

    except Exception as e:
        logger.error(f"Error in invite friends handler message building: {e}")
        await safe_edit_message_text(
            query, "❌ Error loading referral information. Please try again."
        )

async def handle_referral_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show detailed referral statistics with optimized caching (reuses prefetch data)"""
    query = update.callback_query
    if not query or not update.effective_user:
        return

    await safe_answer_callback_query(query, "📈")

    # Try to use cached data first (from handle_invite_friends prefetch)
    cached = get_cached_referral_data(context.user_data)
    
    if not cached:
        # No cache - fetch fresh data with prefetch
        user_id = None
        async with AsyncSessionLocal() as session:
            try:
                result = await session.execute(
                    select(User).filter(User.telegram_id == int(update.effective_user.id))
                )
                user = result.scalar_one_or_none()

                if not user:
                    await safe_edit_message_text(query, "❌ User not found.")
                    return

                user_id = user.id

                # Prefetch and cache data
                prefetch_data = await prefetch_referral_context(user.id, session)
                if prefetch_data:
                    cache_referral_data(context.user_data, prefetch_data)
                    cached = get_cached_referral_data(context.user_data)
                    
            except Exception as e:
                logger.error(f"Error fetching referral stats: {e}")
                await safe_edit_message_text(query, "❌ Error loading statistics.")
                return
        
        # If prefetch failed, use sync fallback
        if not cached and user_id:
            try:
                # FALLBACK: Use sync session (CRITICAL FIX for sync/async mismatch)
                # ReferralSystem.get_referral_stats expects synchronous Session
                with SessionLocal() as sync_session:
                    sync_user = sync_session.query(User).filter(User.id == user_id).first()
                    if not sync_user:
                        await safe_edit_message_text(query, "❌ User not found.")
                        return
                    
                    # Get stats and manually create cached dict
                    stats = ReferralSystem.get_referral_stats(sync_user, sync_session)
                    # Create a minimal cached dict for consistency
                    cached = {
                        'referral_code': stats['referral_code'],
                        'total_referrals': stats['total_referrals'],
                        'active_referrals': stats['active_referrals'],
                        'total_rewards_earned': stats['total_earned'],
                        'recent_referrals': []  # Stats might not have this, provide empty
                    }
            except Exception as e:
                logger.error(f"Error in sync fallback for referral stats: {e}")
                await safe_edit_message_text(query, "❌ Error loading statistics.")
                return

    # Use cached data to build stats
    if not cached:
        await safe_edit_message_text(query, "❌ Error loading statistics.")
        return
    
    try:
        referral_code = cached['referral_code']
        total_referrals = cached['total_referrals']
        active_referrals = cached['active_referrals']
        pending_referrals = total_referrals - active_referrals
        total_earned = float(cached['total_rewards_earned'])
        recent_referrals = cached.get('recent_referrals', [])

        # Format currency amounts without unnecessary decimals
        def format_currency(amount):
            return f"${amount:g}"

        # Build detailed stats message
        stats_text = f"""📈 Your Referral Performance

🎯 Overview:
• Referral Code: {referral_code}
• Total Invites: {total_referrals}
• Active Referrals: {active_referrals}
• Pending: {pending_referrals}
• Total Earned: ${total_earned:.2f}

💡 How it works:
• Friends get {format_currency(ReferralSystem.REFEREE_REWARD_USD)} instantly
• You earn {format_currency(ReferralSystem.REFERRER_REWARD_USD)} when they trade {format_currency(ReferralSystem.MIN_ACTIVITY_FOR_REWARD)}+
• No limit on earnings!"""

        # Add recent referrals if any
        if recent_referrals:
            stats_text += "\n\n🏆 Active Referrals:"
            for referral in recent_referrals[:5]:  # Show top 5
                name = referral.get('first_name') or referral.get('username') or "User"
                total_trades = referral.get('total_trades', 0)
                stats_text += f"\n• {name}: {total_trades} trade{'s' if total_trades != 1 else ''}"

        keyboard = [
            [
                InlineKeyboardButton("🔗 Share Link", callback_data="invite_friends"),
                InlineKeyboardButton(
                    "🏆 Leaderboard", callback_data="referral_leaderboard"
                ),
            ],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ]

        await safe_edit_message_text(
            query, stats_text, reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error rendering referral stats: {e}")
        await safe_edit_message_text(query, "❌ Error loading statistics.")

async def handle_referral_leaderboard(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show referral leaderboard"""
    query = update.callback_query
    if not query:
        return

    await safe_answer_callback_query(query, "🏆")

    session = SessionLocal()
    try:
        leaderboard = ReferralSystem.get_referral_leaderboard(limit=10, session=session)

        if not leaderboard:
            leaderboard_text = """🏆 Referral Leaderboard

No active referrers yet! Be the first to invite friends and earn rewards!"""
        else:
            leaderboard_text = """🏆 Top Referrers

"""
            for entry in leaderboard:
                rank_emoji = (
                    "🥇"
                    if entry["rank"] == 1
                    else (
                        "🥈"
                        if entry["rank"] == 2
                        else "🥉" if entry["rank"] == 3 else f"{entry['rank']}."
                    )
                )
                leaderboard_text += f"{rank_emoji} {entry['display_name']}\n"
                leaderboard_text += f"   {entry['active_referrals']} active • ${entry['total_earned']:.0f} earned\n\n"

        keyboard = [
            [
                InlineKeyboardButton("🔗 Share Link", callback_data="invite_friends"),
                InlineKeyboardButton("📈 My Stats", callback_data="referral_stats"),
            ],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")],
        ]

        await safe_edit_message_text(
            query, leaderboard_text, reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Error in referral leaderboard handler: {e}")
        await safe_edit_message_text(query, "❌ Error loading leaderboard.")
    finally:
        session.close()

async def process_referral_from_start(telegram_id: str, referral_code: str) -> dict:
    """
    Process referral when user starts bot with referral code
    
    NOTE: After calling this function, the caller should invalidate referral cache
    for both the referrer and referee to ensure stats are updated:
    - invalidate_referral_cache(referrer_context.user_data)
    - invalidate_referral_cache(referee_context.user_data)
    """
    from database import AsyncSessionLocal
    from sqlalchemy import select
    
    session = AsyncSessionLocal()
    try:
        # Find or create user
        result_query = await session.execute(select(User).filter(User.telegram_id == telegram_id))
        user = result_query.scalar_one_or_none()

        if not user:
            # This will be called after user creation in the start handler
            return {"success": False, "error": "User not found"}

        # Check if user already has a referrer
        if user.referred_by_id:
            return {"success": False, "error": "User already has a referrer"}

        # Process the referral
        result = await ReferralSystem.process_referral_signup(user, referral_code, session)
        
        # NOTE: Cache invalidation should be done by caller with access to context
        # invalidate_referral_cache(context.user_data) for both users

        return result

    except Exception as e:
        logger.error(f"Error processing referral from start: {e}")
        return {"success": False, "error": str(e)}
    finally:
        await session.close()
