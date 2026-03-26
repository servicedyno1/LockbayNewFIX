"""
Buyer Payment Confirmation Notifications
Reusable notification logic for sending payment confirmation to buyers
"""

import logging
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from typing import Optional
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from services.consolidated_notification_service import (
    ConsolidatedNotificationService,
    NotificationRequest,
    NotificationCategory,
    NotificationPriority
)
from utils.markdown_escaping import format_username_html
from handlers.escrow import clean_seller_identifier

logger = logging.getLogger(__name__)


async def send_buyer_payment_confirmation(
    buyer_id: int,
    escrow_db_id: int,
    escrow_public_id: str,
    escrow_amount: Decimal,
    buyer_fee: Decimal,
    seller_identifier: str,
    seller_type: str,
    payment_confirmed_at: Optional[datetime] = None,
    expires_at: Optional[datetime] = None,
    overpayment_credited: Optional[Decimal] = None
) -> bool:
    """
    Send payment confirmation to buyer via Telegram + Email
    
    This function sends dual-channel notifications (Telegram bot + Email) to confirm
    that the buyer's payment has been received and the escrow has been created.
    
    Args:
        buyer_id: Buyer's user ID
        escrow_db_id: Escrow database ID (for callback buttons)
        escrow_public_id: Escrow public ID (e.g., ES101825UJKS)
        escrow_amount: Escrow amount in USD
        buyer_fee: Fee paid by buyer in USD
        seller_identifier: Seller's username or identifier
        seller_type: Type of seller identifier ("username" or other)
        payment_confirmed_at: When payment was confirmed
        expires_at: When seller acceptance expires
        overpayment_credited: Amount of overpayment credited to wallet (optional)
        
    Returns:
        bool: True if notifications sent successfully, False otherwise
    """
    try:
        notification_service = ConsolidatedNotificationService()
        
        # Calculate total amount paid (amount + fees)
        escrow_amount_float = float(escrow_amount)
        buyer_fee_float = float(buyer_fee)
        total_paid = escrow_amount_float + buyer_fee_float
        
        # Format seller display for HTML (used in bot message)
        import html
        seller_display_html = (
            format_username_html(f"@{seller_identifier}", include_link=False)
            if seller_type == "username"
            else html.escape(seller_identifier)
        )
        
        # Format seller display for plain text (used in countdown message)
        seller_display_plain = clean_seller_identifier(seller_identifier)
        if seller_type == "username":
            seller_display_plain = f"@{seller_display_plain}"
        
        # Add overpayment notice if applicable
        overpayment_notice = ""
        if overpayment_credited and overpayment_credited > 0:
            overpayment_float = float(overpayment_credited)
            overpayment_notice = f"\n\n💰 Overpayment: ${overpayment_float:.2f} credited to wallet"
        
        # Create notification message (mobile-optimized)
        message = f"""✅ Payment Sent

#{escrow_public_id[-8:]} • ${escrow_amount_float:.2f}
Paid: ${total_paid:.2f} (inc. ${buyer_fee_float:.2f} fee)
To: {seller_display_html}

⏰ Awaiting seller acceptance
Funds secured in escrow{overpayment_notice}"""

        # Send notification to buyer (both bot and email)
        request = NotificationRequest(
            user_id=buyer_id,
            category=NotificationCategory.ESCROW_UPDATES,
            priority=NotificationPriority.HIGH,
            title="✅ Payment Sent",
            message=message,
            template_data={
                "escrow_id": escrow_public_id,
                "amount": escrow_amount_float,
                "total_paid": total_paid,
                "buyer_fee": buyer_fee_float,
                "seller": seller_display_html,
                "parse_mode": "HTML",
                "keyboard": [
                    [{"text": "📋 View Trade", "callback_data": f"view_trade_{escrow_db_id}"}],
                    [{"text": "🏠 Main Menu", "callback_data": "main_menu"}]
                ]
            },
            broadcast_mode=True  # CRITICAL: Dual-channel delivery (Telegram + Email)
        )
        
        result = await notification_service.send_notification(request)
        logger.info(f"✅ Payment confirmation sent to buyer {buyer_id} via {len(result)} channels (escrow {escrow_public_id})")
        return True
        
    except Exception as e:
        logger.error(f"Failed to send payment confirmation to buyer {buyer_id}: {e}")
        return False


