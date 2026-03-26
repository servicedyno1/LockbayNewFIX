"""
Trade Acceptance Notification Service
Handles comprehensive notifications when trades are accepted and activated
"""

import logging
import html
from typing import Dict, Any
from datetime import datetime
from decimal import Decimal
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError

from services.email import EmailService
from services.welcome_email import WelcomeEmailService
from services.admin_trade_notifications import AdminTradeNotificationService
from utils.markdown_escaping import format_username_html
from config import Config
from database import SessionLocal
from models import User, Escrow

logger = logging.getLogger(__name__)


class TradeAcceptanceNotificationService:
    """Send comprehensive notifications when trades are accepted and activated"""
    
    def __init__(self):
        if not Config.BOT_TOKEN:
            raise ValueError("BOT_TOKEN not configured")
        self.bot = Bot(token=Config.BOT_TOKEN)
        self.email_service = EmailService()
        self.welcome_email_service = WelcomeEmailService()
        self.admin_notifications = AdminTradeNotificationService()
    
    async def notify_trade_acceptance(
        self,
        escrow_id: str,
        buyer_id: int,
        seller_id: int,
        amount: Decimal,
        currency: str = "USD"
    ) -> Dict[str, bool]:
        """
        Send comprehensive notifications when a trade is accepted
        
        Args:
            escrow_id: Unique escrow identifier
            buyer_id: Buyer's user ID
            seller_id: Seller's user ID
            amount: Trade amount
            currency: Currency (default: USD)
            
        Returns:
            Dict with notification results
        """
        
        results = {
            'buyer_telegram': False,
            'buyer_email': False,
            'seller_telegram': False,
            'seller_email': False,
            'seller_welcome_email': False,
            'admin_notification': False
        }
        
        try:
            session = SessionLocal()
            try:
                # Get user details
                buyer = session.query(User).filter(User.id == buyer_id).first()
                seller = session.query(User).filter(User.id == seller_id).first()
                escrow = session.query(Escrow).filter(Escrow.escrow_id == escrow_id).first()
                
                if not buyer or not seller or not escrow:
                    logger.error(f"Missing data for trade acceptance notification - Escrow: {escrow_id}")
                    return results
                
                # Send buyer notifications
                # REMOVED: Buyer Telegram notification now sent directly in handlers/escrow.py
                # with 3 action buttons (Open Chat, View Trade Details, Main Menu)
                results['buyer_telegram'] = True  # Already handled in handler
                
                if buyer.email and buyer.email_verified:
                    results['buyer_email'] = await self._send_buyer_acceptance_email(
                        buyer.email, escrow_id, amount, seller, currency, escrow
                    )
                
                # Send seller notifications
                results['seller_telegram'] = await self._notify_seller_trade_confirmed(
                    seller, escrow_id, amount, buyer, currency
                )
                
                if seller.email and seller.email_verified:
                    results['seller_email'] = await self._send_seller_confirmation_email(
                        seller.email, escrow_id, amount, buyer, currency
                    )
                    
                    # Send welcome email if this is seller's first trade
                    if self._is_first_trade(seller_id):
                        results['seller_welcome_email'] = await self._send_seller_welcome_email(
                            seller.email, seller.first_name or seller.username or "Trader", seller.id
                        )
                
                # Send admin notification
                results['admin_notification'] = await self._send_admin_trade_activation_alert(
                    escrow_id, amount, buyer, seller, currency
                )
                
                # Log comprehensive results
                success_count = sum(1 for success in results.values() if success)
                total_count = len(results)
                
                logger.info(f"✅ Trade acceptance notifications for {escrow_id}: {success_count}/{total_count} successful")
                
                if success_count < total_count:
                    failed_notifications = [k for k, v in results.items() if not v]
                    logger.warning(f"⚠️ Failed notifications for {escrow_id}: {', '.join(failed_notifications)}")
                
                return results
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error in trade acceptance notification service for {escrow_id}: {e}")
            return results
    
    async def _notify_buyer_trade_accepted(
        self, buyer: User, escrow_id: str, amount: Decimal, seller: User, currency: str, escrow: Escrow
    ) -> bool:
        """Send Telegram notification to buyer when their trade is accepted"""
        try:
            seller_username = str(seller.username) if seller.username is not None else None
            seller_display = (
                str(escrow.seller_contact_display) if escrow.seller_contact_display is not None else
                str(seller.first_name) if seller.first_name else
                str(seller.username) if seller.username else
                f"User_{seller.telegram_id}"
            )
            
            message = f"""🎉 <b>Trade Accepted!</b>

🆔 <b>Trade ID:</b> #{html.escape(escrow_id)}
💰 <b>Amount:</b> ${amount:.2f} USD
👤 <b>Seller:</b> {format_username_html(seller_username, include_link=False) if seller_username else html.escape(seller_display)}

✅ Trade is now <b>ACTIVE</b>! Seller will deliver soon.

💼 Your payment is safely held in escrow."""

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        f"📋 View Trade #{escrow_id}", 
                        callback_data=f"view_trade_{escrow_id}"
                    )
                ],
                [
                    InlineKeyboardButton("📊 My Trades", callback_data="trades_messages_hub"),
                    InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")
                ]
            ])

            await self.bot.send_message(
                chat_id=buyer.telegram_id,
                text=message,
                parse_mode="HTML",
                reply_markup=keyboard
            )
            
            logger.info(f"✅ Buyer notification sent for trade {escrow_id} to user {buyer.telegram_id}")
            return True
            
        except TelegramError as e:
            logger.error(f"❌ Failed to send buyer notification for {escrow_id}: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error sending buyer notification for {escrow_id}: {e}")
            return False
    
    async def _notify_seller_trade_confirmed(
        self, seller: User, escrow_id: str, amount: Decimal, buyer: User, currency: str
    ) -> bool:
        """
        Seller Telegram notification is sent directly in handlers/escrow.py (handle_seller_accept_trade)
        This method now only returns True to maintain compatibility with the notification flow
        Email notification to seller is handled separately via _send_seller_confirmation_email
        """
        logger.info(f"Seller Telegram notification for {escrow_id} handled in handler (no duplicate sent)")
        return True
    
    async def _send_buyer_acceptance_email(
        self, buyer_email: str, escrow_id: str, amount: Decimal, seller: User, currency: str, escrow: Escrow
    ) -> bool:
        """Send email notification to buyer about trade acceptance"""
        try:
            seller_username = str(seller.username) if seller.username is not None else None
            seller_display = (
                str(escrow.seller_contact_display) if escrow.seller_contact_display is not None else
                str(seller.first_name) if seller.first_name else
                str(seller.username) if seller.username else
                "Your Trading Partner"
            )
            seller_display_html = format_username_html(seller_username, include_link=False) if seller_username else html.escape(seller_display)
            
            subject = f"🎉 Trade Accepted - #{escrow_id} | {Config.PLATFORM_NAME}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: linear-gradient(135deg, #28a745 0%, #20c997 100%); color: white; padding: 30px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1>🎉 Trade Accepted!</h1>
                    <p>Your trade is now active and secured</p>
                </div>
                
                <div style="background: white; padding: 30px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <h2>Trade Details</h2>
                    
                    <div style="background: #e8f5e8; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #28a745;">
                        <table style="width: 100%;">
                            <tr><td style="font-weight: bold;">Trade ID:</td><td>#{html.escape(escrow_id)}</td></tr>
                            <tr><td style="font-weight: bold;">Amount:</td><td>${amount:.2f} USD</td></tr>
                            <tr><td style="font-weight: bold;">Seller:</td><td>{seller_display_html}</td></tr>
                            <tr><td style="font-weight: bold;">Status:</td><td><span style="color: #28a745; font-weight: bold;">✅ ACTIVE</span></td></tr>
                        </table>
                    </div>
                    
                    <h3>What happens next?</h3>
                    <ul>
                        <li><strong>Secure Payment:</strong> Your ${amount:.2f} USD payment is safely held in escrow</li>
                        <li><strong>Service Delivery:</strong> The seller will now deliver the agreed service/product</li>
                        <li><strong>Completion:</strong> You'll be notified when delivery is complete</li>
                        <li><strong>Release Funds:</strong> Confirm delivery and release payment to complete the trade</li>
                    </ul>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="https://t.me/{Config.BOT_USERNAME}" style="background: #28a745; color: white; padding: 15px 30px; text-decoration: none; border-radius: 6px; font-weight: 600;">
                            View Trade in Bot
                        </a>
                    </div>
                    
                    <div style="background: #f8f9fa; padding: 15px; border-radius: 6px; margin-top: 20px;">
                        <p style="margin: 0; color: #6c757d; font-size: 14px;">
                            <strong>Security Note:</strong> Your funds are protected by escrow until you confirm delivery and release payment.
                        </p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p>© 2025 {Config.PLATFORM_NAME}. Secure trading platform.</p>
                </div>
            </div>
            """
            
            seller_display_plain = (
                str(escrow.seller_contact_display) if escrow.seller_contact_display is not None else
                str(seller.first_name) if seller.first_name else
                str(seller.username) if seller.username else
                "Your Trading Partner"
            )
            
            text_content = f"""
