"""
Consolidated Notification Service
Provides unified notification functionality for the escrow platform
"""

import logging

logger = logging.getLogger(__name__)


class ConsolidatedNotificationService:
    """Unified notification service for all platform communications"""
    
    def __init__(self):
        self.initialized = False
        
    async def initialize(self):
        """Initialize the notification service"""
        try:
            logger.info("🔔 Consolidated notification service initializing...")
            self.initialized = True
            logger.info("✅ Consolidated notification service ready")
        except Exception as e:
            logger.error(f"❌ Failed to initialize notification service: {e}")
    
    async def send_telegram_notification(self, user_id: int, message: str, **kwargs):
        """Send Telegram notification to user"""
        try:
            # Implementation will be added as needed
            logger.debug(f"📱 Telegram notification to {user_id}: {message[:50]}...")
        except Exception as e:
            logger.error(f"❌ Failed to send Telegram notification: {e}")
    
    async def send_telegram_message(self, bot, user_id: int, message: str, **kwargs):
        """Send Telegram message using provided bot instance"""
        try:
            logger.debug(f"📱 Sending Telegram message to {user_id}: {message[:50]}...")
            await bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=kwargs.get('parse_mode', 'Markdown')
            )
            logger.debug(f"✅ Telegram message sent to {user_id}")
        except Exception as e:
            logger.error(f"❌ Failed to send Telegram message to {user_id}: {e}")
            raise e
    
    async def send_email_notification(self, user_id: int, subject: str = "", message: str = "", **kwargs):
        """Send email notification to user"""
        try:
            # Implementation will be added as needed
            logger.debug(f"📧 Email notification to {user_id}: {subject}")
        except Exception as e:
            logger.error(f"❌ Failed to send email notification: {e}")
            
    async def send_delivery_notification(self, escrow):
        """Send delivery confirmation notification - SAFE TRANSACTION PATTERN"""
        logger.info(f"📦 Delivery notification for escrow {escrow.escrow_id}")
        
        try:
            from utils.atomic_transactions import atomic_transaction
            from models import User
            from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
            from config import Config
            
            # SAFE PATTERN: First, extract data in separate, quick transaction
            user_data = None
            with atomic_transaction() as session:
                buyer = session.query(User).filter(User.id == escrow.buyer_id).first()
                seller = session.query(User).filter(User.id == escrow.seller_id).first()
                
                if not buyer:
                    logger.error(f"❌ Buyer not found for escrow {escrow.escrow_id}")
                    return
                
                # Extract all needed data while in transaction
                user_data = {
                    'buyer_telegram_id': buyer.telegram_id,
                    'seller_name': seller.first_name or seller.username or "Seller" if seller else "Seller",
                    'escrow_id': escrow.escrow_id,
                    'escrow_db_id': escrow.id,
                    'amount': float(escrow.amount)
                }
            # Transaction committed here - database lock released
            
            # SAFE PATTERN: Now send notifications OUTSIDE transaction context
            if not user_data:
                return
                
            if Config.BOT_TOKEN and user_data['buyer_telegram_id']:
                try:
                    # Create compact mobile-friendly notification message
                    message = "📦 **Item Delivered**\n\n"
                    message += f"**Trade #{user_data['escrow_id'][-6:]}** • ${user_data['amount']:.2f} USD\n"
                    message += f"Seller: {user_data['seller_name']}\n\n"
                    message += "✅ Item marked as delivered\n"
                    message += "Please release funds to complete"
                    
                    # Create compact mobile-responsive action buttons
                    keyboard = [
                        [InlineKeyboardButton("✅ Release Funds", callback_data=f"release_funds_{user_data['escrow_db_id']}")],
                        [
                            InlineKeyboardButton("📋 View Trade", callback_data=f"view_trade_{user_data['escrow_db_id']}"),
                            InlineKeyboardButton("💬 Support", callback_data="start_support_chat")
                        ]
                    ]
                    
                    # Send Telegram notification
                    bot = Bot(token=Config.BOT_TOKEN)
                    await bot.send_message(
                        chat_id=user_data['buyer_telegram_id'],
                        text=message,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode="Markdown"
                    )
                    
                    logger.info(f"✅ Delivery notification sent to buyer {user_data['buyer_telegram_id']} for escrow {user_data['escrow_id']}")
                except Exception as telegram_error:
                    logger.error(f"❌ Failed to send delivery notification Telegram message: {telegram_error}")
            else:
                logger.info(f"📱 Skipping Telegram notification: BOT_TOKEN={bool(Config.BOT_TOKEN)}, telegram_id={bool(user_data.get('buyer_telegram_id'))}")
                
        except Exception as e:
            logger.error(f"❌ Failed to send delivery notification: {e}")
        
    async def send_funds_released(self, escrow):
        """Send funds released notification"""
        logger.info(f"💰 Funds released notification for escrow {escrow.escrow_id}")
        
    async def send_escrow_payment_confirmation(self, user_id: int, amount_crypto: str, currency: str, amount_usd: float = None, txid: str = None) -> bool:
        """Send escrow payment confirmation via both Telegram and email - SAFE TRANSACTION PATTERN"""
        try:
            from utils.atomic_transactions import atomic_transaction
            from models import User
            from telegram import Bot
            from services.email import EmailService
            from config import Config
            
            # SAFE PATTERN: First, extract data in separate, quick transaction
            user_data = None
            with atomic_transaction() as session:
                user = session.query(User).filter(User.id == user_id).first()
                if not user:
                    logger.warning(f"User {user_id} not found for escrow payment confirmation")
                    return False
                
                # Extract all needed user data while in transaction
                user_data = {
                    'telegram_id': user.telegram_id,
                    'email': user.email,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'username': user.username
                }
            # Transaction committed here - database lock released
            
            # SAFE PATTERN: Now send notifications OUTSIDE transaction context
            if not user_data:
                return False
                
            # Initialize notification status flags
            telegram_sent = False
            email_sent = False
            
            # Attempt Telegram notification if prerequisites are met
            if Config.BOT_TOKEN and user_data['telegram_id']:
                try:
                    # Create escrow payment confirmation message
                    if amount_usd is not None:
                        message = (
                            f"✅ **Escrow Payment Confirmed**\n\n"
                            f"**{amount_crypto} {currency}** (${amount_usd:.2f} USD)\n"
                            f"Transaction: {txid[:8]}...{txid[-4:]}\n\n"
                            f"📋 Your escrow payment has been confirmed. Waiting for seller to accept the trade.\n\n"
                            f"/trades to view status"
                        )
                    else:
                        # Safe message without USD when rate unavailable
                        message = (
                            f"✅ **Escrow Payment Confirmed**\n\n"
                            f"**{amount_crypto} {currency}**\n"
                            f"Transaction: {txid[:8]}...{txid[-4:]}\n\n"
                            f"📋 Your escrow payment has been confirmed. Waiting for seller to accept the trade.\n\n"
                            f"/trades to view status"
                        )
                    
                    # Send Telegram notification
                    bot = Bot(Config.BOT_TOKEN)
                    await bot.send_message(
                        chat_id=int(user_data['telegram_id']),
                        text=message,
                        parse_mode='Markdown'
                    )
                    telegram_sent = True
                    logger.info(f"✅ Escrow payment Telegram confirmation sent to user {user_id}")
                    
                except Exception as telegram_error:
                    logger.error(f"❌ Failed to send Telegram escrow payment confirmation to user {user_id}: {telegram_error}")
            else:
                logger.info(f"📱 Skipping Telegram notification for user {user_id}: BOT_TOKEN={bool(Config.BOT_TOKEN)}, telegram_id={bool(user_data['telegram_id'])}")
            
            # Attempt email notification if user has email
            if user_data['email'] and user_data['email'].strip():
                try:
                    email_service = EmailService()
                    user_name = f"{user_data['first_name'] or ''} {user_data['last_name'] or ''}".strip() or user_data['username'] or "User"
                    
                    success = await self._send_escrow_payment_email(
                        email_service, user_data['email'], user_name, amount_crypto, currency, amount_usd, txid
                    )
                    
                    if success:
                        email_sent = True
                        logger.info(f"✅ Escrow payment confirmation email sent to {user_data['email']}")
                    else:
                        logger.warning(f"❌ Failed to send escrow payment confirmation email to {user_data['email']}")
                        
                except Exception as email_error:
                    logger.error(f"❌ Exception sending escrow payment email to user {user_id}: {email_error}")
            else:
                logger.info(f"📧 Skipping email notification for user {user_id}: email available={bool(user_data['email'] and user_data['email'].strip())}")
            
            # Return True if at least one channel succeeded
            if telegram_sent or email_sent:
                logger.info(f"✅ Escrow payment confirmation delivered to user {user_id} (telegram_sent={telegram_sent}, email_sent={email_sent})")
                return True
            else:
                logger.warning(f"❌ Failed to deliver escrow payment confirmation to user {user_id} via any channel (telegram_sent={telegram_sent}, email_sent={email_sent})")
                return False
                
        except Exception as e:
            logger.error(f"❌ Failed to send escrow payment confirmation: {e}")
            return False
    
    async def _send_escrow_payment_email(self, email_service, user_email: str, user_name: str, amount_crypto: str, currency: str, amount_usd: float = None, txid: str = None) -> bool:
        """Send email confirmation for escrow payment"""
        try:
            from config import Config
            
            subject = f"Escrow Payment Confirmed - {Config.BRAND}"
            
            # Create HTML email content
            usd_display = f"${amount_usd:.2f} USD" if amount_usd is not None else "Rate unavailable"
            
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Escrow Payment Confirmed</title>
                <style>
                    body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; background-color: #f8f9fa; }}
                    .container {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; }}
                    .header {{ background: linear-gradient(135deg, #28a745 0%, #20c997 100%); color: white; padding: 30px; text-align: center; }}
                    .header h1 {{ margin: 0; font-size: 28px; font-weight: 600; }}
                    .content {{ padding: 30px; }}
                    .confirmation-card {{ background: linear-gradient(135deg, #28a745 0%, #20c997 100%); color: white; padding: 25px; border-radius: 12px; margin: 20px 0; text-align: center; }}
                    .amount {{ font-size: 32px; font-weight: bold; margin: 10px 0; }}
                    .details {{ background-color: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0; }}
                    .detail-row {{ display: flex; justify-content: space-between; margin: 10px 0; padding: 8px 0; border-bottom: 1px solid #e0e0e0; }}
                    .detail-label {{ font-weight: 600; color: #666; }}
                    .detail-value {{ color: #333; font-family: 'Courier New', monospace; }}
                    .transaction-id {{ word-break: break-all; font-size: 12px; }}
                    .footer {{ background-color: #f8f9fa; padding: 20px; text-align: center; color: #666; font-size: 14px; }}
                    .button {{ display: inline-block; background: linear-gradient(135deg, #28a745 0%, #20c997 100%); color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; margin: 10px 0; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>✅ Payment Confirmed</h1>
                        <p>Your escrow payment has been successfully received</p>
                    </div>
                    
                    <div class="content">
                        <p>Hi {user_name},</p>
                        
                        <p>Great news! Your escrow payment has been confirmed and the trade is now active.</p>
                        
                        <div class="confirmation-card">
                            <div class="amount">{amount_crypto} {currency}</div>
                            <p>{usd_display}</p>
                        </div>
                        
                        <div class="details">
                            <div class="detail-row">
                                <span class="detail-label">Crypto Amount:</span>
                                <span class="detail-value">{amount_crypto} {currency}</span>
                            </div>
                            {f'<div class="detail-row"><span class="detail-label">USD Value:</span><span class="detail-value">${amount_usd:.2f} USD</span></div>' if amount_usd is not None else ""}
                            <div class="detail-row">
                                <span class="detail-label">Transaction ID:</span>
                                <span class="detail-value transaction-id">{txid}</span>
                            </div>
                        </div>
                        
                        <h3>What happens next?</h3>
                        <ul>
                            <li>🔄 Waiting for seller to accept the trade</li>
                            <li>💬 You can communicate via secure escrow chat</li>
                            <li>📦 Seller will provide item/service details</li>
                            <li>✅ Release funds when you receive what you paid for</li>
                        </ul>
                        
                        <div style="text-align: center; margin: 30px 0;">
                            <a href="{Config.WEBAPP_URL}/trades" class="button">View Trade Status</a>
                        </div>
                        
                        <p style="color: #666; font-size: 14px; margin-top: 30px;">
                            Questions? Reply to this email or contact our support team.
                        </p>
                    </div>
                    
                    <div class="footer">
                        <p>&copy; {Config.BRAND} - Safe Money Exchange</p>
                        <p>Your payment is securely held in escrow until trade completion.</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            # Text version for email clients that don't support HTML
            text_content = f"""
            Escrow Payment Confirmed - {Config.BRAND}
            
            Hi {user_name},
            
            Your escrow payment has been confirmed!
            
            Amount: {amount_crypto} {currency}
            {f"USD Value: ${amount_usd:.2f} USD" if amount_usd is not None else ""}
            Transaction: {txid}
            
            What happens next:
            - Waiting for seller to accept the trade
            - You can communicate via secure escrow chat
            - Release funds when you receive what you paid for
            
            Visit {Config.WEBAPP_URL}/trades to view your trade status.
            
            Thanks for using {Config.BRAND}!
            """
            
            success = await email_service.send_email(
                to_email=user_email,
                subject=subject,
                text_content=text_content,
                html_content=html_content
            )
            
            return success
            
        except Exception as e:
            logger.error(f"Error sending escrow payment email: {e}")
            return False
    
    async def send_escrow_cancelled(self, escrow, reason: str):
        """Send escrow cancellation notification"""
        logger.info(f"❌ Escrow cancelled notification for escrow {escrow.escrow_id}: {reason}")
    
    async def send_admin_alert(self, message: str, **kwargs):
        """Send alert notification to admin"""
        try:
            logger.warning(f"🚨 ADMIN ALERT: {message}")
            
            # Send actual email alert to admin
            try:
                from services.email import email_service
                from config import Config
                
                if email_service.enabled and Config.ADMIN_EMAIL:
                    subject = "🚨 LockBay System Alert - Immediate Attention Required"
                    
                    # Format the alert message for email
                    email_body = f"""
URGENT SYSTEM ALERT - IMMEDIATE ATTENTION REQUIRED

{message}

Time: {kwargs.get('timestamp', 'N/A')}
System: LockBay Escrow Platform
Environment: Production

Please investigate this issue immediately.

---
This is an automated alert from LockBay monitoring system.
                    """
                    
                    success = email_service.send_email(
                        to_email=Config.ADMIN_EMAIL,
                        subject=subject,
                        text_content=email_body
                    )
                    
                    if success:
                        logger.info(f"✅ Admin email alert sent to {Config.ADMIN_EMAIL}")
                    else:
                        logger.error(f"❌ Failed to send admin email alert to {Config.ADMIN_EMAIL}")
                else:
                    logger.warning("⚠️ Admin email alerts disabled - email service not available")
                    
            except Exception as email_error:
                logger.error(f"❌ Exception sending admin email alert: {email_error}")
                
        except Exception as e:
            logger.error(f"❌ Failed to send admin alert: {e}")
    
    async def notify_all_admins(self, message: str, **kwargs):
        """Send notification to all admin users"""
        try:
            logger.info(f"📢 Notifying all admins: {message[:100]}...")
            # For now, delegate to send_admin_alert 
            await self.send_admin_alert(message, **kwargs)
        except Exception as e:
            logger.error(f"❌ Failed to notify all admins: {e}")
    
    async def send_telegram_group_message(self, group_id: int, message: str, **kwargs):
        """Send message to Telegram group"""
        try:
            logger.info(f"👥 Group message to {group_id}: {message[:50]}...")
            # Implementation to be added when needed
        except Exception as e:
            logger.error(f"❌ Failed to send group message: {e}")


# Global instance
consolidated_notification_service = ConsolidatedNotificationService()


async def start_notification_monitoring():
    """Start notification monitoring system"""
    try:
        await consolidated_notification_service.initialize()
        logger.info("✅ Notification monitoring system started")
    except Exception as e:
        logger.error(f"❌ Failed to start notification monitoring: {e}")