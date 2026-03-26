"""
Rating Reminder Service
Sends follow-up reminders to users who haven't rated completed trades
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from config import Config
from database import SessionLocal
from models import User, Escrow, Rating, EscrowStatus
from services.email import EmailService

logger = logging.getLogger(__name__)


class RatingReminderService:
    """Service to send rating reminders for unrated completed trades"""
    
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
    
    @staticmethod
    async def process_rating_reminders() -> Dict[str, Any]:
        """
        Main entry point - find completed trades without ratings and send reminders
        Sends reminders for trades completed 24 hours ago and 7 days ago
        """
        results = {
            'reminders_sent': 0,
            'email_reminders_sent': 0,
            'errors': [],
            'processed_trades': []
        }
        
        try:
            session = SessionLocal()
            service = RatingReminderService()
            
            try:
                # Find completed trades without ratings (24 hours and 7 days old)
                now = datetime.utcnow()
                
                # 24 hour reminder window (23-25 hours ago)
                day_reminder_start = now - timedelta(hours=25)
                day_reminder_end = now - timedelta(hours=23)
                
                # 7 day reminder window (6.5-7.5 days ago)
                week_reminder_start = now - timedelta(days=7.5)
                week_reminder_end = now - timedelta(days=6.5)
                
                # Find completed escrows in reminder windows
                completed_escrows = session.query(Escrow).filter(
                    Escrow.status == EscrowStatus.COMPLETED.value,
                    Escrow.completed_at.isnot(None),
                    (
                        (Escrow.completed_at.between(day_reminder_start, day_reminder_end)) |
                        (Escrow.completed_at.between(week_reminder_start, week_reminder_end))
                    )
                ).all()
                
                logger.info(f"Found {len(completed_escrows)} completed escrows eligible for rating reminders")
                
                for escrow in completed_escrows:
                    try:
                        # Check if buyer has rated seller
                        buyer_rating = session.query(Rating).filter(
                            Rating.escrow_id == escrow.id,
                            Rating.rater_id == escrow.buyer_id,
                            Rating.category == 'seller'
                        ).first()
                        
                        # Check if seller has rated buyer
                        seller_rating = session.query(Rating).filter(
                            Rating.escrow_id == escrow.id,
                            Rating.rater_id == escrow.seller_id,
                            Rating.category == 'buyer'
                        ).first()
                        
                        # Get users
                        buyer = session.query(User).filter(User.id == escrow.buyer_id).first()
                        seller = session.query(User).filter(User.id == escrow.seller_id).first()
                        
                        if not buyer or not seller:
                            continue
                        
                        # Send reminders to users who haven't rated
                        if not buyer_rating:
                            buyer_sent = await service._send_buyer_rating_reminder(
                                buyer, escrow, seller, session
                            )
                            if buyer_sent:
                                results['reminders_sent'] += 1
                                
                                # Send email reminder if email verified
                                if buyer.email and buyer.email_verified:
                                    email_sent = await service._send_buyer_rating_email_reminder(
                                        buyer, escrow, seller
                                    )
                                    if email_sent:
                                        results['email_reminders_sent'] += 1
                        
                        if not seller_rating:
                            seller_sent = await service._send_seller_rating_reminder(
                                seller, escrow, buyer, session
                            )
                            if seller_sent:
                                results['reminders_sent'] += 1
                                
                                # Send email reminder if email verified
                                if seller.email and seller.email_verified:
                                    email_sent = await service._send_seller_rating_email_reminder(
                                        seller, escrow, buyer
                                    )
                                    if email_sent:
                                        results['email_reminders_sent'] += 1
                        
                        results['processed_trades'].append({
                            'escrow_id': escrow.escrow_id,
                            'buyer_reminded': not buyer_rating,
                            'seller_reminded': not seller_rating,
                            'completed_at': escrow.completed_at.isoformat()
                        })
                        
                    except Exception as trade_error:
                        logger.error(f"Error processing rating reminder for escrow {escrow.escrow_id}: {trade_error}")
                        results['errors'].append(f"Trade {escrow.escrow_id}: {str(trade_error)}")
                
                logger.info(f"Rating reminders processed: {results['reminders_sent']} Telegram, {results['email_reminders_sent']} email")
                return results
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error in rating reminder service: {e}")
            results['errors'].append(str(e))
            return results
    
    async def _send_buyer_rating_reminder(
        self, buyer: User, escrow: Escrow, seller: User, session
    ) -> bool:
        """Send Telegram rating reminder to buyer"""
        try:
            seller_contact_display = str(escrow.seller_contact_display) if escrow.seller_contact_display else None
            seller_name = seller_contact_display or seller.first_name or seller.username or "Seller"
            completed_at = escrow.completed_at if isinstance(escrow.completed_at, datetime) else datetime.utcnow()
            days_ago = (datetime.utcnow() - completed_at).days
            amount = float(escrow.amount) if escrow.amount else 0.0
            amount_display = self._format_currency(amount, 'USD')
            
            message = (
                f"⭐ <b>Rate: {amount_display} trade with {seller_name}</b>\n"
                f"🆔 <code>{escrow.escrow_id}</code> • {days_ago} day{'s' if days_ago != 1 else ''} ago\n\n"
                "🌟 Help build trust!"
            )
            
            keyboard = [
                [InlineKeyboardButton("⭐ Rate this Seller", callback_data=f"rate_seller_{escrow.id}")],
                [InlineKeyboardButton("📊 View My Trades", callback_data="trades_messages_hub")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]
            
            await self.bot.send_message(
                chat_id=int(buyer.telegram_id),
                text=message,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            logger.info(f"Buyer rating reminder sent for escrow {escrow.escrow_id}")
            return True
            
        except TelegramError as e:
            logger.error(f"Failed to send buyer rating reminder for escrow {escrow.escrow_id}: {e}")
            return False
    
    async def _send_seller_rating_reminder(
        self, seller: User, escrow: Escrow, buyer: User, session
    ) -> bool:
        """Send Telegram rating reminder to seller"""
        try:
            buyer_name = buyer.first_name or buyer.username or "Buyer"
            completed_at = escrow.completed_at if isinstance(escrow.completed_at, datetime) else datetime.utcnow()
            days_ago = (datetime.utcnow() - completed_at).days
            amount = float(escrow.amount) if escrow.amount else 0.0
            amount_display = self._format_currency(amount, 'USD')
            
            message = (
                f"⭐ <b>Rate: {amount_display} trade with {buyer_name}</b>\n"
                f"🆔 <code>{escrow.escrow_id}</code> • {days_ago} day{'s' if days_ago != 1 else ''} ago\n\n"
                "🌟 Build your reputation!"
            )
            
            keyboard = [
                [InlineKeyboardButton("⭐ Rate this Buyer", callback_data=f"rate_buyer_{escrow.id}")],
                [InlineKeyboardButton("📊 View My Trades", callback_data="trades_messages_hub")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]
            
            await self.bot.send_message(
                chat_id=int(seller.telegram_id),
                text=message,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            logger.info(f"Seller rating reminder sent for escrow {escrow.escrow_id}")
            return True
            
        except TelegramError as e:
            logger.error(f"Failed to send seller rating reminder for escrow {escrow.escrow_id}: {e}")
            return False
    
    async def _send_buyer_rating_email_reminder(
        self, buyer: User, escrow: Escrow, seller: User
    ) -> bool:
        """Send email rating reminder to buyer"""
        try:
            seller_contact_display = str(escrow.seller_contact_display) if escrow.seller_contact_display else None
            seller_name = seller_contact_display or seller.first_name or seller.username or "Seller"
            completed_at = escrow.completed_at if isinstance(escrow.completed_at, datetime) else datetime.utcnow()
            days_ago = (datetime.utcnow() - completed_at).days
            amount = float(escrow.amount) if escrow.amount else 0.0
            amount_display = self._format_currency(amount, 'USD')
            timestamp_display = self._format_timestamp(completed_at)
            
            subject = f"⭐ Rate Your Recent Trade: {amount_display} - {Config.PLATFORM_NAME}"
            
            html_content = f"""
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <!-- Summary Box at Top -->
                <div style="background: #f0f8ff; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                    <strong style="color: #333;">Trade ID:</strong> <span style="font-family: monospace;">{escrow.escrow_id}</span><br>
                    <strong style="color: #333;">Amount:</strong> {amount_display}<br>
                    <strong style="color: #333;">Completed:</strong> {timestamp_display}
                </div>
                
                <!-- Status Badge -->
                <div style="background: #ffc107; color: white; padding: 20px; text-align: center; border-radius: 12px 12px 0 0;">
                    <h1 style="margin: 0; font-size: 24px;">⭐ Rate Your Recent Trade</h1>
                    <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 14px;">{Config.PLATFORM_NAME}</p>
                </div>
                
                <div style="background: white; padding: 25px; border-radius: 0 0 12px 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                    <h2 style="margin: 0 0 15px 0; color: #333;">Hi {buyer.first_name or 'there'}!</h2>
                    
                    <p style="margin: 0 0 20px 0; color: #666; font-size: 16px;">You completed a trade with <strong>{seller_name}</strong> {days_ago} day{'s' if days_ago != 1 else ''} ago.</p>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 15px;">
                        <tr style="border-bottom: 1px solid #f0f0f0;">
                            <td style="padding: 10px 0; color: #666; font-size: 14px;">Trade Amount</td>
                            <td style="padding: 10px 0; text-align: right; font-weight: bold; font-size: 16px;">{amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #f0f0f0;">
                            <td style="padding: 10px 0; color: #666; font-size: 14px;">Seller</td>
                            <td style="padding: 10px 0; text-align: right; font-size: 14px;">{seller_name}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #fff3cd; border-left: 4px solid #ffc107; border-radius: 4px;">
                        <p style="margin: 0; font-weight: bold; color: #333;">🌟 Help other buyers by sharing your experience!</p>
                        <p style="margin: 10px 0 0 0; color: #666;">Your rating helps build trust in our community and guides future buyers.</p>
                    </div>
                    
                    <!-- What to Do Next -->
                    <div style="margin-top: 20px; padding: 15px; background: #fffbf0; border-left: 4px solid #17a2b8; border-radius: 4px;">
                        <strong style="color: #333;">📋 What to Do Next:</strong>
                        <p style="margin: 10px 0 0 0; color: #666;">Open the {Config.PLATFORM_NAME} bot on Telegram to rate this seller. It only takes a minute!</p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #999; font-size: 12px;">
                    <p style="margin: 5px 0;">Thank you for using {Config.PLATFORM_NAME}</p>
                    <p style="margin: 5px 0;">This is an automated reminder</p>
                </div>
            </div>
            """
            
            if not buyer.email:
                logger.warning(f"Cannot send email reminder to buyer {buyer.id} - no email address")
                return False
            
            success = self.email_service.send_email(
                to_email=buyer.email,
                subject=subject,
                html_content=html_content
            )
            
            if success:
                logger.info(f"Buyer rating email reminder sent for escrow {escrow.escrow_id}")
            else:
                logger.error(f"Failed to send buyer rating email reminder for escrow {escrow.escrow_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error sending buyer rating email reminder for escrow {escrow.escrow_id}: {e}")
            return False
    
    async def _send_seller_rating_email_reminder(
        self, seller: User, escrow: Escrow, buyer: User
    ) -> bool:
        """Send email rating reminder to seller"""
        try:
            buyer_name = buyer.first_name or buyer.username or "Buyer"
            completed_at = escrow.completed_at if isinstance(escrow.completed_at, datetime) else datetime.utcnow()
            days_ago = (datetime.utcnow() - completed_at).days
            amount = float(escrow.amount) if escrow.amount else 0.0
            amount_display = self._format_currency(amount, 'USD')
            timestamp_display = self._format_timestamp(completed_at)
            
            subject = f"⭐ Rate Your Recent Trade: {amount_display} - {Config.PLATFORM_NAME}"
            
            html_content = f"""
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <!-- Summary Box at Top -->
                <div style="background: #f0f8ff; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                    <strong style="color: #333;">Trade ID:</strong> <span style="font-family: monospace;">{escrow.escrow_id}</span><br>
                    <strong style="color: #333;">Amount:</strong> {amount_display}<br>
                    <strong style="color: #333;">Completed:</strong> {timestamp_display}
                </div>
                
                <!-- Status Badge -->
                <div style="background: #17a2b8; color: white; padding: 20px; text-align: center; border-radius: 12px 12px 0 0;">
                    <h1 style="margin: 0; font-size: 24px;">⭐ Rate Your Recent Trade</h1>
                    <p style="margin: 5px 0 0 0; opacity: 0.9; font-size: 14px;">{Config.PLATFORM_NAME}</p>
                </div>
                
                <div style="background: white; padding: 25px; border-radius: 0 0 12px 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
                    <h2 style="margin: 0 0 15px 0; color: #333;">Hi {seller.first_name or 'there'}!</h2>
                    
                    <p style="margin: 0 0 20px 0; color: #666; font-size: 16px;">You completed a trade with <strong>{buyer_name}</strong> {days_ago} day{'s' if days_ago != 1 else ''} ago.</p>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 15px;">
                        <tr style="border-bottom: 1px solid #f0f0f0;">
                            <td style="padding: 10px 0; color: #666; font-size: 14px;">Trade Amount</td>
                            <td style="padding: 10px 0; text-align: right; font-weight: bold; font-size: 16px;">{amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #f0f0f0;">
                            <td style="padding: 10px 0; color: #666; font-size: 14px;">Buyer</td>
                            <td style="padding: 10px 0; text-align: right; font-size: 14px;">{buyer_name}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #d1ecf1; border-left: 4px solid #17a2b8; border-radius: 4px;">
                        <p style="margin: 0; font-weight: bold; color: #333;">🌟 Rate your buyer experience!</p>
                        <p style="margin: 10px 0 0 0; color: #666;">Help other sellers and build your reputation in our community.</p>
                    </div>
                    
                    <!-- What to Do Next -->
                    <div style="margin-top: 20px; padding: 15px; background: #fffbf0; border-left: 4px solid #ffc107; border-radius: 4px;">
                        <strong style="color: #333;">📋 What to Do Next:</strong>
                        <p style="margin: 10px 0 0 0; color: #666;">Open the {Config.PLATFORM_NAME} bot on Telegram to rate this buyer. It only takes a minute!</p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #999; font-size: 12px;">
                    <p style="margin: 5px 0;">Thank you for using {Config.PLATFORM_NAME}</p>
                    <p style="margin: 5px 0;">This is an automated reminder</p>
                </div>
            </div>
            """
            
            if not seller.email:
                logger.warning(f"Cannot send email reminder to seller {seller.id} - no email address")
                return False
            
            success = self.email_service.send_email(
                to_email=seller.email,
                subject=subject,
                html_content=html_content
            )
            
            if success:
                logger.info(f"Seller rating email reminder sent for escrow {escrow.escrow_id}")
            else:
                logger.error(f"Failed to send seller rating email reminder for escrow {escrow.escrow_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error sending seller rating email reminder for escrow {escrow.escrow_id}: {e}")
            return False


# Convenience function for scheduler
async def process_rating_reminders() -> Dict[str, Any]:
    """Convenience function for rating reminder processing"""
    return await RatingReminderService.process_rating_reminders()