def create_payment_complete_message(
    escrow_public_id: str,
    escrow_amount: Decimal,
    buyer_fee: Decimal,
    seller_identifier: str,
    seller_type: str,
    fee_paid_by: Optional[str] = None,
    payment_confirmed_at: Optional[datetime] = None,
    expires_at: Optional[datetime] = None
) -> tuple[str, InlineKeyboardMarkup]:
    """
    Create "Payment Complete" message with countdown timer
    
    This creates the message that shows in the buyer's chat after payment,
    including the dynamic countdown timer for seller acceptance.
    
    Args:
        escrow_public_id: Escrow public ID
        escrow_amount: Escrow amount in USD
        buyer_fee: Fee paid by buyer
        seller_identifier: Seller's username or identifier
        seller_type: Type of seller identifier
        fee_paid_by: Who paid the fee ("buyer", "seller", "split")
        payment_confirmed_at: When payment was confirmed
        expires_at: When seller acceptance expires
        
    Returns:
        tuple: (message_text, keyboard_markup)
    """
    # Build fee display based on who paid
    fee_info = ""
    fee_amount = buyer_fee if fee_paid_by in ("buyer", "buyer_pays") else Decimal("0.0")
    
    if fee_paid_by in ("buyer", "buyer_pays") and fee_amount > 0:
        total_paid = escrow_amount + fee_amount
        fee_info = f"\n💸 You paid: ${total_paid:.2f} (inc. ${fee_amount:.2f} fee)"
    elif fee_paid_by == "split" and fee_amount > 0:
        split_fee = fee_amount / 2
        fee_info = f"\n💸 You paid: ${escrow_amount + split_fee:.2f} (inc. ${split_fee:.2f} fee)"
    elif fee_paid_by in ("seller", "seller_pays"):
        fee_info = f"\n💸 You paid: ${escrow_amount:.2f}"
    
    # Calculate seller acceptance deadline
    from config import Config as AppConfig
    
    if not expires_at and payment_confirmed_at:
        seller_timeout_minutes = getattr(AppConfig, 'SELLER_RESPONSE_TIMEOUT_MINUTES', 1440)  # 24 hours
        expires_at = payment_confirmed_at + timedelta(minutes=seller_timeout_minutes)
    
    # Format time remaining
    seller_time_msg = "⏰ Seller has 24h to accept"  # Fallback
    if expires_at:
        current_time = datetime.now(timezone.utc)
        expires_at_aware = expires_at.replace(tzinfo=timezone.utc) if expires_at.tzinfo is None else expires_at
        time_remaining = expires_at_aware - current_time
        
        if time_remaining.total_seconds() > 0:
            total_minutes = int(time_remaining.total_seconds() / 60)
            hours = total_minutes // 60
            minutes = total_minutes % 60
            
            if hours > 0 and minutes > 0:
                seller_time_msg = f"⏰ Seller has {hours}h {minutes}m left to accept"
            elif hours > 0:
                seller_time_msg = f"⏰ Seller has {hours}h left to accept"
            else:
                seller_time_msg = f"⏰ Seller has {minutes}m left to accept"
    
    # Clean seller display for plain text (no markdown escaping)
    seller_display_clean = clean_seller_identifier(seller_identifier)
    if seller_type == "username":
        seller_display_clean = f"@{seller_display_clean}"
    
    text = f"""✅ Payment Complete

📤 Offer sent to: {seller_display_clean}
💰 Trade: ${escrow_amount:.2f}{fee_info}
🆔 ID: {escrow_public_id}

{seller_time_msg}"""

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 View Trade", callback_data="view_trade_0")],  # Will be updated with actual ID
        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
    ])
    
    return text, keyboard
