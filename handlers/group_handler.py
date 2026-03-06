"""
Group Handler - Manages bot being added/removed from Telegram groups.

When the bot is added to a group:
- Registers the group in the database
- Sends a welcome message explaining what events will be broadcasted

When the bot is removed from a group:
- Marks the group as inactive in the database
"""

import logging
from telegram import Update, ChatMember
from telegram.ext import ContextTypes, ChatMemberHandler

logger = logging.getLogger(__name__)


async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle bot being added to or removed from a group"""
    if not update.my_chat_member:
        return

    chat = update.my_chat_member.chat
    new_status = update.my_chat_member.new_chat_member.status
    old_status = update.my_chat_member.old_chat_member.status

    # Bot was added to a group
    if old_status in (ChatMember.LEFT, ChatMember.BANNED) and new_status in (ChatMember.MEMBER, ChatMember.ADMINISTRATOR):
        logger.info(f"Bot added to group: {chat.title} ({chat.id}), type: {chat.type}")

        try:
            from services.group_event_service import group_event_service
            group_event_service.register_group(
                chat_id=chat.id,
                chat_title=chat.title or "Unknown Group",
                chat_type=chat.type
            )
            logger.info(f"Registered group: {chat.title} ({chat.id})")
        except Exception as e:
            logger.error(f"Failed to register group {chat.id}: {e}")

        # Send welcome message
        try:
            welcome_text = (
                "Welcome to <b>LockBay Escrow</b>!\n\n"
                "I'll broadcast trade activity updates here:\n"
                "- New escrow trades opened\n"
                "- Payments confirmed\n"
                "- Seller acceptances\n"
                "- Completed deals\n"
                "- New user signups\n"
                "- Trade ratings\n\n"
                "All private trades remain confidential.\n"
                "Use /start in a private chat to begin trading."
            )
            await context.bot.send_message(
                chat_id=chat.id,
                text=welcome_text,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Failed to send welcome message to group {chat.id}: {e}")

    # Bot was removed from a group
    elif old_status in (ChatMember.MEMBER, ChatMember.ADMINISTRATOR) and new_status in (ChatMember.LEFT, ChatMember.BANNED):
        logger.info(f"Bot removed from group: {chat.title} ({chat.id})")

        try:
            from services.group_event_service import group_event_service
            group_event_service.unregister_group(chat_id=chat.id)
            logger.info(f"Unregistered group: {chat.title} ({chat.id})")
        except Exception as e:
            logger.error(f"Failed to unregister group {chat.id}: {e}")


def register_group_handlers(application) -> None:
    """Register group management handlers"""
    application.add_handler(
        ChatMemberHandler(handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER)
    )
    logger.info("Registered group management handlers")
