"""
Savings Service for Phase 2 Retention Strategy
Track user savings and trigger milestone celebrations
"""

import logging
from typing import Dict, List
from datetime import datetime, timedelta
from database import SessionLocal
from models import UserEarnings
from config import Config

logger = logging.getLogger(__name__)


class SavingsService:
    """Service to manage user savings tracking and milestone celebrations"""

    # Realistic competitor fee percentages (based on cashout amount)
    CRYPTO_COMPETITOR_FEE_RATE = 0.05  # 5% typical crypto cashout fee rate
    FIAT_COMPETITOR_FEE_RATE = 0.03  # 3% typical fiat cashout fee rate
    MIN_COMPETITOR_FEE = 0.50  # Minimum $0.50 competitor fee
    MAX_COMPETITOR_FEE = 25.0  # Maximum $25.00 competitor fee cap

    @classmethod
    def format_currency(cls, amount: float) -> str:
        """Format amount with platform currency symbol"""
        if Config.PLATFORM_CURRENCY == "JPY":
            return (
                f"{Config.PLATFORM_CURRENCY_SYMBOL}{int(amount)}"  # No decimals for JPY
            )
        return f"{Config.PLATFORM_CURRENCY_SYMBOL}{amount:.2f}"

    @classmethod
    def format_platform_currency(cls, amount: float) -> str:
        """Format amount with platform currency symbol (alias for consistency)"""
        return cls.format_currency(amount)

    @classmethod
    async def record_cashout_savings(
        cls, user_id: int, amount_usd: float, cashout_type: str = "general", cashout_reference: str = None
    ) -> Dict:
        """Record cashout and calculate realistic USD-based savings vs competitors with idempotency"""
        session = SessionLocal()
        try:
            # Generate unique cashout reference if not provided
            if not cashout_reference:
                from datetime import datetime
                cashout_reference = f"CO_{user_id}_{int(datetime.utcnow().timestamp())}_{cashout_type}"
            
            # Check if savings already processed for this cashout
            if await cls._is_cashout_savings_processed(user_id, cashout_reference, amount_usd):
                logger.info(f"Cashout savings already processed for user {user_id}, reference: {cashout_reference}")
                return {
                    "savings_added": 0,
                    "total_savings": 0,
                    "new_milestones": [],
                    "cashout_count": 0,
                    "achievements": [],
                    "duplicate_prevented": True
                }

            # Get or create savings record
            savings = UserEarnings.get_or_create_for_user(session, user_id)

            # Calculate realistic savings based on cashout amount and type
            if cashout_type == "crypto":
                # 5% typical crypto cashout fee vs our 0%
                competitor_fee = amount_usd * cls.CRYPTO_COMPETITOR_FEE_RATE
            elif cashout_type == "fiat" or cashout_type == "ngn":
                # 3% typical fiat cashout fee vs our 0%
                competitor_fee = amount_usd * cls.FIAT_COMPETITOR_FEE_RATE
            else:
                # Default 4% for other cashout types
                competitor_fee = amount_usd * 0.04

            # Apply min/max limits to keep savings realistic
            savings_amount = max(
                min(competitor_fee, cls.MAX_COMPETITOR_FEE), cls.MIN_COMPETITOR_FEE
            )

            # For very small cashouts, reduce the minimum to be proportional
            if amount_usd < 10.0:  # For cashouts under $10
                savings_amount = min(
                    savings_amount, amount_usd * 0.1
                )  # Max 10% of cashout

            # Record cashout savings to prevent duplicates
            await cls._record_cashout_savings(user_id, cashout_reference, amount_usd, savings_amount)

            # Add savings (we charge $0, competitors charge percentage fees)
            milestones = savings.add_earnings(session, savings_amount)

            logger.info(
                f"Recorded ${savings_amount} savings for user {user_id}, type: {cashout_type}, reference: {cashout_reference}, new milestones: {milestones}"
            )

            return {
                "savings_added": savings_amount,
                "total_savings": float(savings.total_savings_usd or 0),
                "new_milestones": milestones,
                "cashout_count": savings.total_cashouts,
                "achievements": savings.get_achievements_summary(),
                "cashout_reference": cashout_reference,
            }

        except Exception as e:
            logger.error(f"Error recording cashout savings: {e}")
            return {"error": str(e)}
        finally:
            session.close()

    @classmethod
    async def record_escrow_completion(
        cls, user_id: int, escrow_amount_usd: float, escrow_reference: str = None
    ) -> Dict:
        """Record completed escrow transaction and check for new trading milestones with idempotency"""
        session = SessionLocal()
        try:
            # Generate unique escrow reference if not provided
            if not escrow_reference:
                from datetime import datetime
                escrow_reference = f"ESC_{user_id}_{int(datetime.utcnow().timestamp())}"
            
            # Check if escrow completion already processed
            if await cls._is_escrow_completion_processed(user_id, escrow_reference, escrow_amount_usd):
                logger.info(f"Escrow completion already processed for user {user_id}, reference: {escrow_reference}")
                return {
                    "escrow_added": False,
                    "escrow_amount": escrow_amount_usd,
                    "total_escrows": 0,
                    "total_volume": 0,
                    "new_milestones": [],
                    "achievements": [],
                    "duplicate_prevented": True
                }

            # Get or create savings record
            savings = UserEarnings.get_or_create_for_user(session, user_id)

            # Record escrow completion to prevent duplicates
            await cls._record_escrow_completion(user_id, escrow_reference, escrow_amount_usd)

            # Add escrow completion (this will call UserEarnings method)
            milestones = await cls._add_escrow_completion_tracking(session, savings, escrow_amount_usd)

            logger.info(
                f"Recorded escrow completion for user {user_id}, amount: ${escrow_amount_usd}, reference: {escrow_reference}, new milestones: {milestones}"
            )

            return {
                "escrow_added": True,
                "escrow_amount": escrow_amount_usd,
                "total_escrows": savings.total_escrows_completed,
                "total_volume": float(savings.total_escrow_volume_usd or 0),
                "new_milestones": milestones,
                "achievements": savings.get_achievements_summary(),
                "escrow_reference": escrow_reference,
            }

        except Exception as e:
            logger.error(f"Error recording escrow completion for user {user_id}: {e}")
            return {"escrow_added": False, "error": str(e), "new_milestones": []}
        finally:
            session.close()

    @classmethod
    async def get_user_savings_summary(cls, user_id: int) -> Dict:
        """Get comprehensive savings summary for user with aggressive caching"""
        # Check production cache first for faster responses
        from utils.production_cache import get_cached, set_cached
        cache_key = f"user_savings_{user_id}"
        
        cached_summary = get_cached(cache_key)
        if cached_summary:
            return cached_summary
        
        session = SessionLocal()
        try:
            savings = UserEarnings.get_or_create_for_user(session, user_id)

            summary = {
                "total_savings": float(savings.total_savings_usd or 0),
                "monthly_savings": float(savings.this_month_savings_usd or 0),
                "weekly_savings": float(savings.this_week_savings_usd or 0),
                "total_cashouts": savings.total_cashouts,
                "milestone_level": getattr(savings, 'get_current_level', lambda: 'bronze')() if hasattr(savings, 'get_current_level') else 'bronze',
                "milestones": {
                    "bronze": savings.milestone_bronze_achieved,
                    "silver": savings.milestone_silver_achieved,
                    "gold": savings.milestone_gold_achieved,
                    "diamond": savings.milestone_diamond_achieved,
                },
            }
            
            # Cache for 60 seconds to reduce database load
            set_cached(cache_key, summary, ttl=60)
            return summary

        except Exception as e:
            logger.error(f"Error getting savings summary: {e}")
            return {"error": str(e)}
        finally:
            session.close()

    @classmethod
    def get_milestone_message(cls, milestone: str, total_savings: float) -> str:
        """Get milestone celebration message (configurable currency, compact)"""
        savings_formatted = cls.format_currency(total_savings)

        milestone_messages = {
            "bronze": f"""🥉 Bronze Unlocked! {savings_formatted} saved
☕ 2 coffees worth - Keep going!""",
            "silver": f"""🥈 Silver Unlocked! {savings_formatted} saved
🍕 Several meals worth - Nice progress!""",
            "gold": f"""🥇 Gold Unlocked! {savings_formatted} saved
🛒 Weekly shopping worth - Power user!""",
            "diamond": f"""💎 Diamond Unlocked! {savings_formatted} saved
💰 Real money saved - VIP status!""",
        }

        return milestone_messages.get(milestone, "🎉 Milestone achieved!")

    @classmethod
    def get_weekly_report_message(cls, savings_summary: Dict) -> str:
        """Generate weekly savings report message (configurable currency, compact)"""
        weekly = savings_summary["weekly_savings"]
        monthly = savings_summary["monthly_savings"]
        total = savings_summary["total_savings"]

        weekly_fmt = cls.format_currency(weekly)
        competitor_fmt = cls.format_currency(3.0)  # Fixed competitor fee value
        monthly_fmt = cls.format_currency(monthly)
        total_fmt = cls.format_currency(total)

        return f"""📊 Week: {weekly_fmt} saved vs {competitor_fmt} competitors
📈 Month: {monthly_fmt} | Total: {total_fmt}
Keep it up! 🔥"""

    @classmethod
    async def get_users_needing_weekly_reports(cls) -> List[int]:
        """Get users who need weekly savings reports"""
        session = SessionLocal()
        try:
            # Users who had cashouts this week but haven't received report
            one_week_ago = datetime.utcnow() - timedelta(days=7)

            users = (
                session.query(UserEarnings)
                .filter(
                    UserEarnings.this_week_savings_usd > 0,
                    (
                        UserEarnings.last_weekly_report_sent.is_(None)
                        | (UserEarnings.last_weekly_report_sent < one_week_ago)
                    ),
                )
                .all()
            )

            return [
                (
                    int(savings.user_id)
                    if hasattr(savings, "user_id") and savings.user_id
                    else 0
                )
                for savings in users
            ]

        except Exception as e:
            logger.error(f"Error getting users for weekly reports: {e}")
            return []
        finally:
            session.close()

    @classmethod
    async def mark_weekly_report_sent(cls, user_id: int):
        """Mark that weekly report was sent to user"""
        session = SessionLocal()
        try:
            from sqlalchemy import update

            # Update weekly report fields via SQL update to avoid Column assignment issues
            session.execute(
                update(UserEarnings)
                .where(UserEarnings.user_id == user_id)
                .values(
                    last_weekly_report_sent=datetime.utcnow(),
                    weekly_reports_sent=UserEarnings.weekly_reports_sent + 1,
                )
            )
            session.commit()

        except Exception as e:
            logger.error(f"Error marking weekly report sent: {e}")
        finally:
            session.close()

    @classmethod
    def calculate_real_world_equivalent(cls, savings_amount: float) -> Dict[str, int]:
        """Calculate what USD savings can buy in real world"""
        # USD price estimates (universal)
        prices = {
            "coffee_cups": 3.0,  # $3 per coffee
            "meals": 8.0,  # $8 per meal
            "movie_tickets": 12.0,  # $12 per ticket
            "phone_credit": 10.0,  # $10 phone credit
            "transport_days": 5.0,  # $5 daily transport
        }

        equivalents = {}
        for item, price in prices.items():
            equivalents[item] = int(savings_amount // price)

        return equivalents

    @classmethod
    async def send_milestone_celebrations(cls, user_id: int, milestones: List[str]):
        """Send milestone celebration notifications to user"""
        if not milestones:
            return

        try:
            # Import bot for messaging - use correct import path
            try:
                from handlers.telegram_utils import get_bot_instance
            except ImportError:
                from utils.bot import get_bot_instance
            from models import User

            session = SessionLocal()
            user = session.query(User).filter(User.id == user_id).first()
            if not user or not user.telegram_id:
                return

            bot = get_bot_instance()

            for milestone in milestones:
                celebration_message = cls.get_enhanced_milestone_celebration_message(
                    milestone
                )
                if celebration_message:
                    try:
                        await bot.send_message(
                            chat_id=user.telegram_id,
                            text=celebration_message,
                            parse_mode="HTML",
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to send milestone celebration to user {user_id}: {e}"
                        )

            session.close()
        except Exception as e:
            logger.error(f"Error sending milestone celebrations: {e}")

    @classmethod
    def get_enhanced_milestone_celebration_message(cls, milestone: str) -> str:
        """Get enhanced celebration messages for both milestone types"""
        milestone_messages = {
            # Savings milestones
            "saver_bronze": """🥉 Bronze Saver Achievement!
            
🎉 You've saved $5+ with LockBay!

You're building smart financial habits! Keep using our platform to save even more compared to expensive competitors.

Next Goal: Silver Saver ($20 saved) 🥈""",
            "saver_silver": """🥈 Silver Saver Achievement!
            
🎉 You've saved $20+ with LockBay!

Fantastic progress! You're proving that smart choices add up to real savings over time.

Next Goal: Gold Saver ($50 saved) 🥇""",
            "saver_gold": """🥇 Gold Saver Achievement!
            
🎉 You've saved $50+ with LockBay!

Outstanding! You're in the top tier of smart savers. Your financial discipline is paying off!

Next Goal: Diamond Saver ($100 saved) 💎""",
            "saver_diamond": """💎 Diamond Saver Achievement!
            
🎉 You've saved $100+ with LockBay!

Elite status achieved! You're a financial champion who makes every dollar count. Keep up the excellence!""",
            # Trading milestones
            "trader_bronze": """🥉 Bronze Trader Achievement!
            
🎉 You've completed 3+ secure trades!

Welcome to the trading community! You're building trust and experience with every successful transaction.

Next Goal: Silver Trader (10 trades) 🥈""",
            "trader_silver": """🥈 Silver Trader Achievement!
            
🎉 You've completed 10+ secure trades!

Impressive trading activity! You're becoming a trusted member of our trading community.

Next Goal: Gold Trader (25 trades) 🥇""",
            "trader_gold": """🥇 Gold Trader Achievement!
            
🎉 You've completed 25+ secure trades!

Expert trader status! Your experience and reliability make you a valuable community member.

<b>Next Goal:</b> Diamond Trader (50 trades) 💎""",
            "trader_diamond": """💎 <b>Diamond Trader Achievement!</b>
            
<b>🎉 You've completed 50+ secure trades!</b>

Elite trader achievement! You're among the most experienced and trusted traders on our platform. Exceptional work!""",
        }

        return milestone_messages.get(
            milestone, f"🎉 Congratulations on your {milestone} achievement!"
        )

    @classmethod
    async def _is_cashout_savings_processed(cls, user_id: int, cashout_reference: str, amount_usd: float) -> bool:
        """Check if cashout savings already processed for this reference"""
        session = SessionLocal()
        try:
            from models import Transaction
            
            # Check for existing savings transaction with this cashout reference
            existing_transaction = session.query(Transaction).filter(
                Transaction.user_id == user_id,
                Transaction.transaction_type.in_(["earnings_escrow", "earnings_exchange", "earnings_cashout"]),
                Transaction.description.contains(cashout_reference)
            ).first()
            
            # Additional check for recent identical cashout savings (same amount, recent time)
            if not existing_transaction:
                from datetime import datetime, timedelta
                recent_cutoff = datetime.utcnow() - timedelta(minutes=30)  # 30 minutes window
                
                recent_identical = session.query(Transaction).filter(
                    Transaction.user_id == user_id,
                    Transaction.transaction_type.in_(["earnings_escrow", "earnings_exchange", "earnings_cashout"]),
                    Transaction.description.contains(f"(${amount_usd:.2f})"),
                    Transaction.created_at >= recent_cutoff
                ).first()
                
                if recent_identical:
                    logger.warning(f"Found recent identical cashout savings for user {user_id}, amount ${amount_usd:.2f}")
                    return True
            
            return existing_transaction is not None
            
        except Exception as e:
            logger.error(f"Error checking cashout savings status: {e}")
            return False  # If check fails, allow processing but log error
        finally:
            session.close()

    @classmethod
    async def _record_cashout_savings(cls, user_id: int, cashout_reference: str, amount_usd: float, savings_amount: float):
        """Record cashout savings processing to prevent future duplicates"""
        try:
            # This is handled by the UserEarnings.add_earnings transaction, but we can add additional tracking if needed
            logger.info(f"Cashout savings recorded: User {user_id}, Ref: {cashout_reference}, Amount: ${savings_amount:.2f}")
            
        except Exception as e:
            logger.error(f"Error recording cashout savings: {e}")

    @classmethod
    async def _is_escrow_completion_processed(cls, user_id: int, escrow_reference: str, escrow_amount: float) -> bool:
        """Check if escrow completion already processed for this reference"""
        session = SessionLocal()
        try:
            from models import Transaction
            
            # Check for existing escrow completion transaction with this reference
            existing_transaction = session.query(Transaction).filter(
                Transaction.user_id == user_id,
                Transaction.transaction_type.in_(["earnings_escrow", "earnings_exchange", "earnings_cashout"]),
                Transaction.description.contains(escrow_reference)
            ).first()
            
            # Additional check for recent identical escrow completions (same amount, recent time)
            if not existing_transaction:
                from datetime import datetime, timedelta
                recent_cutoff = datetime.utcnow() - timedelta(minutes=30)  # 30 minutes window
                
                recent_identical = session.query(Transaction).filter(
                    Transaction.user_id == user_id,
                    Transaction.transaction_type.in_(["earnings_escrow", "earnings_exchange", "earnings_cashout"]),
                    Transaction.description.contains(f"(${escrow_amount:.2f})"),
                    Transaction.created_at >= recent_cutoff
                ).first()
                
                if recent_identical:
                    logger.warning(f"Found recent identical escrow completion for user {user_id}, amount ${escrow_amount:.2f}")
                    return True
            
            return existing_transaction is not None
            
        except Exception as e:
            logger.error(f"Error checking escrow completion status: {e}")
            return False  # If check fails, allow processing but log error
        finally:
            session.close()

    @classmethod
    async def _record_escrow_completion(cls, user_id: int, escrow_reference: str, escrow_amount: float):
        """Record escrow completion processing to prevent future duplicates"""
        try:
            # This is handled by the UserEarnings tracking, but we can add additional tracking if needed
            logger.info(f"Escrow completion recorded: User {user_id}, Ref: {escrow_reference}, Amount: ${escrow_amount:.2f}")
            
        except Exception as e:
            logger.error(f"Error recording escrow completion: {e}")

    @classmethod
    async def _add_escrow_completion_tracking(cls, session, savings, escrow_amount_usd: float):
        """Add escrow completion tracking - simplified version of add_escrow_completion"""
        try:
            
            # Update escrow tracking
            savings.total_escrows_completed = (savings.total_escrows_completed or 0) + 1
            savings.total_escrow_volume_usd = (
                float(savings.total_escrow_volume_usd or 0) + escrow_amount_usd
            )
            savings.this_month_escrows = (savings.this_month_escrows or 0) + 1
            savings.this_week_escrows = (savings.this_week_escrows or 0) + 1
            
            # Check for new trading milestones
            milestones = []
            total_volume = float(savings.total_escrow_volume_usd or 0)
            
            if savings.milestone_trader_bronze_achieved is not True and total_volume >= 250.0:
                savings.milestone_trader_bronze_achieved = True
                milestones.append("trader_bronze")
            if savings.milestone_trader_silver_achieved is not True and total_volume >= 1000.0:
                savings.milestone_trader_silver_achieved = True
                milestones.append("trader_silver")
            if savings.milestone_trader_gold_achieved is not True and total_volume >= 5000.0:
                savings.milestone_trader_gold_achieved = True
                milestones.append("trader_gold")
            if savings.milestone_trader_diamond_achieved is not True and total_volume >= 15000.0:
                savings.milestone_trader_diamond_achieved = True
                milestones.append("trader_diamond")
            
            session.commit()
            return milestones
            
        except Exception as e:
            logger.error(f"Error in escrow completion tracking: {e}")
            session.rollback()
            return []


# Global service instance
savings_service = SavingsService()
