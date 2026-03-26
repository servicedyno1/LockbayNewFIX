"""
Post-Completion Notification Service
Handles comprehensive notifications to buyers and sellers after escrow completion
"""

import logging
from typing import Optional, Dict
from datetime import datetime
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from services.email import EmailService
from config import Config
from database import async_managed_session
from models import User, Escrow, Rating
from sqlalchemy import select

logger = logging.getLogger(__name__)


class PostCompletionNotificationService:
    """Send comprehensive post-completion notifications to buyers and sellers"""
    
    def __init__(self):
        if not Config.BOT_TOKEN:
            raise ValueError("BOT_TOKEN not configured")
        self.bot = Bot(token=Config.BOT_TOKEN)
        self.email_service = EmailService()
    
    def _format_currency(self, amount: float, currency: str = 'USD') -> str:
        """
        Format currency amount with proper symbol and precision.
        
        Args:
            amount: Numerical amount to format
            currency: Currency code (NGN, USD, BTC, ETH, etc.)
            
        Returns:
            Formatted currency string with proper symbol and precision
        """
        if currency == 'NGN':
            return f"₦{amount:,.2f}"
        elif currency == 'USD':
            return f"${amount:,.2f}"
        else:
            return f"{amount:.8f} {currency}"
    
    def _format_timestamp(self, dt: datetime) -> str:
        """
        Format timestamp with absolute time and relative time ago.
        
        Args:
            dt: Datetime object to format
            
        Returns:
            Formatted timestamp string like "YYYY-MM-DD HH:MM UTC (X hours ago)"
        """
        from datetime import timezone
        
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        
        now = datetime.utcnow()
        diff = now - dt
        
        if diff.days > 0:
            relative = f"{diff.days} day{'s' if diff.days != 1 else ''} ago"
        elif diff.seconds >= 3600:
            hours = diff.seconds // 3600
            relative = f"{hours} hour{'s' if hours != 1 else ''} ago"
        else:
            minutes = diff.seconds // 60
            relative = f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        
        return f"{dt.strftime('%Y-%m-%d %H:%M')} UTC ({relative})"
    
    async def notify_escrow_completion(
        self,
        escrow_id: str,
        completion_type: str,  # 'released', 'refunded', 'dispute_resolved'
        amount: float,
        buyer_id: int,
        seller_id: int,
        buyer_email: Optional[str] = None,
        seller_email: Optional[str] = None,
        dispute_winner_id: Optional[int] = None,
        dispute_loser_id: Optional[int] = None,
        resolution_type: Optional[str] = None
    ) -> Dict[str, bool]:
        """Send comprehensive completion notifications to both parties"""
        
        results = {
            'buyer_telegram': False,
            'buyer_email': False,
            'seller_telegram': False,
            'seller_email': False,
            'rating_prompts': False
        }
        
        try:
            async with async_managed_session() as session:
                # Get user details and escrow object using async queries
                buyer_result = await session.execute(select(User).where(User.id == buyer_id))
                buyer = buyer_result.scalar_one_or_none()
                
                seller_result = await session.execute(select(User).where(User.id == seller_id))
                seller = seller_result.scalar_one_or_none()
                
                escrow_result = await session.execute(select(Escrow).where(Escrow.escrow_id == escrow_id))
                escrow = escrow_result.scalar_one_or_none()
                
                if not buyer or not seller:
                    logger.error(f"Missing user data for escrow {escrow_id}")
                    return results
                
                # Extract seller_contact_display for buyer-facing notifications (convert Column to str)
                seller_contact_display = str(escrow.seller_contact_display) if escrow and escrow.seller_contact_display else None
                escrow_numeric_id = int(escrow.id) if escrow and escrow.id else None
                
                # Send buyer notifications
                if completion_type == 'released':
                    results['buyer_telegram'] = await self._notify_buyer_funds_released(
                        buyer, escrow_id, amount, seller, seller_contact_display, escrow_numeric_id
                    )
                    email = buyer_email or buyer.email
                    if email:
                        results['buyer_email'] = await self._send_buyer_completion_email(
                            email, escrow_id, amount, completion_type, seller, seller_contact_display
                        )
                elif completion_type == 'refunded':
                    results['buyer_telegram'] = await self._notify_buyer_refund_received(
                        buyer, escrow_id, amount
                    )
                    email = buyer_email or buyer.email
                    if email:
                        results['buyer_email'] = await self._send_buyer_completion_email(
                            email, escrow_id, amount, completion_type, seller, seller_contact_display
                        )
                elif completion_type == 'dispute_resolved':
                    # Determine buyer's amount based on resolution type
                    is_buyer_winner = (dispute_winner_id == buyer_id)
                    is_buyer_loser = (dispute_loser_id == buyer_id)
                    
                    if resolution_type and resolution_type.startswith('custom_split_'):
                        # Custom split: calculate buyer's portion from percentage
                        parts = resolution_type.split('_')
                        if len(parts) >= 4:
                            buyer_percent = int(parts[2])
                            buyer_amount = amount * buyer_percent / 100
                        else:
                            buyer_amount = amount  # Fallback to full amount
                    elif is_buyer_winner:
                        # Buyer wins: they get the full refund amount
                        buyer_amount = amount
                    elif is_buyer_loser:
                        # Buyer loses: they get nothing
                        buyer_amount = 0.0
                    else:
                        # Fallback (shouldn't happen)
                        buyer_amount = amount
                    
                    if resolution_type:
                        results['buyer_telegram'] = await self._notify_dispute_outcome(
                            buyer, escrow_id, buyer_amount, seller, is_buyer_winner, resolution_type, buyer, seller, escrow_numeric_id
                        )
                    email = buyer_email or buyer.email
                    if email and resolution_type:
                        results['buyer_email'] = await self._send_dispute_resolution_email(
                            email, escrow_id, buyer_amount, is_buyer_winner, 
                            resolution_type, seller, role='buyer', seller_contact_display=seller_contact_display
                        )
                
                # Send seller notifications
                if completion_type == 'released':
                    results['seller_telegram'] = await self._notify_seller_payment_received(
                        seller, escrow_id, amount, buyer, escrow_numeric_id
                    )
                    email = seller_email or seller.email
                    if email:
                        results['seller_email'] = await self._send_seller_completion_email(
                            email, escrow_id, amount, completion_type, buyer
                        )
                elif completion_type == 'refunded':
                    results['seller_telegram'] = await self._notify_seller_trade_refunded(
                        seller, escrow_id, amount
                    )
                    email = seller_email or seller.email
                    if email:
                        results['seller_email'] = await self._send_seller_completion_email(
                            email, escrow_id, amount, completion_type, buyer
                        )
                elif completion_type == 'dispute_resolved':
                    # Determine seller's amount based on resolution type
                    is_seller_winner = (dispute_winner_id == seller_id)
                    is_seller_loser = (dispute_loser_id == seller_id)
                    
                    if resolution_type and resolution_type.startswith('custom_split_'):
                        # Custom split: calculate seller's portion from percentage
                        parts = resolution_type.split('_')
                        if len(parts) >= 4:
                            seller_percent = int(parts[3])
                            seller_amount = amount * seller_percent / 100
                        else:
                            seller_amount = amount  # Fallback to full amount
                    elif is_seller_winner:
                        # Seller wins: they get the full release amount
                        seller_amount = amount
                    elif is_seller_loser:
                        # Seller loses: they get nothing
                        seller_amount = 0.0
                    else:
                        # Fallback (shouldn't happen)
                        seller_amount = amount
                    
                    if resolution_type:
                        results['seller_telegram'] = await self._notify_dispute_outcome(
                            seller, escrow_id, seller_amount, buyer, is_seller_winner, resolution_type, buyer, seller, escrow_numeric_id
                        )
                    email = seller_email or seller.email
                    if email and resolution_type:
                        results['seller_email'] = await self._send_dispute_resolution_email(
                            email, escrow_id, seller_amount, is_seller_winner, 
                            resolution_type, buyer, role='seller'
                        )
                
                # Rating prompts are now unified in completion notifications (no separate messages)
                if completion_type == 'released':
                    # Rating button already included in _notify_buyer_funds_released and _notify_seller_payment_received
                    results['rating_prompts'] = True
                elif completion_type == 'dispute_resolved':
                    # Rating button already included in _notify_dispute_outcome for both parties
                    results['rating_prompts'] = True
                
                logger.info(f"Post-completion notifications sent for {escrow_id}: {results}")
                return results
                
        except Exception as e:
            logger.error(f"Error in post-completion notifications for {escrow_id}: {e}")
            return results
    
    async def _notify_buyer_funds_released(
        self, buyer: User, escrow_id: str, amount: float, seller: User, seller_contact_display: Optional[str] = None, escrow_numeric_id: Optional[int] = None
    ) -> bool:
        """Send Telegram notification to buyer when funds are released - UNIFIED with rating"""
        try:
            seller_name = seller_contact_display or (seller.first_name if seller else None) or (seller.username if seller else None) or "Seller"
            seller_username = f"@{seller.username}" if seller.username else seller_name
            amount_display = self._format_currency(amount, 'USD')
            
            # Compact mobile-friendly message with rating prompt
            message = (
                f"✅ <b>Trade Complete: {amount_display}</b>\n"
                f"🆔 <code>{escrow_id}</code> • Seller: {seller_name}\n\n"
                f"💭 <b>Rate {seller_username}</b>\n"
                "Help others by sharing your experience!"
            )
            
            # Unified: Rating button + navigation - only include rating if we have numeric ID
            keyboard = []
            if escrow_numeric_id:
                keyboard.append([InlineKeyboardButton(f"⭐ Rate {seller_username}", callback_data=f"rate_escrow_{escrow_numeric_id}")])
            keyboard.append([InlineKeyboardButton("💰 Wallet", callback_data="wallet_menu"), 
                             InlineKeyboardButton("🏠 Menu", callback_data="main_menu")])
            
            await self.bot.send_message(
                chat_id=int(buyer.telegram_id),
                text=message,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            logger.info(f"Buyer unified completion+rating notification sent for {escrow_id}")
            return True
            
        except TelegramError as e:
            logger.error(f"Failed to send buyer completion notification for {escrow_id}: {e}")
            return False
    
    async def _notify_buyer_refund_received(
        self, buyer: User, escrow_id: str, amount: float
    ) -> bool:
        """Send Telegram notification to buyer when refund is processed"""
        try:
            amount_display = self._format_currency(amount, 'USD')
            message = (
                f"↩️ <b>Refunded: {amount_display}</b>\n"
                f"🆔 <code>{escrow_id}</code> • Funds returned\n\n"
                "📧 Questions? Contact support"
            )
            
            keyboard = [
                [InlineKeyboardButton("💰 View Wallet", callback_data="wallet_menu")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]
            
            await self.bot.send_message(
                chat_id=int(buyer.telegram_id),
                text=message,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            logger.info(f"Buyer refund notification sent for {escrow_id}")
            return True
            
        except TelegramError as e:
            logger.error(f"Failed to send buyer refund notification for {escrow_id}: {e}")
            return False
    
    async def _notify_seller_payment_received(
        self, seller: User, escrow_id: str, amount: float, buyer: User, escrow_numeric_id: Optional[int] = None
    ) -> bool:
        """Send Telegram notification to seller when payment is received - UNIFIED with rating"""
        try:
            buyer_name = buyer.first_name or buyer.username or "Buyer"
            buyer_username = f"@{buyer.username}" if buyer.username else buyer_name
            amount_display = self._format_currency(amount, 'USD')
            
            # Compact mobile-friendly message with rating prompt
            message = (
                f"🎉 <b>Payment: {amount_display} Received!</b>\n"
                f"🆔 <code>{escrow_id}</code> • Buyer: {buyer_name}\n\n"
                f"💭 <b>Rate {buyer_username}</b>\n"
                "Help others by sharing your experience!"
            )
            
            # Unified: Rating button + actions - only include rating if we have numeric ID
            keyboard = []
            if escrow_numeric_id:
                keyboard.append([InlineKeyboardButton(f"⭐ Rate {buyer_username}", callback_data=f"rate_escrow_{escrow_numeric_id}")])
            keyboard.append([InlineKeyboardButton("💸 Cash Out", callback_data="wallet_cashout"), 
                             InlineKeyboardButton("🏠 Menu", callback_data="main_menu")])
            
            await self.bot.send_message(
                chat_id=int(seller.telegram_id),
                text=message,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            logger.info(f"Seller unified payment+rating notification sent for {escrow_id}")
            return True
            
        except TelegramError as e:
            logger.error(f"Failed to send seller payment notification for {escrow_id}: {e}")
            return False
    
    async def _notify_seller_trade_refunded(
        self, seller: User, escrow_id: str, amount: float
    ) -> bool:
        """Send Telegram notification to seller when trade is refunded"""
        try:
            amount_display = self._format_currency(amount, 'USD')
            message = (
                f"↩️ <b>Trade Refunded: {amount_display}</b>\n"
                f"🆔 <code>{escrow_id}</code> • No payment\n\n"
                "📧 Questions? Contact support"
            )
            
            keyboard = [
                [InlineKeyboardButton("📊 My Trades", callback_data="my_trades")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]
            
            await self.bot.send_message(
                chat_id=int(seller.telegram_id),
                text=message,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            logger.info(f"Seller refund notification sent for {escrow_id}")
            return True
            
        except TelegramError as e:
            logger.error(f"Failed to send seller refund notification for {escrow_id}: {e}")
            return False
    
    async def _send_rating_prompts(
        self, buyer: User, seller: User, escrow_id: str, session
    ) -> bool:
        """Send delayed rating prompts to encourage feedback"""
        try:
            # Get escrow ID first using async query
            escrow_result = await session.execute(select(Escrow).where(Escrow.escrow_id == escrow_id))
            escrow = escrow_result.scalar_one_or_none()
            
            if not escrow:
                logger.warning(f"Escrow {escrow_id} not found for rating prompts")
                return False
            
            # Check if ratings already exist using async queries
            buyer_rating_result = await session.execute(
                select(Rating).where(
                    Rating.escrow_id == escrow.id,
                    Rating.rater_id == buyer.id,
                    Rating.category == 'seller'
                )
            )
            buyer_rating = buyer_rating_result.scalar_one_or_none()
            
            seller_rating_result = await session.execute(
                select(Rating).where(
                    Rating.escrow_id == escrow.id,
                    Rating.rater_id == seller.id,
                    Rating.category == 'buyer'
                )
            )
            seller_rating = seller_rating_result.scalar_one_or_none()
            
            # Schedule rating reminders for users who haven't rated yet
            # This could be enhanced with a background job scheduler
            logger.info(f"Rating prompts tracked for {escrow_id} (buyer_rated: {bool(buyer_rating)}, seller_rated: {bool(seller_rating)})")
            return True
            
        except Exception as e:
            logger.error(f"Error in rating prompts for {escrow_id}: {e}")
            return False
    
    async def _send_buyer_completion_email(
        self, email: str, escrow_id: str, amount: float, completion_type: str, seller: User, seller_contact_display: Optional[str] = None
    ) -> bool:
        """Send completion email to buyer with rating deep link"""
        try:
            seller_name = seller_contact_display or (seller.first_name if seller else None) or (seller.username if seller else None) or "Seller"
            seller_username = f"@{seller.username}" if seller.username else seller_name
            amount_display = self._format_currency(amount, 'USD')
            timestamp_display = self._format_timestamp(datetime.utcnow())
            
            # Deep link to open Telegram bot and trigger rating
            rating_deep_link = f"https://t.me/{Config.BOT_USERNAME}?start=rate_{escrow_id}"
            
            if completion_type == 'released':
                subject = f"✅ Trade Complete: {amount_display} - Rate {seller_username}"
                status_title = "Trade Completed!"
                status_message = "Funds successfully released to seller"
                status_color = "#28a745"
                icon = "✅"
                rating_section = f"""
                    <div style="margin-top: 25px; padding: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 8px; text-align: center;">
                        <h3 style="color: white; margin: 0 0 10px 0;">⭐ Rate Your Seller</h3>
                        <p style="color: white; margin: 0 0 15px 0; font-size: 14px;">How was your experience with {seller_username}?</p>
                        <a href="{rating_deep_link}" style="display: inline-block; background: white; color: #667eea; padding: 12px 30px; border-radius: 25px; text-decoration: none; font-weight: bold; font-size: 16px;">⭐ Rate {seller_username}</a>
                        <p style="color: rgba(255,255,255,0.8); margin: 10px 0 0 0; font-size: 12px;">Opens in Telegram</p>
                    </div>
                """
            else:  # refunded
                subject = f"↩️ Trade Refunded - {Config.PLATFORM_NAME}"
                status_title = "Trade Refunded"
                status_message = "Funds returned to your wallet"
                status_color = "#dc3545"
                icon = "↩️"
                rating_section = ""
            
            html_content = f"""
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <!-- Summary Box at Top -->
                <div style="background: #f0f8ff; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                    <strong style="color: #333;">Trade ID:</strong> <span style="font-family: monospace;">{escrow_id}</span><br>
                    <strong style="color: #333;">Amount:</strong> {amount_display}<br>
                    <strong style="color: #333;">Completed:</strong> {timestamp_display}
                </div>
                
                <!-- Status Badge -->
                <div style="background: {status_color}; color: white; padding: 20px; text-align: center; border-radius: 12px 12px 0 0;">
                    <h1 style="margin: 0; font-size: 24px;">{icon} {status_title}</h1>
                    <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 14px;">{Config.PLATFORM_NAME}</p>
                </div>
                
                <div style="background: white; padding: 25px; border-radius: 0 0 12px 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                    <h2 style="margin: 0 0 15px 0; color: #333;">Trade Details</h2>
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 15px;">
                        <tr style="border-bottom: 1px solid #f0f0f0;">
                            <td style="padding: 10px 0; color: #666; font-size: 14px;">Amount</td>
                            <td style="padding: 10px 0; text-align: right; font-weight: bold; font-size: 16px;">{amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #f0f0f0;">
                            <td style="padding: 10px 0; color: #666; font-size: 14px;">Seller</td>
                            <td style="padding: 10px 0; text-align: right; font-size: 14px;">{seller_name}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #f0f0f0;">
                            <td style="padding: 10px 0; color: #666; font-size: 14px;">Status</td>
                            <td style="padding: 10px 0; text-align: right; font-size: 14px; color: {status_color}; font-weight: bold;">{status_message}</td>
                        </tr>
                    </table>
                    
                    <!-- What to Do Next -->
                    <div style="margin-top: 20px; padding: 15px; background: #fffbf0; border-left: 4px solid #ffc107; border-radius: 4px;">
                        <strong style="color: #333;">📋 What to Do Next:</strong>
                        <p style="margin: 10px 0 0 0; color: #666;">Your wallet has been updated. You can start a new trade or cash out your funds.</p>
                    </div>
                    
                    {rating_section}
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #999; font-size: 12px;">
                    <p style="margin: 5px 0;">Thank you for using {Config.PLATFORM_NAME}</p>
                    <p style="margin: 5px 0;">This is an automated notification</p>
                </div>
            </div>
            """
            
            success = self.email_service.send_email(
                to_email=email,
                subject=subject,
                html_content=html_content
            )
            
            if success:
                logger.info(f"Buyer completion email with rating link sent for {escrow_id}")
            else:
                logger.error(f"Failed to send buyer completion email for {escrow_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error sending buyer completion email for {escrow_id}: {e}")
            return False
    
    async def _send_seller_completion_email(
        self, email: str, escrow_id: str, amount: float, completion_type: str, buyer: User
    ) -> bool:
        """Send completion email to seller with rating deep link"""
        try:
            buyer_name = buyer.first_name or buyer.username or "Buyer"
            buyer_username = f"@{buyer.username}" if buyer.username else buyer_name
            amount_display = self._format_currency(amount, 'USD')
            timestamp_display = self._format_timestamp(datetime.utcnow())
            
            # Deep link to open Telegram bot and trigger rating
            rating_deep_link = f"https://t.me/{Config.BOT_USERNAME}?start=rate_{escrow_id}"
            
            if completion_type == 'released':
                subject = f"🎉 Payment Received: {amount_display} - Rate {buyer_username}"
                status_title = "Payment Received!"
                status_message = "Funds added to your wallet"
                status_color = "#28a745"
                icon = "🎉"
                rating_section = f"""
                    <div style="margin-top: 25px; padding: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 8px; text-align: center;">
                        <h3 style="color: white; margin: 0 0 10px 0;">⭐ Rate Your Buyer</h3>
                        <p style="color: white; margin: 0 0 15px 0; font-size: 14px;">How was your experience with {buyer_username}?</p>
                        <a href="{rating_deep_link}" style="display: inline-block; background: white; color: #667eea; padding: 12px 30px; border-radius: 25px; text-decoration: none; font-weight: bold; font-size: 16px;">⭐ Rate {buyer_username}</a>
                        <p style="color: rgba(255,255,255,0.8); margin: 10px 0 0 0; font-size: 12px;">Opens in Telegram</p>
                    </div>
                """
            else:  # refunded
                subject = f"↩️ Trade Refunded - {Config.PLATFORM_NAME}"
                status_title = "Trade Refunded"
                status_message = "Trade refunded to buyer"
                status_color = "#dc3545"
                icon = "↩️"
                rating_section = ""
            
            html_content = f"""
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <!-- Summary Box at Top -->
                <div style="background: #f0f8ff; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                    <strong style="color: #333;">Trade ID:</strong> <span style="font-family: monospace;">{escrow_id}</span><br>
                    <strong style="color: #333;">Amount:</strong> {amount_display}<br>
                    <strong style="color: #333;">Received:</strong> {timestamp_display}
                </div>
                
                <!-- Status Badge -->
                <div style="background: {status_color}; color: white; padding: 20px; text-align: center; border-radius: 12px 12px 0 0;">
                    <h1 style="margin: 0; font-size: 24px;">{icon} {status_title}</h1>
                    <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 14px;">{Config.PLATFORM_NAME}</p>
                </div>
                
                <div style="background: white; padding: 25px; border-radius: 0 0 12px 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                    <h2 style="margin: 0 0 15px 0; color: #333;">Payment Details</h2>
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 15px;">
                        <tr style="border-bottom: 1px solid #f0f0f0;">
                            <td style="padding: 10px 0; color: #666; font-size: 14px;">Amount</td>
                            <td style="padding: 10px 0; text-align: right; font-weight: bold; font-size: 16px;">{amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #f0f0f0;">
                            <td style="padding: 10px 0; color: #666; font-size: 14px;">Buyer</td>
                            <td style="padding: 10px 0; text-align: right; font-size: 14px;">{buyer_name}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #f0f0f0;">
                            <td style="padding: 10px 0; color: #666; font-size: 14px;">Status</td>
                            <td style="padding: 10px 0; text-align: right; font-size: 14px; color: {status_color}; font-weight: bold;">{status_message}</td>
                        </tr>
                    </table>
                    
                    <!-- What to Do Next -->
                    <div style="margin-top: 20px; padding: 15px; background: #fffbf0; border-left: 4px solid #ffc107; border-radius: 4px;">
                        <strong style="color: #333;">📋 What to Do Next:</strong>
                        <p style="margin: 10px 0 0 0; color: #666;">Funds are now in your wallet. You can cash out anytime or use them for your next trade.</p>
                    </div>
                    
                    {rating_section}
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #999; font-size: 12px;">
                    <p style="margin: 5px 0;">Thank you for using {Config.PLATFORM_NAME}</p>
                    <p style="margin: 5px 0;">This is an automated notification</p>
                </div>
            </div>
            """
            
            success = self.email_service.send_email(
                to_email=email,
                subject=subject,
                html_content=html_content
            )
            
            if success:
                logger.info(f"Seller completion email with rating link sent for {escrow_id}")
            else:
                logger.error(f"Failed to send seller completion email for {escrow_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error sending seller completion email for {escrow_id}: {e}")
            return False
    
    async def _send_dispute_resolution_email(
        self, email: str, escrow_id: str, amount: float, is_winner: bool, 
        resolution_type: str, counterpart: User, role: str, seller_contact_display: Optional[str] = None
    ) -> bool:
        """Send dispute resolution email with deep link rating button"""
        try:
            if role == "buyer":
                counterpart_name = seller_contact_display or (counterpart.first_name if counterpart else None) or (counterpart.username if counterpart else None) or "Seller"
            else:
                counterpart_name = counterpart.first_name or counterpart.username or "Buyer"
            
            counterpart_username = f"@{counterpart.username}" if counterpart.username else counterpart_name
            amount_display = self._format_currency(amount, 'USD')
            timestamp_display = self._format_timestamp(datetime.utcnow())
            
            # Deep link to open Telegram bot and trigger rating
            rating_deep_link = f"https://t.me/{Config.BOT_USERNAME}?start=rate_{escrow_id}"
            
            # Check if this is a custom split resolution
            is_custom_split = resolution_type and resolution_type.startswith('custom_split_')
            
            if is_custom_split and not is_winner:
                # Custom split: show the split details
                parts = resolution_type.split('_')
                if len(parts) >= 4:
                    buyer_percent = int(parts[2])
                    seller_percent = int(parts[3])
                    user_percent = buyer_percent if role == "buyer" else seller_percent
                    
                    subject = f"⚖️ Dispute Resolved: Split {buyer_percent}/{seller_percent} - Rate {counterpart_username}"
                    status_title = "Dispute Resolved - Custom Split"
                    status_color = "#ffc107"  # Warning/amber color for split
                    icon = "⚖️"
                    
                    status_message = f"The admin decided on a {buyer_percent}/{seller_percent} split."
                    outcome_details = f"Your portion: {amount_display} ({user_percent}%)"
                else:
                    # Fallback if parsing fails
                    subject = f"⚖️ Dispute Resolved - Split Decision - Rate {counterpart_username}"
                    status_title = "Dispute Resolved - Custom Split"
                    status_color = "#ffc107"
                    icon = "⚖️"
                    
                    status_message = "The admin decided on a custom split."
                    outcome_details = f"Your portion: {amount_display}"
                
                # Add gradient rating button
                call_to_action = f"""
                    <div style="margin-top: 25px; padding: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 8px; text-align: center;">
                        <h3 style="color: white; margin: 0 0 10px 0;">⭐ Rate Your Experience</h3>
                        <p style="color: white; margin: 0 0 15px 0; font-size: 14px;">How was your experience with {counterpart_username}?</p>
                        <a href="{rating_deep_link}" style="display: inline-block; background: white; color: #667eea; padding: 12px 30px; border-radius: 25px; text-decoration: none; font-weight: bold; font-size: 16px;">⭐ Rate {counterpart_username}</a>
                        <p style="color: rgba(255,255,255,0.8); margin: 10px 0 0 0; font-size: 12px;">Opens in Telegram</p>
                    </div>
                """
            elif is_winner:
                subject = f"✅ Dispute Resolved in Your Favor - Rate {counterpart_username}"
                status_title = "Dispute Resolved in Your Favor"
                status_color = "#28a745"
                icon = "✅"
                
                if resolution_type == 'refund':
                    status_message = "The admin resolved this dispute in your favor."
                    outcome_details = "Funds have been returned to your wallet."
                elif resolution_type == 'release':
                    status_message = "The admin resolved this dispute in your favor."
                    outcome_details = "Funds have been credited to your wallet."
                else:
                    status_message = "The admin resolved this dispute in your favor."
                    outcome_details = "Resolution has been applied to your account."
                
                # Add gradient rating button
                call_to_action = f"""
                    <div style="margin-top: 25px; padding: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 8px; text-align: center;">
                        <h3 style="color: white; margin: 0 0 10px 0;">⭐ Rate Your Experience</h3>
                        <p style="color: white; margin: 0 0 15px 0; font-size: 14px;">How was your experience with {counterpart_username}?</p>
                        <a href="{rating_deep_link}" style="display: inline-block; background: white; color: #667eea; padding: 12px 30px; border-radius: 25px; text-decoration: none; font-weight: bold; font-size: 16px;">⭐ Rate {counterpart_username}</a>
                        <p style="color: rgba(255,255,255,0.8); margin: 10px 0 0 0; font-size: 12px;">Opens in Telegram</p>
                    </div>
                """
                
            else:
                # Loser: optional rating
                subject = f"⚖️ Dispute Resolution Update - {Config.PLATFORM_NAME}"
                status_title = "Dispute Resolution Update"
                status_color = "#6c757d"
                icon = "⚖️"
                
                if resolution_type == 'refund':
                    status_message = "The admin reviewed your dispute."
                    outcome_details = "We understand this may not be the outcome you hoped for."
                elif resolution_type == 'release':
                    status_message = "The admin reviewed your dispute."
                    outcome_details = "We understand this may not be the outcome you hoped for."
                else:
                    status_message = "The admin completed the dispute review."
                    outcome_details = "We understand this may not be the outcome you hoped for."
                
                # Add optional rating button with muted styling
                call_to_action = f"""
                    <div style="margin-top: 25px; padding: 20px; background: #f8f9fa; border: 2px dashed #dee2e6; border-radius: 8px; text-align: center;">
                        <h3 style="color: #6c757d; margin: 0 0 10px 0;">⭐ Rate Your Experience (Optional)</h3>
                        <p style="color: #6c757d; margin: 0 0 15px 0; font-size: 14px;">Your feedback helps us improve.</p>
                        <a href="{rating_deep_link}" style="display: inline-block; background: #6c757d; color: white; padding: 12px 30px; border-radius: 25px; text-decoration: none; font-weight: bold; font-size: 16px;">⭐ Rate {counterpart_username}</a>
                        <p style="color: #adb5bd; margin: 10px 0 0 0; font-size: 12px;">Opens in Telegram</p>
                    </div>
                """
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <!-- Summary Box at Top -->
                <div style="background: #f0f8ff; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                    <strong style="color: #333;">Trade ID:</strong> <span style="font-family: monospace;">{escrow_id}</span><br>
                    <strong style="color: #333;">Amount:</strong> {amount_display}<br>
                    <strong style="color: #333;">Resolved:</strong> {timestamp_display}
                </div>
                
                <!-- Status Badge -->
                <div style="background: {status_color}; color: white; padding: 20px; text-align: center; border-radius: 12px 12px 0 0;">
                    <h1 style="margin: 0; font-size: 24px;">{icon} {status_title}</h1>
                    <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 14px;">{Config.PLATFORM_NAME}</p>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 12px 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                    <h2 style="margin: 0 0 15px 0; color: #333;">Dispute Resolution Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 15px;">
                        <tr style="border-bottom: 1px solid #f0f0f0;">
                            <td style="padding: 10px 0; color: #666; font-size: 14px;">Amount</td>
                            <td style="padding: 10px 0; text-align: right; font-weight: bold; font-size: 16px;">{amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #f0f0f0;">
                            <td style="padding: 10px 0; color: #666; font-size: 14px;">{"Seller" if role == "buyer" else "Buyer"}</td>
                            <td style="padding: 10px 0; text-align: right; font-size: 14px;">{counterpart_name}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #f0f0f0;">
                            <td style="padding: 10px 0; color: #666; font-size: 14px;">Resolution Type</td>
                            <td style="padding: 10px 0; text-align: right; font-size: 14px;">{resolution_type.title()}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e8f4fd; border-left: 4px solid #007bff; border-radius: 4px;">
                        <p style="margin: 0; margin-bottom: 10px;"><strong>Resolution:</strong> {status_message}</p>
                        <p style="margin: 0;">{outcome_details}</p>
                    </div>
                    
                    <!-- What to Do Next -->
                    <div style="margin-top: 20px; padding: 15px; background: #fffbf0; border-left: 4px solid #ffc107; border-radius: 4px;">
                        <strong style="color: #333;">📋 What to Do Next:</strong>
                        <p style="margin: 10px 0 0 0; color: #666;">Check your wallet balance and review the resolution details. If you have questions, contact support.</p>
                    </div>
                    
                    {call_to_action}
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #999; font-size: 12px;">
                    <p style="margin: 5px 0;">Thank you for using {Config.PLATFORM_NAME}</p>
                    <p style="margin: 5px 0;">This is an automated notification</p>
                </div>
            </div>
            """
            
            success = self.email_service.send_email(
                to_email=email,
                subject=subject,
                html_content=html_content
            )
            
            if success:
                logger.info(f"Dispute resolution email sent for {escrow_id} (winner: {is_winner}, role: {role})")
            else:
                logger.error(f"Failed to send dispute resolution email for {escrow_id} (winner: {is_winner}, role: {role})")
            
            return success
            
        except Exception as e:
            logger.error(f"Error sending dispute resolution email for {escrow_id}: {e}")
            return False
    
    async def _notify_dispute_outcome(
        self, user: User, escrow_id: str, amount: float, counterpart: User, is_winner: bool, resolution_type: str, buyer: User, seller: User, escrow_numeric_id: Optional[int] = None
    ) -> bool:
        """Send outcome-aware dispute resolution notification - UNIFIED with rating"""
        try:
            counterpart_name = counterpart.first_name or counterpart.username or "Other party"
            counterpart_username = f"@{counterpart.username}" if counterpart.username else counterpart_name
            
            # Determine if user is buyer or seller to show correct rating button
            role = "buyer" if user.id == buyer.id else "seller"
            
            # Check if this is a custom split resolution
            is_custom_split = resolution_type and resolution_type.startswith('custom_split_')
            
            if is_custom_split and not is_winner:
                # Custom split: show the split details instead of generic message
                parts = resolution_type.split('_')
                amount_display = self._format_currency(amount, 'USD')
                if len(parts) >= 4:
                    buyer_percent = int(parts[2])
                    seller_percent = int(parts[3])
                    user_percent = buyer_percent if role == "buyer" else seller_percent
                    
                    message = (
                        f"⚖️ <b>Dispute Resolved - Custom Split</b>\n"
                        f"🆔 <code>{escrow_id}</code> • Trade with {counterpart_name}\n\n"
                        f"💰 <b>Your portion: {amount_display} ({user_percent}%)</b>\n\n"
                        f"The admin decided on a {buyer_percent}/{seller_percent} split.\n\n"
                        f"💭 <b>Rate {counterpart_username}</b>\n"
                        f"Help others by sharing your experience!"
                    )
                else:
                    # Fallback if parsing fails
                    message = (
                        f"⚖️ <b>Dispute Resolved - Custom Split</b>\n"
                        f"🆔 <code>{escrow_id}</code> • Trade with {counterpart_name}\n\n"
                        f"💰 {amount_display} credited to your wallet\n\n"
                        f"💭 <b>Rate {counterpart_username}</b>\n"
                        f"Help others by sharing your experience!"
                    )
                
                # Only include rating button if we have numeric ID
                keyboard = []
                if escrow_numeric_id:
                    keyboard.append([InlineKeyboardButton(f"⭐ Rate {counterpart_username}", callback_data=f"rate_escrow_{escrow_numeric_id}")])
                keyboard.append([InlineKeyboardButton("💰 Wallet", callback_data="wallet_menu"), 
                                 InlineKeyboardButton("🏠 Menu", callback_data="main_menu")])
            elif is_winner:
                amount_display = self._format_currency(amount, 'USD')
                message = (
                    f"✅ <b>Dispute Resolved in Your Favor</b>\n"
                    f"🆔 <code>{escrow_id}</code> • Trade with {counterpart_name}\n\n"
                    f"💰 {amount_display} credited to your wallet\n\n"
                    f"💭 <b>Rate {counterpart_username}</b>\n"
                    f"Help others by sharing your experience!"
                )
                
                # Only include rating button if we have numeric ID
                keyboard = []
                if escrow_numeric_id:
                    keyboard.append([InlineKeyboardButton(f"⭐ Rate {counterpart_username}", callback_data=f"rate_escrow_{escrow_numeric_id}")])
                keyboard.append([InlineKeyboardButton("💰 Wallet", callback_data="wallet_menu"), 
                                 InlineKeyboardButton("🏠 Menu", callback_data="main_menu")])
            else:
                # Loser: make rating optional with skip button
                message = (
                    f"⚖️ <b>Dispute Resolution Update</b>\n"
                    f"🆔 <code>{escrow_id}</code> • Trade with {counterpart_name}\n\n"
                    f"The admin reviewed your dispute.\n"
                    f"We understand this may not be the outcome you hoped for.\n\n"
                    f"💭 <b>Rate {counterpart_username} (Optional)</b>\n"
                    f"Your feedback helps us improve!"
                )
                
                # Only include rating button if we have numeric ID
                keyboard = []
                if escrow_numeric_id:
                    keyboard.append([InlineKeyboardButton(f"⭐ Rate {counterpart_username}", callback_data=f"rate_escrow_{escrow_numeric_id}")])
                keyboard.append([InlineKeyboardButton("❌ Skip", callback_data="main_menu"), 
                                 InlineKeyboardButton("🏠 Menu", callback_data="main_menu")])
            
            await self.bot.send_message(
                chat_id=int(user.telegram_id),
                text=message,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            logger.info(f"Unified dispute outcome+rating notification sent for {escrow_id} (winner: {is_winner})")
            return True
            
        except TelegramError as e:
            logger.error(f"Failed to send dispute outcome notification for {escrow_id}: {e}")
            return False
    
    async def _send_dispute_rating_prompts(
        self, buyer: User, seller: User, escrow_id: str, session, 
        dispute_winner_id: int, dispute_loser_id: int, resolution_type: str
    ) -> bool:
        """Send dispute-specific rating prompts to both parties"""
        try:
            # Check if ratings already exist
            escrow = session.query(Escrow).filter(Escrow.escrow_id == escrow_id).first()
            if not escrow:
                return False
                
            buyer_rating = session.query(Rating).filter(
                Rating.escrow_id == escrow.id,
                Rating.rater_id == buyer.id,
                Rating.category == 'seller'
            ).first()
            
            seller_rating = session.query(Rating).filter(
                Rating.escrow_id == escrow.id,
                Rating.rater_id == seller.id,
                Rating.category == 'buyer'
            ).first()
            
            # Determine buyer and seller outcomes
            buyer_is_winner = (dispute_winner_id == buyer.id) if dispute_winner_id else None
            seller_is_winner = (dispute_winner_id == seller.id) if dispute_winner_id else None
            
            # Send rating prompt to buyer (buyer rates seller)
            if not buyer_rating:
                try:
                    buyer_outcome = "winner" if buyer_is_winner else "loser" if buyer_is_winner is False else "participant"
                    seller_name = seller.first_name or seller.username or "Seller"
                    
                    buyer_message = (
                        f"⭐ <b>Rate Your Experience</b>\n"
                        f"🆔 <code>{escrow_id}</code> • Dispute Resolved\n\n"
                        f"How would you rate <b>{seller_name}</b> in this trade?\n"
                        f"Your feedback helps maintain our community standards."
                    )
                    
                    buyer_keyboard = [
                        [InlineKeyboardButton("⭐ Rate Seller", callback_data=f"rate_dispute:{escrow_id}:{buyer_outcome}:{resolution_type}")],
                        [InlineKeyboardButton("❌ Skip", callback_data="main_menu")]
                    ]
                    
                    await self.bot.send_message(
                        chat_id=int(buyer.telegram_id),
                        text=buyer_message,
                        parse_mode='HTML',
                        reply_markup=InlineKeyboardMarkup(buyer_keyboard)
                    )
                    logger.info(f"Dispute rating prompt sent to buyer for {escrow_id} (outcome: {buyer_outcome})")
                except TelegramError as e:
                    logger.error(f"Failed to send dispute rating prompt to buyer for {escrow_id}: {e}")
            
            # Send rating prompt to seller (seller rates buyer)
            if not seller_rating:
                try:
                    seller_outcome = "winner" if seller_is_winner else "loser" if seller_is_winner is False else "participant"
                    buyer_name = buyer.first_name or buyer.username or "Buyer"
                    
                    seller_message = (
                        f"⭐ <b>Rate Your Experience</b>\n"
                        f"🆔 <code>{escrow_id}</code> • Dispute Resolved\n\n"
                        f"How would you rate <b>{buyer_name}</b> in this trade?\n"
                        f"Your feedback helps maintain our community standards."
                    )
                    
                    seller_keyboard = [
                        [InlineKeyboardButton("⭐ Rate Buyer", callback_data=f"rate_dispute:{escrow_id}:{seller_outcome}:{resolution_type}")],
                        [InlineKeyboardButton("❌ Skip", callback_data="main_menu")]
                    ]
                    
                    await self.bot.send_message(
                        chat_id=int(seller.telegram_id),
                        text=seller_message,
                        parse_mode='HTML',
                        reply_markup=InlineKeyboardMarkup(seller_keyboard)
                    )
                    logger.info(f"Dispute rating prompt sent to seller for {escrow_id} (outcome: {seller_outcome})")
                except TelegramError as e:
                    logger.error(f"Failed to send dispute rating prompt to seller for {escrow_id}: {e}")
            
            logger.info(
                f"Dispute rating prompts completed for {escrow_id} "
                f"(buyer_rated: {bool(buyer_rating)}, seller_rated: {bool(seller_rating)}, "
                f"winner_id: {dispute_winner_id}, loser_id: {dispute_loser_id}, type: {resolution_type})"
            )
            return True
            
        except Exception as e:
            logger.error(f"Error in dispute rating prompts for {escrow_id}: {e}")
            return False


# Helper function for easy integration
async def notify_escrow_completion(
    escrow_id: str,
    completion_type: str,
    amount: float,
    buyer_id: int,
    seller_id: int,
    buyer_email: Optional[str] = None,
    seller_email: Optional[str] = None,
    dispute_winner_id: Optional[int] = None,
    dispute_loser_id: Optional[int] = None,
    resolution_type: Optional[str] = None
) -> Dict[str, bool]:
    """Convenience function for sending post-completion notifications"""
    service = PostCompletionNotificationService()
    return await service.notify_escrow_completion(
        escrow_id=escrow_id,
        completion_type=completion_type,
        amount=amount,
        buyer_id=buyer_id,
        seller_id=seller_id,
        buyer_email=buyer_email,
        seller_email=seller_email,
        dispute_winner_id=dispute_winner_id,
        dispute_loser_id=dispute_loser_id,
        resolution_type=resolution_type
    )