Trade Accepted - #{escrow_id}

Your trade has been accepted and is now ACTIVE!

Trade Details:
- Trade ID: #{escrow_id}
- Amount: ${amount:.2f} USD
- Seller: {seller_display_plain}
- Status: ACTIVE

Your payment is safely held in escrow. The seller will now deliver the service/product.

Access your trade: https://t.me/{Config.BOT_USERNAME}

{Config.PLATFORM_NAME} - Secure Trading Platform
            """
            
            success = self.email_service.send_email(
                to_email=buyer_email,
                subject=subject,
                text_content=text_content,
                html_content=html_content
            )
            
            if success:
                logger.info(f"✅ Buyer acceptance email sent for {escrow_id} to {buyer_email}")
            else:
                logger.error(f"❌ Failed to send buyer acceptance email for {escrow_id}")
                
            return success
            
        except Exception as e:
            logger.error(f"❌ Error sending buyer acceptance email for {escrow_id}: {e}")
            return False
    
    async def _send_seller_confirmation_email(
        self, seller_email: str, escrow_id: str, amount: Decimal, buyer: User, currency: str
    ) -> bool:
        """Send email confirmation to seller about trade acceptance"""
        try:
            buyer_username = buyer.username or None
            buyer_display = buyer.first_name or buyer.username or "Your Trading Partner"
            buyer_display_html = format_username_html(buyer_username, include_link=False) if buyer_username else html.escape(buyer_display)
            
            subject = f"✅ Trade Confirmed - #{escrow_id} | {Config.PLATFORM_NAME}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: linear-gradient(135deg, #007bff 0%, #6f42c1 100%); color: white; padding: 30px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1>✅ Trade Confirmed!</h1>
                    <p>Your trade is active - time to deliver!</p>
                </div>
                
                <div style="background: white; padding: 30px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <h2>Trade Details</h2>
                    
                    <div style="background: #e7f3ff; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #007bff;">
                        <table style="width: 100%;">
                            <tr><td style="font-weight: bold;">Trade ID:</td><td>#{html.escape(escrow_id)}</td></tr>
                            <tr><td style="font-weight: bold;">Amount:</td><td>${amount:.2f} USD</td></tr>
                            <tr><td style="font-weight: bold;">Buyer:</td><td>{buyer_display_html}</td></tr>
                            <tr><td style="font-weight: bold;">Status:</td><td><span style="color: #007bff; font-weight: bold;">🚀 ACTIVE</span></td></tr>
                        </table>
                    </div>
                    
                    <h3>Your Next Steps:</h3>
                    <ol>
                        <li><strong>Deliver Service:</strong> Provide the agreed service/product to the buyer</li>
                        <li><strong>Mark Complete:</strong> Use the bot to mark delivery as complete</li>
                        <li><strong>Get Paid:</strong> Receive payment once buyer confirms delivery</li>
                    </ol>
                    
                    <div style="background: #fff3cd; padding: 15px; border-radius: 6px; margin: 20px 0; border-left: 4px solid #ffc107;">
                        <p style="margin: 0; color: #856404;">
                            <strong>💰 Payment Secured:</strong> The buyer's ${amount:.2f} USD payment is safely held in escrow and will be released to you upon completion.
                        </p>
                    </div>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="https://t.me/{Config.BOT_USERNAME}" style="background: #007bff; color: white; padding: 15px 30px; text-decoration: none; border-radius: 6px; font-weight: 600;">
                            Access Trade
                        </a>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p>© 2025 {Config.PLATFORM_NAME}. Secure trading platform.</p>
                </div>
            </div>
            """
            
            buyer_display_plain = buyer.first_name or buyer.username or "Your Trading Partner"
            
            text_content = f"""
Trade Confirmed - #{escrow_id}

Your trade has been confirmed and is now ACTIVE!

Trade Details:
- Trade ID: #{escrow_id}
- Amount: ${amount:.2f} USD
- Buyer: {buyer_display_plain}
- Status: ACTIVE

Next Steps:
1. Deliver the agreed service/product
2. Mark delivery as complete in the bot
3. Receive payment once buyer confirms

Payment Secured: ${amount:.2f} USD is held in escrow for you.

Access your trade: https://t.me/{Config.BOT_USERNAME}

{Config.PLATFORM_NAME} - Secure Trading Platform
            """
            
            success = self.email_service.send_email(
                to_email=seller_email,
                subject=subject,
                text_content=text_content,
                html_content=html_content
            )
            
            if success:
                logger.info(f"✅ Seller confirmation email sent for {escrow_id} to {seller_email}")
            else:
                logger.error(f"❌ Failed to send seller confirmation email for {escrow_id}")
                
            return success
            
        except Exception as e:
            logger.error(f"❌ Error sending seller confirmation email for {escrow_id}: {e}")
            return False
    
    async def _send_seller_welcome_email(self, seller_email: str, seller_name: str, seller_id: int) -> bool:
        """Send welcome email to new seller"""
        try:
            success = await self.welcome_email_service.send_welcome_email(
                user_email=seller_email,
                user_name=seller_name,
                user_id=seller_id,
                include_agreement_pdf=True
            )
            
            if success:
                logger.info(f"✅ Welcome email sent to new seller {seller_email}")
            else:
                logger.warning(f"⚠️ Welcome email failed for new seller {seller_email}")
                
            return success
            
        except Exception as e:
            logger.error(f"❌ Error sending welcome email to {seller_email}: {e}")
            return False
    
    async def _send_admin_trade_activation_alert(
        self, escrow_id: str, amount: Decimal, buyer: User, seller: User, currency: str
    ) -> bool:
        """Send admin notification about trade activation"""
        try:
            buyer_info = buyer.first_name or buyer.username or f"User_{buyer.telegram_id}"
            seller_info = seller.first_name or seller.username or f"User_{seller.telegram_id}"
            
            activation_data = {
                'escrow_id': escrow_id,
                'amount': amount,
                'currency': currency,
                'buyer_info': f"{buyer_info} (ID: {buyer.id})",
                'seller_info': f"{seller_info} (ID: {seller.id})",
                'activated_at': datetime.utcnow(),
                'buyer_email': buyer.email or 'Not provided',
                'seller_email': seller.email or 'Not provided'
            }
            
            # Use existing admin notification service with custom activation method
            success = await self._send_custom_admin_activation_alert(activation_data)
            
            if success:
                logger.info(f"✅ Admin activation alert sent for {escrow_id}")
            else:
                logger.warning(f"⚠️ Admin activation alert failed for {escrow_id}")
                
            return success
            
        except Exception as e:
            logger.error(f"❌ Error sending admin activation alert for {escrow_id}: {e}")
            return False
    
    async def _send_custom_admin_activation_alert(self, activation_data: Dict[str, Any]) -> bool:
        """Send custom admin email for trade activation"""
        try:
            if not Config.ADMIN_EMAIL:
                logger.warning("Admin email not configured - skipping activation notification")
                return False
                
            escrow_id = activation_data.get('escrow_id', 'Unknown')
            amount = activation_data.get('amount', 0)
            activation_data.get('currency', 'USD')
            buyer_info = activation_data.get('buyer_info', 'Unknown')
            seller_info = activation_data.get('seller_info', 'Unknown')
            activated_at = activation_data.get('activated_at', datetime.utcnow())
            
            subject = f"🚀 Trade Activated - {escrow_id} | {Config.PLATFORM_NAME}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: linear-gradient(135deg, #17a2b8 0%, #6f42c1 100%); color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1>🚀 Trade Activated</h1>
                    <p>{Config.PLATFORM_NAME} Admin Alert</p>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <h2>Trade Activation Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Trade ID:</td>
                            <td style="padding: 8px;">{escrow_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Amount:</td>
                            <td style="padding: 8px;">${amount:.2f} USD</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Buyer:</td>
                            <td style="padding: 8px;">{buyer_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Seller:</td>
                            <td style="padding: 8px;">{seller_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Activated:</td>
                            <td style="padding: 8px;">{activated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Buyer Email:</td>
                            <td style="padding: 8px;">{activation_data.get('buyer_email', 'Not provided')}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Seller Email:</td>
                            <td style="padding: 8px;">{activation_data.get('seller_email', 'Not provided')}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e1f5fe; border-left: 4px solid #17a2b8; border-radius: 4px;">
                        <p style="margin: 0;"><strong>Status:</strong> Trade is now ACTIVE - seller will deliver service and buyer will release payment upon completion</p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p>This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            text_content = f"""
Trade Activated - {escrow_id}

A trade has been accepted and is now active on {Config.PLATFORM_NAME}.

Trade Details:
- Trade ID: {escrow_id}
- Amount: ${amount:.2f} USD
- Buyer: {buyer_info}
- Seller: {seller_info}
- Activated: {activated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}

Status: Trade is now ACTIVE - seller will deliver service and buyer will release payment upon completion.

This is an automated notification from {Config.PLATFORM_NAME}.
            """
            
            success = self.email_service.send_email(
                to_email=Config.ADMIN_EMAIL,
                subject=subject,
                text_content=text_content,
                html_content=html_content
            )
            
            return success
            
        except Exception as e:
            logger.error(f"Error sending custom admin activation alert: {e}")
            return False
    
    def _is_first_trade(self, seller_id: int) -> bool:
        """Check if this is the seller's first trade"""
        try:
            session = SessionLocal()
            try:
                # Count completed trades for this seller
                completed_trades = session.query(Escrow).filter(
                    Escrow.seller_id == seller_id,
                    Escrow.status.in_(['completed', 'released'])
                ).count()
                
                return completed_trades == 0
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error checking first trade status for seller {seller_id}: {e}")
            return True  # Default to sending welcome email if unsure


# Global instance
trade_acceptance_notifications = TradeAcceptanceNotificationService()