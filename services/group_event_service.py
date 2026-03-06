"""
Group Event Broadcast Service - Sends trade lifecycle events to all registered Telegram groups

Events broadcasted:
- Trade created (escrow created)
- Trade funded (payment confirmed)
- Seller accepted
- Escrow completed (funds released)
- Rating submitted
- New user onboarded (first-time user)

All messages include @bot_username and deeplink for maximum engagement.
"""

import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from telegram import Bot
from telegram.error import TelegramError, Forbidden
from config import Config

logger = logging.getLogger(__name__)

def _bot_tag() -> str:
    """Get the bot @username mention"""
    return f"@{Config.BOT_USERNAME}" if Config.BOT_USERNAME else "@LockBayBot"

def _bot_link() -> str:
    """Get the bot deeplink URL"""
    username = Config.BOT_USERNAME or "LockBayBot"
    return f"https://t.me/{username}"

class GroupEventService:
    """Broadcasts trade lifecycle events to all registered Telegram groups"""

    def __init__(self):
        self.name = "GroupEventService"

    def _get_active_groups(self) -> List[Dict[str, Any]]:
        """Get all active groups from database"""
        try:
            from models import BotGroup
            from database import SessionLocal
            with SessionLocal() as session:
                groups = session.query(BotGroup).filter(
                    BotGroup.is_active == True,
                    BotGroup.events_enabled == True
                ).all()
                return [{"chat_id": g.chat_id, "chat_title": g.chat_title} for g in groups]
        except Exception as e:
            logger.error(f"Error fetching active groups: {e}")
            return []

    async def _broadcast_to_groups(self, message: str, exclude_chat_id: Optional[int] = None) -> int:
        """Send a message to all registered active groups"""
        if not Config.BOT_TOKEN:
            logger.debug("Bot token not configured - skipping group broadcast")
            return 0

        groups = self._get_active_groups()
        if not groups:
            logger.debug("No active groups registered - skipping broadcast")
            return 0

        bot = Bot(Config.BOT_TOKEN)
        sent_count = 0

        for group in groups:
            chat_id = group["chat_id"]
            if exclude_chat_id and chat_id == exclude_chat_id:
                continue

            try:
                await bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode='HTML',
                    disable_web_page_preview=True
                )
                sent_count += 1
            except Forbidden:
                logger.warning(f"Bot removed from group {chat_id} - marking inactive")
                self._deactivate_group(chat_id)
            except TelegramError as e:
                logger.error(f"Failed to send to group {chat_id}: {e}")
            except Exception as e:
                logger.error(f"Unexpected error sending to group {chat_id}: {e}")

        return sent_count

    def _deactivate_group(self, chat_id: int):
        """Mark a group as inactive (bot was removed)"""
        try:
            from models import BotGroup
            from database import SessionLocal
            from sqlalchemy import update
            with SessionLocal() as session:
                session.execute(
                    update(BotGroup).where(BotGroup.chat_id == chat_id).values(
                        is_active=False,
                        removed_at=datetime.now(timezone.utc)
                    )
                )
                session.commit()
        except Exception as e:
            logger.error(f"Error deactivating group {chat_id}: {e}")

    async def broadcast_trade_created(self, data: Dict[str, Any]) -> int:
        """Broadcast when a new escrow trade is created"""
        escrow_id = data.get('escrow_id', 'Unknown')
        amount = data.get('amount', 0)
        currency = data.get('currency', 'USD')
        buyer_name = data.get('buyer_info', 'A trader')
        amount_display = f"${amount:,.2f}" if currency == 'USD' else f"{amount} {currency}"

        message = (
            f"\U0001f4e2 <b>New Escrow Trade Just Opened!</b>\n\n"
            f"\U0001f4b0 <b>{amount_display}</b> deal secured in escrow\n"
            f"\U0001f464 Buyer: {buyer_name}\n"
            f"\U0001f4cb Trade: <code>{escrow_id}</code>\n\n"
            f"Funds are locked and protected \u2014 waiting for a seller to accept.\n\n"
            f"\u26a1 <b>Want to trade safely?</b> Start now \u27a1 {_bot_tag()}\n"
            f"\U0001f517 {_bot_link()}"
        )
        count = await self._broadcast_to_groups(message)
        if count > 0:
            logger.info(f"Broadcasted trade_created {escrow_id} to {count} groups")
        return count

    async def broadcast_trade_funded(self, data: Dict[str, Any]) -> int:
        """Broadcast when escrow is funded (payment confirmed)"""
        escrow_id = data.get('escrow_id', 'Unknown')
        amount = data.get('amount', 0)
        currency = data.get('currency', 'USD')
        buyer_name = data.get('buyer_info', 'A trader')
        amount_display = f"${amount:,.2f}" if currency == 'USD' else f"{amount} {currency}"

        message = (
            f"\u2705 <b>Trade Funded \u2014 Payment Confirmed!</b>\n\n"
            f"\U0001f4b0 <b>{amount_display}</b> is now held in escrow\n"
            f"\U0001f464 Buyer: {buyer_name}\n"
            f"\U0001f4cb Trade: <code>{escrow_id}</code>\n\n"
            f"The buyer's funds are locked securely. Seller can now accept and deliver.\n\n"
            f"\U0001f6e1 <b>Trade with confidence on LockBay</b> \u27a1 {_bot_tag()}\n"
            f"\U0001f517 {_bot_link()}"
        )
        count = await self._broadcast_to_groups(message)
        if count > 0:
            logger.info(f"Broadcasted trade_funded {escrow_id} to {count} groups")
        return count

    async def broadcast_seller_accepted(self, data: Dict[str, Any]) -> int:
        """Broadcast when seller accepts the trade"""
        escrow_id = data.get('escrow_id', 'Unknown')
        amount = data.get('amount', 0)
        currency = data.get('currency', 'USD')
        seller_name = data.get('seller_info', 'A seller')
        amount_display = f"${amount:,.2f}" if currency == 'USD' else f"{amount} {currency}"

        message = (
            f"\U0001f91d <b>Seller Accepted \u2014 Trade is Live!</b>\n\n"
            f"\U0001f4b0 <b>{amount_display}</b> deal in progress\n"
            f"\U0001f464 Seller: {seller_name}\n"
            f"\U0001f4cb Trade: <code>{escrow_id}</code>\n\n"
            f"Both parties are engaged. Delivery is underway with escrow protection.\n\n"
            f"\U0001f525 <b>Join the action</b> \u27a1 {_bot_tag()}\n"
            f"\U0001f517 {_bot_link()}"
        )
        count = await self._broadcast_to_groups(message)
        if count > 0:
            logger.info(f"Broadcasted seller_accepted {escrow_id} to {count} groups")
        return count

    async def broadcast_escrow_completed(self, data: Dict[str, Any]) -> int:
        """Broadcast when escrow is completed (funds released)"""
        escrow_id = data.get('escrow_id', 'Unknown')
        amount = data.get('amount', 0)
        currency = data.get('currency', 'USD')
        amount_display = f"${amount:,.2f}" if currency == 'USD' else f"{amount} {currency}"

        message = (
            f"\U0001f389 <b>Trade Completed Successfully!</b>\n\n"
            f"\U0001f4b8 <b>{amount_display}</b> released to the seller\n"
            f"\U0001f4cb Trade: <code>{escrow_id}</code>\n\n"
            f"Another secure deal closed on LockBay. Both parties satisfied.\n\n"
            f"\U0001f4aa <b>Zero scams. Zero stress.</b>\n"
            f"Start your first escrow trade \u27a1 {_bot_tag()}\n"
            f"\U0001f517 {_bot_link()}"
        )
        count = await self._broadcast_to_groups(message)
        if count > 0:
            logger.info(f"Broadcasted escrow_completed {escrow_id} to {count} groups")
        return count

    async def broadcast_rating_submitted(self, data: Dict[str, Any]) -> int:
        """Broadcast when a rating is submitted"""
        escrow_id = data.get('escrow_id', 'Unknown')
        rating = data.get('rating', 0)
        reviewer_name = data.get('reviewer_info', 'A trader')
        stars = int(rating)
        star_display = "\u2b50" * stars + "\u2606" * (5 - stars)

        if stars >= 4:
            sentiment = "Another happy trader on LockBay!"
        elif stars >= 3:
            sentiment = "Honest feedback helps our community grow."
        else:
            sentiment = "We're always working to improve the experience."

        message = (
            f"\u2b50 <b>Trade Rated \u2014 {star_display}</b>\n\n"
            f"\U0001f4cb Trade: <code>{escrow_id}</code>\n"
            f"\U0001f464 Rated by: {reviewer_name}\n"
            f"\U0001f31f Rating: <b>{rating}/5</b>\n\n"
            f"{sentiment}\n\n"
            f"\U0001f4ca <b>Build your reputation</b> \u2014 trade on {_bot_tag()}\n"
            f"\U0001f517 {_bot_link()}"
        )
        count = await self._broadcast_to_groups(message)
        if count > 0:
            logger.info(f"Broadcasted rating {escrow_id} to {count} groups")
        return count

    async def broadcast_new_user_onboarded(self, data: Dict[str, Any]) -> int:
        """Broadcast when a new user joins LockBay"""
        first_name = data.get('first_name', 'Someone new')
        username = data.get('username')
        user_display = first_name
        if username:
            user_display = f"{first_name} (@{username})"

        message = (
            f"\U0001f680 <b>New Trader Joined LockBay!</b>\n\n"
            f"Welcome <b>{user_display}</b> to the community!\n\n"
            f"The LockBay network keeps growing \u2014 more traders means "
            f"more opportunities and better deals for everyone.\n\n"
            f"\U0001f91d <b>Join the community</b> \u27a1 {_bot_tag()}\n"
            f"\U0001f517 {_bot_link()}"
        )
        count = await self._broadcast_to_groups(message)
        if count > 0:
            logger.info(f"Broadcasted new_user_onboarded to {count} groups")
        return count

    @staticmethod
    def register_group(chat_id: int, chat_title: str, chat_type: str) -> bool:
        """Register a new group or reactivate an existing one"""
        try:
            from models import BotGroup
            from database import SessionLocal
            from sqlalchemy import update
            with SessionLocal() as session:
                existing = session.query(BotGroup).filter(BotGroup.chat_id == chat_id).first()
                if existing:
                    existing.is_active = True
                    existing.chat_title = chat_title
                    existing.chat_type = chat_type
                    existing.removed_at = None
                    existing.events_enabled = True
                    logger.info(f"Reactivated group: {chat_title} ({chat_id})")
                else:
                    new_group = BotGroup(
                        chat_id=chat_id,
                        chat_title=chat_title,
                        chat_type=chat_type,
                        is_active=True,
                        events_enabled=True
                    )
                    session.add(new_group)
                    logger.info(f"Registered new group: {chat_title} ({chat_id})")
                session.commit()
                return True
        except Exception as e:
            logger.error(f"Error registering group {chat_id}: {e}")
            return False

    @staticmethod
    def unregister_group(chat_id: int) -> bool:
        """Mark a group as inactive when bot is removed"""
        try:
            from models import BotGroup
            from database import SessionLocal
            from sqlalchemy import update
            with SessionLocal() as session:
                session.execute(
                    update(BotGroup).where(BotGroup.chat_id == chat_id).values(
                        is_active=False,
                        removed_at=datetime.now(timezone.utc)
                    )
                )
                session.commit()
                logger.info(f"Unregistered group: {chat_id}")
                return True
        except Exception as e:
            logger.error(f"Error unregistering group {chat_id}: {e}")
            return False

# Global instance
group_event_service = GroupEventService()
