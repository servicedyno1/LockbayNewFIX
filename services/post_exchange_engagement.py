"""
Post Exchange Engagement Service
Comprehensive retention and engagement system for ExchangeOrder completion
Mirrors successful post-escrow engagement patterns
"""

import logging
from datetime import datetime, timedelta
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from models import ExchangeOrder, User
from database import SessionLocal
from config import Config

logger = logging.getLogger(__name__)


class PostExchangeEngagementService:
    """Service for post-exchange user engagement and retention"""

    # Competitor fee percentages for savings calculation
    TRADITIONAL_EXCHANGE_FEE_RATE = 0.08  # 8% typical competitor fee
    TRADITIONAL_BANK_FEE_RATE = 0.05     # 5% typical bank transfer fee
    MIN_COMPETITOR_FEE = 2.00            # Minimum $2 competitor fee

    @staticmethod
    async def send_immediate_completion_followup(
        exchange_id: str, user_id: int, context: ContextTypes.DEFAULT_TYPE
    ):
        """Send immediate post-completion engagement (5 minutes after completion)"""
        try:
            session = SessionLocal()
            try:
                exchange = (
                    session.query(ExchangeOrder)
                    .filter(ExchangeOrder.exchange_id == exchange_id)
                    .first()
                )
                if not exchange or getattr(exchange, "status", "") != "completed":
                    return  # Exchange not completed, skip notification

                # Calculate savings compared to competitors
                source_amount = float(getattr(exchange, "from_amount", 0))
                target_amount = float(getattr(exchange, "to_amount", 0))
                exchange_type = getattr(exchange, "exchange_type", "")
                
                savings_info = PostExchangeEngagementService._calculate_savings(
                    source_amount, target_amount, exchange_type
                )

                # Format currency display
                if exchange_type == "crypto_to_ngn":
                    getattr(exchange, "from_currency", "CRYPTO")
                    amount_display = f"₦{target_amount:,.0f}"
                else:
                    to_currency = getattr(exchange, "to_currency", "CRYPTO")
                    amount_display = f"{target_amount:.6f} {to_currency}"

                followup_text = f"🎉 Exchange Complete!\n\n{amount_display} delivered\n💰 Saved {savings_info['currency']}{savings_info['amount']:,.0f} vs competitors\n\nRate your experience:"

                keyboard = [
                    [
                        InlineKeyboardButton("⭐", callback_data=f"rate_exchange_{exchange_id}_1"),
                        InlineKeyboardButton("⭐⭐", callback_data=f"rate_exchange_{exchange_id}_2"),
                        InlineKeyboardButton("⭐⭐⭐", callback_data=f"rate_exchange_{exchange_id}_3"),
                        InlineKeyboardButton("⭐⭐⭐⭐", callback_data=f"rate_exchange_{exchange_id}_4"),
                        InlineKeyboardButton("⭐⭐⭐⭐⭐", callback_data=f"rate_exchange_{exchange_id}_5"),
                    ],
                    [
                        InlineKeyboardButton(
                            "💰 View Savings", callback_data=f"view_savings_{exchange_id}"
                        ),
                        InlineKeyboardButton(
                            "🔄 Quick Exchange", callback_data="start_exchange"
                        ),
                    ],
                ]

                await context.bot.send_message(
                    chat_id=user_id,
                    text=followup_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )

                logger.info(f"Immediate completion follow-up sent for exchange {exchange_id}")

            finally:
                session.close()

        except Exception as e:
            logger.error(f"Error sending immediate completion follow-up: {e}")

    @staticmethod
    async def send_achievement_followup(
        exchange_id: str, user_id: int, context: ContextTypes.DEFAULT_TYPE
    ):
        """Send achievement and retention follow-up (30 minutes after completion)"""
        try:
            session = SessionLocal()
            try:
                user = session.query(User).filter(User.id == user_id).first()
                if not user:
                    return

                # Count user's completed exchanges
                exchange_count = (
                    session.query(ExchangeOrder)
                    .filter(
                        ExchangeOrder.user_id == user_id,
                        ExchangeOrder.status == "completed"
                    )
                    .count()
                )

                # Calculate total savings
                exchanges = (
                    session.query(ExchangeOrder)
                    .filter(
                        ExchangeOrder.user_id == user_id,
                        ExchangeOrder.status == "completed"
                    )
                    .all()
                )

                total_savings = 0
                for ex in exchanges:
                    source_amount = float(getattr(ex, "from_amount", 0))
                    target_amount = float(getattr(ex, "to_amount", 0))
                    exchange_type = getattr(ex, "exchange_type", "")
                    savings = PostExchangeEngagementService._calculate_savings(
                        source_amount, target_amount, exchange_type
                    )
                    total_savings += savings["amount"]

                # Determine achievement level
                achievement_info = PostExchangeEngagementService._get_achievement_level(exchange_count)

                followup_text = f"""🏆 Achievement Unlocked!

{achievement_info['emoji']} {achievement_info['title']} - {exchange_count} successful exchange{"s" if exchange_count != 1 else ""}!
💰 Platform savings: ₦{total_savings:,.0f} vs competitors

{achievement_info['message']}

Keep going to unlock:
{PostExchangeEngagementService._get_next_achievements(exchange_count)}

Ready for your next exchange?"""

                keyboard = [
                    [
                        InlineKeyboardButton(
                            "🏆 View All Achievements", callback_data=f"view_achievements_{user_id}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🔄 Quick Exchange", callback_data="start_exchange"
                        ),
                        InlineKeyboardButton(
                            "📊 My Stats", callback_data=f"view_exchange_stats_{user_id}"
                        ),
                    ],
                ]

                await context.bot.send_message(
                    chat_id=user_id,
                    text=followup_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )

                logger.info(f"Achievement follow-up sent for exchange {exchange_id}")

            finally:
                session.close()

        except Exception as e:
            logger.error(f"Error sending achievement follow-up: {e}")

    @staticmethod
    async def send_educational_followup(
        exchange_id: str, user_id: int, context: ContextTypes.DEFAULT_TYPE
    ):
        """Send educational follow-up (24 hours after completion)"""
        try:
            session = SessionLocal()
            try:
                exchange = (
                    session.query(ExchangeOrder)
                    .filter(ExchangeOrder.exchange_id == exchange_id)
                    .first()
                )
                if not exchange:
                    return

                target_amount = float(getattr(exchange, "to_amount", 0))
                exchange_type = getattr(exchange, "exchange_type", "")

                if exchange_type == "crypto_to_ngn":
                    amount_display = f"₦{target_amount:,.0f}"
                else:
                    to_currency = getattr(exchange, "to_currency", "CRYPTO")
                    amount_display = f"{target_amount:.6f} {to_currency}"

                followup_text = f"""💡 Exchange Tips

Yesterday's exchange: {amount_display} ✅

Pro tip: Exchange rates change frequently throughout the day.
Our {Config.RATE_LOCK_DURATION_MINUTES}-minute rate lock protects you from price swings!

Did you know?
• Best rates are typically between 8AM-6PM UTC
• Rate volatility is highest during market opens
• Our markup is {Config.EXCHANGE_MARKUP_PERCENTAGE}% - industry leading!

Ready for your next exchange?"""

                keyboard = [
                    [
                        InlineKeyboardButton(
                            "🔄 Quick Exchange", callback_data="start_exchange"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🔔 Set Rate Alert", callback_data=f"set_rate_alert_{user_id}"
                        ),
                        InlineKeyboardButton(
                            "💡 More Tips", callback_data="exchange_help"
                        ),
                    ],
                ]

                await context.bot.send_message(
                    chat_id=user_id,
                    text=followup_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )

                logger.info(f"Educational follow-up sent for exchange {exchange_id}")

            finally:
                session.close()

        except Exception as e:
            logger.error(f"Error sending educational follow-up: {e}")

    @staticmethod
    async def send_weekly_milestone_report(
        user_id: int, context: ContextTypes.DEFAULT_TYPE
    ):
        """Send weekly milestone and impact report"""
        try:
            session = SessionLocal()
            try:
                # Get user's exchanges from last 7 days
                week_ago = datetime.utcnow() - timedelta(days=7)
                weekly_exchanges = (
                    session.query(ExchangeOrder)
                    .filter(
                        ExchangeOrder.user_id == user_id,
                        ExchangeOrder.status == "completed",
                        ExchangeOrder.completed_at >= week_ago
                    )
                    .all()
                )

                if not weekly_exchanges:
                    return  # No recent activity

                # Calculate weekly stats
                weekly_count = len(weekly_exchanges)
                total_savings = 0
                total_completion_times = []

                for ex in weekly_exchanges:
                    # Calculate savings
                    source_amount = float(getattr(ex, "from_amount", 0))
                    target_amount = float(getattr(ex, "to_amount", 0))
                    exchange_type = getattr(ex, "exchange_type", "")
                    savings = PostExchangeEngagementService._calculate_savings(
                        source_amount, target_amount, exchange_type
                    )
                    total_savings += savings["amount"]

                    # Calculate completion time (created to completed)
                    created_at = getattr(ex, "created_at", None)
                    completed_at = getattr(ex, "completed_at", None)
                    if created_at and completed_at:
                        completion_time = (completed_at - created_at).total_seconds() / 60
                        total_completion_times.append(completion_time)

                avg_completion = (
                    sum(total_completion_times) / len(total_completion_times)
                    if total_completion_times else 0
                )

                # Get total exchange count for percentile
                total_exchanges = (
                    session.query(ExchangeOrder)
                    .filter(
                        ExchangeOrder.user_id == user_id,
                        ExchangeOrder.status == "completed"
                    )
                    .count()
                )

                achievement_info = PostExchangeEngagementService._get_achievement_level(total_exchanges)
                percentile = min(95, max(15, 20 + (total_exchanges * 5)))  # Simple percentile calc

                followup_text = f"""📊 Your {Config.PLATFORM_NAME} Impact This Week

💱 {weekly_count} exchange{"s" if weekly_count != 1 else ""} completed**
💰 **₦{total_savings:,.0f} saved** in fees  
⚡ **Average completion: {avg_completion:.1f} minutes**
🏆 **{achievement_info['title']} status** achieved!

🎯 **You're in the top {100-percentile}% of active traders!**

Keep up the excellent momentum!"""

                keyboard = [
                    [
                        InlineKeyboardButton(
                            "📈 View Full Report", callback_data=f"view_full_report_{user_id}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "🔄 Quick Exchange", callback_data="start_exchange"
                        ),
                        InlineKeyboardButton(
                            "🏆 Share Achievement", callback_data=f"share_achievement_{user_id}"
                        ),
                    ],
                ]

                await context.bot.send_message(
                    chat_id=user_id,
                    text=followup_text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )

                logger.info(f"Weekly milestone report sent to user {user_id}")

            finally:
                session.close()

        except Exception as e:
            logger.error(f"Error sending weekly milestone report: {e}")

    @staticmethod
    def _calculate_savings(source_amount: float, target_amount: float, exchange_type: str) -> dict:
        """Calculate savings compared to traditional exchanges"""
        if exchange_type == "crypto_to_ngn":
            # For crypto to NGN: competitor would charge higher fees
            competitor_fee = max(
                PostExchangeEngagementService.MIN_COMPETITOR_FEE,
                target_amount * PostExchangeEngagementService.TRADITIONAL_EXCHANGE_FEE_RATE
            )
            our_fee = target_amount * float(Config.EXCHANGE_MARKUP_PERCENTAGE / 100)
            savings = competitor_fee - our_fee
            return {"amount": max(0, savings), "currency": "₦"}
        else:
            # For NGN to crypto: competitor would give less crypto
            competitor_fee = max(
                PostExchangeEngagementService.MIN_COMPETITOR_FEE,
                source_amount * PostExchangeEngagementService.TRADITIONAL_BANK_FEE_RATE
            )
            # Convert to NGN equivalent savings
            ngn_savings = competitor_fee * 800  # Rough USD to NGN conversion
            return {"amount": max(0, ngn_savings), "currency": "₦"}

    @staticmethod
    def _get_achievement_level(exchange_count: int) -> dict:
        """Get achievement level based on exchange count"""
        if exchange_count == 1:
            return {
                "emoji": "🥉",
                "title": "Bronze Exchanger",
                "message": "Welcome to the LockBay trading community!"
            }
        elif exchange_count <= 5:
            return {
                "emoji": "🥈",
                "title": "Silver Exchanger",
                "message": "You're becoming a regular trader!"
            }
        elif exchange_count <= 20:
            return {
                "emoji": "🥇",
                "title": "Gold Exchanger",
                "message": "Expert level trading achieved!"
            }
        else:
            return {
                "emoji": "💎",
                "title": "Diamond Exchanger",
                "message": "Elite trader status - you're amazing!"
            }

    @staticmethod
    def _get_next_achievements(current_count: int) -> str:
        """Get next achievement milestones"""
        if current_count < 5:
            return "🥈 Silver Exchanger (5 exchanges)\n🥇 Gold Exchanger (20 exchanges)"
        elif current_count < 20:
            return "🥇 Gold Exchanger (20 exchanges)\n💎 Diamond Exchanger (50 exchanges)"
        else:
            return "💎 Diamond Exchanger - **UNLOCKED!**\n🌟 Keep trading to maintain elite status!"