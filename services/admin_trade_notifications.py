"""Admin Trade Notifications Service - Sends email alerts to admin for trade/exchange events"""

import logging
from typing import Dict, Any
from datetime import datetime
from services.email import EmailService
from config import Config
from telegram import Bot
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

class AdminTradeNotificationService:
    """Service to send admin email notifications for trade and exchange events"""
    
    def __init__(self):
        self.email_service = EmailService()
        self.admin_email = Config.ADMIN_EMAIL
    
    def _format_currency(self, amount: float, currency: str) -> str:
        """
        Format currency amount with proper symbol and precision.
        
        Args:
            amount: Numerical amount to format (or None if unavailable)
            currency: Currency code (NGN, USD, BTC, ETH, etc.)
            
        Returns:
            Formatted currency string with proper symbol and precision
        """
        # Handle None/unavailable amounts
        if amount is None:
            if currency == 'NGN':
                return "₦0.00 (rate unavailable)"
            elif currency == 'USD':
                return "$0.00 (rate unavailable)"
            else:
                return f"0.00000000 {currency} (rate unavailable)"
        
        if currency == 'NGN':
            return f"₦{amount:,.2f}"
        elif currency == 'USD':
            return f"${amount:.2f}"
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
        
        # Normalize timezone-aware datetimes to UTC before formatting
        if dt.tzinfo is not None:
            dt_utc = dt.astimezone(timezone.utc)
        else:
            dt_utc = dt
        
        formatted_date = dt_utc.strftime('%Y-%m-%d %H:%M UTC')
        
        now = datetime.now(timezone.utc) if dt.tzinfo else datetime.utcnow()
        time_diff = now - dt
        
        if time_diff.total_seconds() < 60:
            relative = "just now"
        elif time_diff.total_seconds() < 3600:
            minutes = int(time_diff.total_seconds() / 60)
            relative = f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        elif time_diff.total_seconds() < 86400:
            hours = int(time_diff.total_seconds() / 3600)
            relative = f"{hours} hour{'s' if hours != 1 else ''} ago"
        else:
            days = int(time_diff.total_seconds() / 86400)
            relative = f"{days} day{'s' if days != 1 else ''} ago"
        
        return f"{formatted_date} ({relative})"
    
    def _format_user_info(self, telegram_id: int, username: str = None, first_name: str = None, last_name: str = None) -> str:
        """
        Format user information for display in notifications.
        
        Args:
            telegram_id: User's Telegram ID
            username: Optional Telegram username
            first_name: Optional user first name
            last_name: Optional user last name
            
        Returns:
            Formatted user info string
        """
        full_name = None
        if first_name:
            full_name = f"{first_name} {last_name}".strip() if last_name else first_name
        
        if full_name and username:
            return f"{full_name} (@{username}) [ID: {telegram_id}]"
        elif full_name:
            return f"{full_name} [ID: {telegram_id}]"
        elif username:
            return f"@{username} [ID: {telegram_id}]"
        else:
            return f"User ID: {telegram_id}"
        
    async def notify_escrow_created(self, escrow_data: Dict[str, Any]) -> bool:
        """
        Queue admin notification when new escrow is created.
        Uses database queue to prevent notification loss during rapid state changes.
        """
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping escrow creation notification")
                return False
                
            # Extract key escrow information
            escrow_id = escrow_data.get('escrow_id', 'Unknown')
            amount = escrow_data.get('amount', 0)
            buyer_info = escrow_data.get('buyer_info', 'Unknown')
            seller_info = escrow_data.get('seller_info', 'Unknown')
            currency = escrow_data.get('currency', 'USD')
            created_at = escrow_data.get('created_at', datetime.utcnow())
            
            amount_display = self._format_currency(amount, currency)
            timestamp_display = self._format_timestamp(created_at)
            
            subject = f"⚠️ ACTION NEEDED: New Escrow Awaiting Payment - {escrow_id}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #ffc107; color: #212529; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0 0 10px 0;">⚠️ New Escrow Created</h1>
                    <div style="display: inline-block; background: #fff; padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold;">
                        ⏳ AWAITING PAYMENT
                    </div>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <div style="background: #fff3cd; padding: 15px; margin-bottom: 20px; border-left: 4px solid #ffc107; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px;">📋 Quick Summary</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            <strong>Escrow ID:</strong> {escrow_id}<br>
                            <strong>Amount:</strong> {amount_display}<br>
                            <strong>Status:</strong> Waiting for buyer payment
                        </p>
                    </div>
                    
                    <h2 style="margin: 20px 0 15px 0;">Trade Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold; width: 40%;">Escrow ID:</td>
                            <td style="padding: 12px 8px;">{escrow_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Amount:</td>
                            <td style="padding: 12px 8px;">{amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Buyer:</td>
                            <td style="padding: 12px 8px;">{buyer_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Seller:</td>
                            <td style="padding: 12px 8px;">{seller_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Created:</td>
                            <td style="padding: 12px 8px;">{timestamp_display}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e7f3ff; border-left: 4px solid #0066cc; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; color: #0066cc; font-size: 16px;">🎯 Recommended Action</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            Monitor this escrow for payment confirmation. If payment is not received within the expected timeframe, the escrow will automatically expire.
                        </p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p style="margin: 0;">This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            # 1. Send email directly (synchronous)
            email_sent = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for escrow creation: {escrow_id}")
            else:
                logger.error(f"❌ Failed to send admin email for escrow creation: {escrow_id}")
            
            # 2. Send Telegram to all admins (proven pattern)
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for escrow creation notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application and application.bot:
                        telegram_message = f"""⚠️ <b>NEW ESCROW CREATED - ACTION NEEDED</b>

📋 ID: <code>{escrow_id}</code>
💰 Amount: {amount_display}
👤 Buyer: {buyer_info}
🤝 Seller: {seller_info}
📅 Created: {timestamp_display}

⏳ Status: <b>AWAITING PAYMENT</b>

🎯 Action: Monitor for payment confirmation"""
                        
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=telegram_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
            
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error queueing admin escrow creation notification: {e}", exc_info=True)
            return False
    
    async def notify_exchange_created(self, exchange_data: Dict[str, Any]) -> bool:
        """Send admin notification when new exchange order is created"""
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping exchange creation notification")
                return False
                
            # Extract key exchange information
            exchange_id = exchange_data.get('exchange_id', 'Unknown')
            amount = exchange_data.get('amount', 0)
            from_currency = exchange_data.get('from_currency', 'Unknown')
            to_currency = exchange_data.get('to_currency', 'Unknown')
            user_info = exchange_data.get('user_info', 'Unknown')
            exchange_type = exchange_data.get('exchange_type', 'Unknown')
            created_at = exchange_data.get('created_at', datetime.utcnow())
            
            amount_display = self._format_currency(amount, from_currency)
            timestamp_display = self._format_timestamp(created_at)
            
            subject = f"💱 New Exchange Order: {from_currency} → {to_currency} - {exchange_id}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #17a2b8; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0 0 10px 0;">💱 New Exchange Order</h1>
                    <div style="display: inline-block; background: #fff; color: #17a2b8; padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold;">
                        PENDING
                    </div>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <div style="background: #d9ecf7; padding: 15px; margin-bottom: 20px; border-left: 4px solid #17a2b8; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px;">📋 Quick Summary</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            <strong>Exchange ID:</strong> {exchange_id}<br>
                            <strong>Amount:</strong> {amount_display}<br>
                            <strong>Pair:</strong> {from_currency} → {to_currency}
                        </p>
                    </div>
                    
                    <h2 style="margin: 20px 0 15px 0;">Exchange Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold; width: 40%;">Exchange ID:</td>
                            <td style="padding: 12px 8px;">{exchange_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Amount:</td>
                            <td style="padding: 12px 8px;">{amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Exchange:</td>
                            <td style="padding: 12px 8px;">{from_currency} → {to_currency}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Type:</td>
                            <td style="padding: 12px 8px;">{exchange_type.replace('_', ' ').title()}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">User:</td>
                            <td style="padding: 12px 8px;">{user_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Created:</td>
                            <td style="padding: 12px 8px;">{timestamp_display}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e7f3ff; border-left: 4px solid #0066cc; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; color: #0066cc; font-size: 16px;">🎯 Recommended Action</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            Monitor this exchange order for incoming deposit. Ensure rates are current and liquidity is available for completion.
                        </p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p style="margin: 0;">This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            success = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if success:
                logger.info(f"✅ Admin notified of exchange creation: {exchange_id}")
            else:
                logger.error(f"❌ Failed to notify admin of exchange creation: {exchange_id}")
                
            return success
            
        except Exception as e:
            logger.error(f"Error sending admin exchange creation notification: {e}")
            return False
    
    async def notify_escrow_completed(self, escrow_data: Dict[str, Any]) -> bool:
        """Send admin notification when escrow is completed"""
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping escrow completion notification")
                return False
                
            # Extract key escrow information
            escrow_id = escrow_data.get('escrow_id', 'Unknown')
            amount = escrow_data.get('amount', 0)
            buyer_info = escrow_data.get('buyer_info', 'Unknown')
            seller_info = escrow_data.get('seller_info', 'Unknown')
            currency = escrow_data.get('currency', 'USD')
            resolution_type = escrow_data.get('resolution_type', 'unknown')
            completed_at = escrow_data.get('completed_at', datetime.utcnow())
            
            amount_display = self._format_currency(amount, currency)
            timestamp_display = self._format_timestamp(completed_at)
            
            # Determine completion method and styling
            if resolution_type == 'released':
                completion_method = "Buyer Released Funds"
                status_color = "#28a745"
                status_bg = "#e8f5e8"
                icon = "✅"
                status_label = "SUCCESS"
            elif resolution_type == 'refunded':
                completion_method = "Refunded to Buyer"
                status_color = "#dc3545"
                status_bg = "#f8e8e8"
                icon = "↩️"
                status_label = "REFUNDED"
            else:
                completion_method = "Completed via Dispute Resolution"
                status_color = "#ffc107"
                status_bg = "#fff8e1"
                icon = "⚖️"
                status_label = "RESOLVED"
            
            subject = f"{icon} Escrow Completed Successfully - {escrow_id}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: {status_color}; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0 0 10px 0;">{icon} Escrow Completed</h1>
                    <div style="display: inline-block; background: #fff; color: {status_color}; padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold;">
                        {status_label}
                    </div>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <div style="background: {status_bg}; padding: 15px; margin-bottom: 20px; border-left: 4px solid {status_color}; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px;">📋 Quick Summary</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            <strong>Escrow ID:</strong> {escrow_id}<br>
                            <strong>Amount:</strong> {amount_display}<br>
                            <strong>Resolution:</strong> {completion_method}
                        </p>
                    </div>
                    
                    <h2 style="margin: 20px 0 15px 0;">Completion Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold; width: 40%;">Escrow ID:</td>
                            <td style="padding: 12px 8px;">{escrow_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Amount:</td>
                            <td style="padding: 12px 8px;">{amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Buyer:</td>
                            <td style="padding: 12px 8px;">{buyer_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Seller:</td>
                            <td style="padding: 12px 8px;">{seller_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Completed:</td>
                            <td style="padding: 12px 8px;">{timestamp_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Resolution Method:</td>
                            <td style="padding: 12px 8px;">{completion_method}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e7f3ff; border-left: 4px solid #0066cc; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; color: #0066cc; font-size: 16px;">🎯 Recommended Action</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            No action required. This trade has been successfully completed. Funds have been distributed according to the resolution method.
                        </p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p style="margin: 0;">This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            # 1. Send email
            email_sent = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for escrow completion: {escrow_id} ({resolution_type})")
            else:
                logger.error(f"❌ Failed to send admin email for escrow completion: {escrow_id}")
            
            # 2. Send Telegram to all admins
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for escrow completion notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application and application.bot:
                        telegram_message = f"""{icon} <b>ESCROW COMPLETED - {status_label}</b>

📋 ID: <code>{escrow_id}</code>
💰 Amount: {amount_display}
👤 Buyer: {buyer_info}
🤝 Seller: {seller_info}
📅 Completed: {timestamp_display}

{icon} Resolution: <b>{completion_method}</b>

✅ Status: Trade successfully completed"""
                        
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=telegram_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
                
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error sending admin escrow completion notification: {e}", exc_info=True)
            return False
    
    async def notify_exchange_completed(self, exchange_data: Dict[str, Any]) -> bool:
        """Send admin notification when exchange order is completed"""
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping exchange completion notification")
                return False
                
            # Extract key exchange information
            exchange_id = exchange_data.get('exchange_id', 'Unknown')
            amount = exchange_data.get('amount', 0)
            from_currency = exchange_data.get('from_currency', 'Unknown')
            to_currency = exchange_data.get('to_currency', 'Unknown')
            final_amount = exchange_data.get('final_amount', amount)
            user_info = exchange_data.get('user_info', 'Unknown')
            exchange_type = exchange_data.get('exchange_type', 'Unknown')
            completed_at = exchange_data.get('completed_at', datetime.utcnow())
            
            input_amount_display = self._format_currency(amount, from_currency)
            output_amount_display = self._format_currency(final_amount, to_currency)
            timestamp_display = self._format_timestamp(completed_at)
            
            subject = f"✅ Exchange Completed: {from_currency} → {to_currency} - {exchange_id}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #28a745; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0 0 10px 0;">✅ Exchange Completed</h1>
                    <div style="display: inline-block; background: #fff; color: #28a745; padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold;">
                        SUCCESS
                    </div>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <div style="background: #e8f5e8; padding: 15px; margin-bottom: 20px; border-left: 4px solid #28a745; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px;">📋 Quick Summary</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            <strong>Exchange ID:</strong> {exchange_id}<br>
                            <strong>Input:</strong> {input_amount_display}<br>
                            <strong>Output:</strong> {output_amount_display}
                        </p>
                    </div>
                    
                    <h2 style="margin: 20px 0 15px 0;">Completion Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold; width: 40%;">Exchange ID:</td>
                            <td style="padding: 12px 8px;">{exchange_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Exchange:</td>
                            <td style="padding: 12px 8px;">{from_currency} → {to_currency}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Input Amount:</td>
                            <td style="padding: 12px 8px;">{input_amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Output Amount:</td>
                            <td style="padding: 12px 8px;">{output_amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Type:</td>
                            <td style="padding: 12px 8px;">{exchange_type.replace('_', ' ').title()}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">User:</td>
                            <td style="padding: 12px 8px;">{user_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Completed:</td>
                            <td style="padding: 12px 8px;">{timestamp_display}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e7f3ff; border-left: 4px solid #0066cc; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; color: #0066cc; font-size: 16px;">🎯 Recommended Action</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            Exchange completed successfully. Verify funds were delivered to user. Update liquidity metrics and monitor for any delivery issues.
                        </p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p style="margin: 0;">This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            success = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if success:
                logger.info(f"✅ Admin notified of exchange completion: {exchange_id}")
            else:
                logger.error(f"❌ Failed to notify admin of exchange completion: {exchange_id}")
                
            return success
            
        except Exception as e:
            logger.error(f"Error sending admin exchange completion notification: {e}")
            return False
    
    async def notify_escrow_cancelled(self, escrow_data: Dict[str, Any]) -> bool:
        """Send admin notification when escrow is cancelled"""
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping escrow cancellation notification")
                return False
                
            escrow_id = escrow_data.get('escrow_id', 'Unknown')
            amount = escrow_data.get('amount', 0)
            buyer_info = escrow_data.get('buyer_info', 'Unknown')
            seller_info = escrow_data.get('seller_info', 'Unknown')
            currency = escrow_data.get('currency', 'USD')
            cancelled_by = escrow_data.get('cancelled_by', 'Unknown')
            reason = escrow_data.get('reason', 'No reason provided')
            cancelled_at = escrow_data.get('cancelled_at', datetime.utcnow())
            
            amount_display = self._format_currency(amount, currency)
            timestamp_display = self._format_timestamp(cancelled_at)
            
            subject = f"❌ Escrow Cancelled - {escrow_id}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #dc3545; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0 0 10px 0;">❌ Escrow Cancelled</h1>
                    <div style="display: inline-block; background: #fff; color: #dc3545; padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold;">
                        CANCELLED
                    </div>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <div style="background: #f8d7da; padding: 15px; margin-bottom: 20px; border-left: 4px solid #dc3545; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px;">📋 Quick Summary</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            <strong>Escrow ID:</strong> {escrow_id}<br>
                            <strong>Amount:</strong> {amount_display}<br>
                            <strong>Cancelled By:</strong> {cancelled_by}
                        </p>
                    </div>
                    
                    <h2 style="margin: 20px 0 15px 0;">Cancellation Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold; width: 40%;">Escrow ID:</td>
                            <td style="padding: 12px 8px;">{escrow_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Amount:</td>
                            <td style="padding: 12px 8px;">{amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Buyer:</td>
                            <td style="padding: 12px 8px;">{buyer_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Seller:</td>
                            <td style="padding: 12px 8px;">{seller_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Cancelled By:</td>
                            <td style="padding: 12px 8px;">{cancelled_by}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Reason:</td>
                            <td style="padding: 12px 8px;">{reason}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Cancelled:</td>
                            <td style="padding: 12px 8px;">{timestamp_display}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e7f3ff; border-left: 4px solid #0066cc; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; color: #0066cc; font-size: 16px;">🎯 Recommended Action</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            Review the cancellation reason. If this was a buyer-initiated cancellation, no further action is required. If there are concerns, investigate the reason for cancellation.
                        </p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p style="margin: 0;">This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            email_sent = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for escrow cancellation: {escrow_id}")
            else:
                logger.error(f"❌ Failed to send admin email for escrow cancellation: {escrow_id}")
            
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for escrow cancellation notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application and application.bot:
                        telegram_message = f"""❌ <b>ESCROW CANCELLED</b>

📋 ID: <code>{escrow_id}</code>
💰 Amount: {amount_display}
👤 Buyer: {buyer_info}
🤝 Seller: {seller_info}
🚫 Cancelled By: {cancelled_by}
📝 Reason: {reason}
📅 Cancelled: {timestamp_display}

❌ Status: Escrow cancelled

🎯 Action: Review cancellation reason if needed"""
                        
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=telegram_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
            
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error sending admin escrow cancellation notification: {e}", exc_info=True)
            return False
    
    async def notify_exchange_cancelled(self, exchange_data: Dict[str, Any]) -> bool:
        """Send admin notification when exchange order is cancelled"""
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping exchange cancellation notification")
                return False
                
            # Extract key exchange information
            exchange_id = exchange_data.get('exchange_id', 'Unknown')
            amount = exchange_data.get('amount', 0)
            from_currency = exchange_data.get('from_currency', 'Unknown')
            to_currency = exchange_data.get('to_currency', 'Unknown')
            user_info = exchange_data.get('user_info', 'Unknown')
            exchange_type = exchange_data.get('exchange_type', 'Unknown')
            cancellation_reason = exchange_data.get('cancellation_reason', 'Unknown reason')
            cancelled_at = exchange_data.get('cancelled_at', datetime.utcnow())
            
            amount_display = self._format_currency(amount, from_currency)
            timestamp_display = self._format_timestamp(cancelled_at)
            
            subject = f"❌ Exchange Cancelled: {from_currency} → {to_currency} - {exchange_id}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #dc3545; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0 0 10px 0;">❌ Exchange Cancelled</h1>
                    <div style="display: inline-block; background: #fff; color: #dc3545; padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold;">
                        CANCELLED
                    </div>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <div style="background: #f8d7da; padding: 15px; margin-bottom: 20px; border-left: 4px solid #dc3545; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px;">📋 Quick Summary</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            <strong>Exchange ID:</strong> {exchange_id}<br>
                            <strong>Amount:</strong> {amount_display}<br>
                            <strong>Reason:</strong> {cancellation_reason}
                        </p>
                    </div>
                    
                    <h2 style="margin: 20px 0 15px 0;">Cancellation Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold; width: 40%;">Exchange ID:</td>
                            <td style="padding: 12px 8px;">{exchange_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Amount:</td>
                            <td style="padding: 12px 8px;">{amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Exchange:</td>
                            <td style="padding: 12px 8px;">{from_currency} → {to_currency}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Type:</td>
                            <td style="padding: 12px 8px;">{exchange_type.replace('_', ' ').title()}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">User:</td>
                            <td style="padding: 12px 8px;">{user_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Reason:</td>
                            <td style="padding: 12px 8px;">{cancellation_reason}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Cancelled:</td>
                            <td style="padding: 12px 8px;">{timestamp_display}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e7f3ff; border-left: 4px solid #0066cc; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; color: #0066cc; font-size: 16px;">🎯 Recommended Action</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            Review cancellation reason. If user-initiated, no action needed. If systemic issue, investigate and address root cause.
                        </p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p style="margin: 0;">This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            success = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if success:
                logger.info(f"✅ Admin notified of exchange cancellation: {exchange_id}")
            else:
                logger.error(f"❌ Failed to notify admin of exchange cancellation: {exchange_id}")
                
            return success
            
        except Exception as e:
            logger.error(f"Error sending admin exchange cancellation notification: {e}")
            return False
    
    # ============================================================================
    # TELEGRAM GROUP NOTIFICATIONS - Escrow Lifecycle Tracking
    # ============================================================================
    
    async def send_group_notification_escrow_created(self, escrow_data: Dict[str, Any]) -> bool:
        """Send Telegram group notification when escrow is created"""
        try:
            if not Config.NOTIFICATION_GROUP_ID:
                logger.debug("Notification group not configured - skipping group notification")
                return False
            
            if not Config.BOT_TOKEN:
                logger.warning("Bot token not configured - skipping group notification")
                return False
            
            escrow_id = escrow_data.get('escrow_id', 'Unknown')
            amount = escrow_data.get('amount', 0)
            buyer_info = escrow_data.get('buyer_info', 'Unknown')
            seller_info = escrow_data.get('seller_info', 'Unknown')
            currency = escrow_data.get('currency', 'USD')
            created_at = escrow_data.get('created_at', datetime.utcnow())
            
            amount_display = self._format_currency(amount, currency)
            timestamp_display = self._format_timestamp(created_at)
            
            message = f"""🆕 <b>New Escrow Created</b>

📋 Trade ID: <code>{escrow_id}</code>
💰 Amount: {amount_display}
👤 Buyer: {buyer_info}
👥 Seller: {seller_info}
📅 Created: {timestamp_display}

⏳ Status: Awaiting payment"""
            
            bot = Bot(Config.BOT_TOKEN)
            await bot.send_message(
                chat_id=Config.NOTIFICATION_GROUP_ID,
                text=message,
                parse_mode='HTML'
            )
            
            logger.info(f"✅ Group notified of escrow creation: {escrow_id}")
            return True
            
        except TelegramError as e:
            logger.error(f"Telegram error sending group notification: {e}")
            return False
        except Exception as e:
            logger.error(f"Error sending group escrow creation notification: {e}")
            return False
    
    async def send_group_notification_payment_confirmed(self, payment_data: Dict[str, Any]) -> bool:
        """Send Telegram group notification AND queue admin email when payment is confirmed"""
        try:
            escrow_id = payment_data.get('escrow_id', 'Unknown')
            amount = payment_data.get('amount', 0)
            currency = payment_data.get('currency', 'USD')
            payment_method = payment_data.get('payment_method', 'Unknown')
            buyer_info = payment_data.get('buyer_info', 'Unknown')
            seller_info = payment_data.get('seller_info', 'Unknown')
            confirmed_at = payment_data.get('confirmed_at', datetime.utcnow())
            txid = payment_data.get('txid', None)
            
            amount_display = self._format_currency(amount, currency)
            timestamp_display = self._format_timestamp(confirmed_at)
            
            # Determine payment method icon
            if payment_method.lower() in ['wallet', 'balance']:
                method_icon = "💼"
                method_display = "Wallet Balance"
            elif 'crypto' in payment_method.lower() or payment_method.upper() in ['BTC', 'LTC', 'ETH', 'USDT']:
                method_icon = "₿"
                method_display = f"{payment_method.upper()} (Crypto)"
            elif 'ngn' in payment_method.lower() or 'fincra' in payment_method.lower():
                method_icon = "🏦"
                method_display = "NGN Bank Transfer"
            else:
                method_icon = "💳"
                method_display = payment_method
            
            telegram_message = f"""💰 Payment Confirmed

📋 Trade ID: {escrow_id}
{method_icon} Method: {method_display}
💵 Amount: {amount_display}
👤 Buyer: {buyer_info}
👥 Seller: {seller_info}
📅 Confirmed: {timestamp_display}"""
            
            if txid:
                telegram_message += f"\n🔗 TX: {txid[:16]}..."
            
            telegram_message += "\n\n✅ Status: Payment secured in escrow"
            
            html_message = f"""💰 <b>Payment Confirmed</b>

📋 Trade ID: <code>{escrow_id}</code>
{method_icon} Method: {method_display}
💵 Amount: {amount_display}
👤 Buyer: {buyer_info}
👥 Seller: {seller_info}
📅 Confirmed: {timestamp_display}"""
            
            if txid:
                html_message += f"\n🔗 TX: <code>{txid[:16]}...</code>"
            
            html_message += "\n\n✅ Status: Payment secured in escrow"
            
            # 1. Send email directly (synchronous)
            subject = f'💰 Payment Confirmed - {escrow_id}'
            html_content = f"""<html><body>
<h2>💰 Payment Confirmed</h2>
<p><strong>Trade ID:</strong> {escrow_id}</p>
<p><strong>Method:</strong> {method_display}</p>
<p><strong>Amount:</strong> {amount_display}</p>
<p><strong>Buyer:</strong> {buyer_info}</p>
<p><strong>Seller:</strong> {seller_info}</p>
<p><strong>Confirmed:</strong> {timestamp_display}</p>
{f'<p><strong>TX:</strong> {txid}</p>' if txid else ''}
<p>✅ <strong>Status:</strong> Payment secured in escrow</p>
</body></html>"""
            
            email_sent = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for payment confirmation: {escrow_id}")
            else:
                logger.error(f"❌ Failed to send admin email for payment confirmation: {escrow_id}")
            
            # 2. Send Telegram to all admins (proven pattern)
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for payment confirmation notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application and application.bot:
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=html_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
            
            # 3. Also send to notification group if configured
            if Config.NOTIFICATION_GROUP_ID and Config.BOT_TOKEN:
                try:
                    bot = Bot(Config.BOT_TOKEN)
                    await bot.send_message(
                        chat_id=Config.NOTIFICATION_GROUP_ID,
                        text=html_message,
                        parse_mode='HTML'
                    )
                    logger.info(f"✅ Group notified of payment confirmation: {escrow_id}")
                except TelegramError as e:
                    logger.error(f"Telegram group notification failed: {e}")
            
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error in payment confirmation notification: {e}")
            return False
    
    async def send_group_notification_seller_notified(self, notification_data: Dict[str, Any]) -> bool:
        """Send Telegram group notification when seller is notified"""
        try:
            if not Config.NOTIFICATION_GROUP_ID or not Config.BOT_TOKEN:
                return False
            
            escrow_id = notification_data.get('escrow_id', 'Unknown')
            seller_info = notification_data.get('seller_info', 'Unknown')
            notification_channel = notification_data.get('notification_channel', 'Unknown')
            amount = notification_data.get('amount', 0)
            currency = notification_data.get('currency', 'USD')
            notified_at = notification_data.get('notified_at', datetime.utcnow())
            
            amount_display = self._format_currency(amount, currency)
            timestamp_display = self._format_timestamp(notified_at)
            
            # Determine notification channel icon
            if notification_channel.lower() == 'telegram':
                channel_icon = "📱"
            elif notification_channel.lower() == 'email':
                channel_icon = "📧"
            else:
                channel_icon = "📢"
            
            message = f"""📢 <b>Seller Notified</b>

📋 Trade ID: <code>{escrow_id}</code>
👥 Seller: {seller_info}
{channel_icon} Channel: {notification_channel}
💰 Amount: {amount_display}
📅 Notified: {timestamp_display}

⏰ Status: Awaiting seller acceptance (24h)"""
            
            bot = Bot(Config.BOT_TOKEN)
            await bot.send_message(
                chat_id=Config.NOTIFICATION_GROUP_ID,
                text=message,
                parse_mode='HTML'
            )
            
            logger.info(f"✅ Group notified of seller notification: {escrow_id}")
            return True
            
        except TelegramError as e:
            logger.error(f"Telegram error sending seller notified: {e}")
            return False
        except Exception as e:
            logger.error(f"Error sending group seller notification: {e}")
            return False
    
    async def send_group_notification_seller_accepted(self, acceptance_data: Dict[str, Any]) -> bool:
        """
        Send direct admin notification when seller accepts trade.
        Uses simple proven pattern: direct email + admin Telegram loop.
        """
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping seller acceptance notification")
                return False
            
            escrow_id = acceptance_data.get('escrow_id', 'Unknown')
            seller_info = acceptance_data.get('seller_info', 'Unknown')
            buyer_info = acceptance_data.get('buyer_info', 'Unknown')
            amount = acceptance_data.get('amount', 0)
            currency = acceptance_data.get('currency', 'USD')
            accepted_at = acceptance_data.get('accepted_at', datetime.utcnow())
            time_to_accept = acceptance_data.get('time_to_accept', None)
            
            amount_display = self._format_currency(amount, currency)
            timestamp_display = self._format_timestamp(accepted_at)
            
            subject = f"✅ Seller Accepted Trade - {Config.PLATFORM_NAME}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #28a745; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1>✅ Seller Accepted Trade</h1>
                    <p>{Config.PLATFORM_NAME} Admin Alert</p>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <h2>Acceptance Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Escrow ID:</td>
                            <td style="padding: 8px;">{escrow_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Amount:</td>
                            <td style="padding: 8px;">{amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Seller:</td>
                            <td style="padding: 8px;">{seller_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Buyer:</td>
                            <td style="padding: 8px;">{buyer_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Accepted:</td>
                            <td style="padding: 8px;">{timestamp_display}</td>
                        </tr>
                        {f'''<tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Response Time:</td>
                            <td style="padding: 8px;">{time_to_accept}</td>
                        </tr>''' if time_to_accept else ''}
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e8f5e8; border-left: 4px solid #28a745; border-radius: 4px;">
                        <p style="margin: 0;"><strong>Status:</strong> Trade is now active</p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p>This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            # 1. Send email directly (synchronous)
            email_sent = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for seller acceptance: {escrow_id}")
            else:
                logger.error(f"❌ Failed to send admin email for seller acceptance: {escrow_id}")
            
            # 2. Send Telegram to all admins (proven pattern from dispute_chat.py)
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for seller acceptance notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application and application.bot:
                        telegram_message = f"""✅ <b>SELLER ACCEPTED TRADE</b>

📋 ID: <code>{escrow_id}</code>
💰 Amount: {amount_display}
👥 Seller: {seller_info}
👤 Buyer: {buyer_info}
📅 Accepted: {timestamp_display}"""
                        
                        if time_to_accept:
                            telegram_message += f"\n⏱️ Response Time: {time_to_accept}"
                        
                        telegram_message += "\n\n🚀 Status: Trade is now active"
                        
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=telegram_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
            
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error sending admin seller acceptance notification: {e}", exc_info=True)
            return False
    
    async def send_group_notification_item_delivered(self, delivery_data: Dict[str, Any]) -> bool:
        """
        Send direct admin notification when item is delivered.
        Uses simple proven pattern: direct email + admin Telegram loop.
        """
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping item delivery notification")
                return False
            
            escrow_id = delivery_data.get('escrow_id', 'Unknown')
            seller_info = delivery_data.get('seller_info', 'Unknown')
            buyer_info = delivery_data.get('buyer_info', 'Unknown')
            amount = delivery_data.get('amount', 0)
            currency = delivery_data.get('currency', 'USD')
            delivered_at = delivery_data.get('delivered_at', datetime.utcnow())
            
            amount_display = self._format_currency(amount, currency)
            timestamp_display = self._format_timestamp(delivered_at)
            
            subject = f"📦 Item Delivered - {Config.PLATFORM_NAME}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #17a2b8; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1>📦 Item Marked as Delivered</h1>
                    <p>{Config.PLATFORM_NAME} Admin Alert</p>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <h2>Delivery Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Escrow ID:</td>
                            <td style="padding: 8px;">{escrow_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Amount:</td>
                            <td style="padding: 8px;">{amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Seller:</td>
                            <td style="padding: 8px;">{seller_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Buyer:</td>
                            <td style="padding: 8px;">{buyer_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Delivered:</td>
                            <td style="padding: 8px;">{timestamp_display}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e8f4f8; border-left: 4px solid #17a2b8; border-radius: 4px;">
                        <p style="margin: 0;"><strong>Status:</strong> Awaiting buyer fund release</p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p>This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            # 1. Send email directly (synchronous)
            email_sent = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for item delivery: {escrow_id}")
            else:
                logger.error(f"❌ Failed to send admin email for item delivery: {escrow_id}")
            
            # 2. Send Telegram to all admins (proven pattern from dispute_chat.py)
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for item delivery notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application and application.bot:
                        telegram_message = f"""📦 <b>ITEM DELIVERED</b>

📋 ID: <code>{escrow_id}</code>
💰 Amount: {amount_display}
👥 Seller: {seller_info}
👤 Buyer: {buyer_info}
📅 Delivered: {timestamp_display}

⏳ Status: Awaiting buyer fund release"""
                        
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=telegram_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
            
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error sending admin item delivery notification: {e}", exc_info=True)
            return False
    
    async def send_group_notification_funds_released(self, release_data: Dict[str, Any]) -> bool:
        """
        Send direct admin notification when funds are released (trade complete).
        Uses simple proven pattern: direct email + admin Telegram loop.
        """
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping funds release notification")
                return False
            
            escrow_id = release_data.get('escrow_id', 'Unknown')
            seller_info = release_data.get('seller_info', 'Unknown')
            buyer_info = release_data.get('buyer_info', 'Unknown')
            amount = release_data.get('amount', 0)
            platform_fee = release_data.get('platform_fee', 0)
            seller_receives = release_data.get('seller_receives', amount)
            released_at = release_data.get('released_at', datetime.utcnow())
            trade_duration = release_data.get('trade_duration', None)
            
            amount_display = self._format_currency(amount, 'USD')
            seller_receives_display = self._format_currency(seller_receives, 'USD')
            platform_fee_display = self._format_currency(platform_fee, 'USD')
            timestamp_display = self._format_timestamp(released_at)
            
            subject = f"💸 Funds Released - Trade Complete - {Config.PLATFORM_NAME}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #28a745; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1>💸 Funds Released - Trade Complete</h1>
                    <p>{Config.PLATFORM_NAME} Admin Alert</p>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <h2>Completion Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Escrow ID:</td>
                            <td style="padding: 8px;">{escrow_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Trade Amount:</td>
                            <td style="padding: 8px;">{amount_display}</td>
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
                            <td style="padding: 8px; font-weight: bold;">Seller Receives:</td>
                            <td style="padding: 8px;">{seller_receives_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Platform Fee:</td>
                            <td style="padding: 8px;">{platform_fee_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Completed:</td>
                            <td style="padding: 8px;">{timestamp_display}</td>
                        </tr>
                        {f'''<tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Trade Duration:</td>
                            <td style="padding: 8px;">{trade_duration}</td>
                        </tr>''' if trade_duration else ''}
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e8f5e8; border-left: 4px solid #28a745; border-radius: 4px;">
                        <p style="margin: 0;"><strong>Status:</strong> Trade successfully completed</p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p>This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            # 1. Send email directly (synchronous)
            email_sent = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for funds release: {escrow_id}")
            else:
                logger.error(f"❌ Failed to send admin email for funds release: {escrow_id}")
            
            # 2. Send Telegram to all admins (proven pattern from dispute_chat.py)
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for funds release notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application and application.bot:
                        amount_display = self._format_currency(amount, 'USD')
                        seller_receives_display = self._format_currency(seller_receives, 'USD')
                        platform_fee_display = self._format_currency(platform_fee, 'USD')
                        timestamp_display = self._format_timestamp(released_at)
                        
                        telegram_message = f"""💸 <b>FUNDS RELEASED - TRADE COMPLETE</b>

📋 ID: <code>{escrow_id}</code>
💰 Amount: {amount_display}
👤 Buyer: {buyer_info}
👥 Seller: {seller_info}
💵 Seller Receives: {seller_receives_display}
🏦 Platform Fee: {platform_fee_display}
📅 Completed: {timestamp_display}"""
                        
                        if trade_duration:
                            telegram_message += f"\n⏱️ Duration: {trade_duration}"
                        
                        telegram_message += "\n\n✅ Status: Trade successfully completed"
                        
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=telegram_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
            
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error sending admin funds release notification: {e}", exc_info=True)
            return False
    
    async def send_group_notification_rating_submitted(self, rating_data: Dict[str, Any]) -> bool:
        """
        Send direct admin notification when rating is submitted.
        Uses simple proven pattern: direct email + admin Telegram loop.
        """
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping rating submission notification")
                return False
            
            escrow_id = rating_data.get('escrow_id', 'Unknown')
            rating_stars = rating_data.get('rating', 0)
            rating_type = rating_data.get('type', 'seller')
            comment = rating_data.get('comment', 'No comment')
            rater_info = rating_data.get('rater_info', 'Unknown')
            rated_user = rating_data.get('rated_user', 'Unknown')
            submitted_at = rating_data.get('submitted_at', datetime.utcnow())
            
            timestamp_display = self._format_timestamp(submitted_at)
            
            # Generate star emoji
            stars_emoji = "⭐" * rating_stars
            stars_empty = "☆" * (5 - rating_stars)
            stars_display = stars_emoji + stars_empty
            
            subject = f"⭐ Rating Submitted - {Config.PLATFORM_NAME}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #ffc107; color: #000; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1>⭐ Rating Submitted</h1>
                    <p>{Config.PLATFORM_NAME} Admin Alert</p>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <h2>Rating Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Trade ID:</td>
                            <td style="padding: 8px;">{escrow_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Rating:</td>
                            <td style="padding: 8px;">{stars_display} ({rating_stars}/5)</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">From:</td>
                            <td style="padding: 8px;">{rater_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">For:</td>
                            <td style="padding: 8px;">{rated_user}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Type:</td>
                            <td style="padding: 8px;">{rating_type.title()}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Comment:</td>
                            <td style="padding: 8px;">{comment}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Submitted:</td>
                            <td style="padding: 8px;">{timestamp_display}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #fff8e6; border-left: 4px solid #ffc107; border-radius: 4px;">
                        <p style="margin: 0;"><strong>Status:</strong> Feedback recorded</p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p>This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            # 1. Send email directly (synchronous)
            email_sent = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for rating submission: {escrow_id}")
            else:
                logger.error(f"❌ Failed to send admin email for rating submission: {escrow_id}")
            
            # 2. Send Telegram to all admins (proven pattern from dispute_chat.py)
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for rating submission notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application and application.bot:
                        telegram_message = f"""⭐ <b>RATING SUBMITTED</b>

📋 Trade ID: <code>{escrow_id}</code>
{stars_display} Rating: {rating_stars}/5
👤 From: {rater_info}
👥 For: {rated_user}
📝 Type: {rating_type.title()}
💭 Comment: {comment[:100]}{"..." if len(comment) > 100 else ""}
📅 Submitted: {timestamp_display}

✅ Status: Feedback recorded"""
                        
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=telegram_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
            
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error sending admin rating submission notification: {e}", exc_info=True)
            return False
    
    async def notify_dispute_resolved(self, dispute_data: Dict[str, Any]) -> bool:
        """
        Send direct admin notification when dispute is resolved.
        Uses simple proven pattern: direct email + admin Telegram loop.
        """
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping dispute resolution notification")
                return False
            
            dispute_id = dispute_data.get('dispute_id', 'Unknown')
            escrow_id = dispute_data.get('escrow_id', 'Unknown')
            resolution_type = dispute_data.get('resolution_type', 'unknown')
            amount = dispute_data.get('amount', 0)
            currency = dispute_data.get('currency', 'USD')
            buyer_info = dispute_data.get('buyer_info', 'Unknown')
            seller_info = dispute_data.get('seller_info', 'Unknown')
            winner_info = dispute_data.get('winner_info', 'Unknown')
            resolved_at = dispute_data.get('resolved_at', datetime.utcnow())
            
            amount_display = self._format_currency(amount, currency)
            timestamp_display = self._format_timestamp(resolved_at)
            
            # Determine resolution emoji and text
            if resolution_type == 'refund':
                resolution_icon = "🔄"
                resolution_text = "Refund to Buyer"
                status_color = "#ffc107"
                status_bg = "#fff8e1"
                status_label = "REFUNDED"
            elif resolution_type == 'release':
                resolution_icon = "💵"
                resolution_text = "Release to Seller"
                status_color = "#28a745"
                status_bg = "#e8f5e8"
                status_label = "RELEASED"
            else:
                resolution_icon = "⚖️"
                resolution_text = "Custom Resolution"
                status_color = "#6c757d"
                status_bg = "#e8e9ea"
                status_label = "RESOLVED"
            
            subject = f"{resolution_icon} Dispute Resolved: {resolution_text} - #{dispute_id}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: {status_color}; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0 0 10px 0;">⚖️ Dispute Resolved</h1>
                    <div style="display: inline-block; background: #fff; color: {status_color}; padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold;">
                        {status_label}
                    </div>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <div style="background: {status_bg}; padding: 15px; margin-bottom: 20px; border-left: 4px solid {status_color}; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px;">📋 Quick Summary</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            <strong>Dispute ID:</strong> #{dispute_id}<br>
                            <strong>Amount:</strong> {amount_display}<br>
                            <strong>Resolution:</strong> {resolution_text}
                        </p>
                    </div>
                    
                    <h2 style="margin: 20px 0 15px 0;">Resolution Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold; width: 40%;">Dispute ID:</td>
                            <td style="padding: 12px 8px;">#{dispute_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Escrow ID:</td>
                            <td style="padding: 12px 8px;">{escrow_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Amount:</td>
                            <td style="padding: 12px 8px;">{amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Buyer:</td>
                            <td style="padding: 12px 8px;">{buyer_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Seller:</td>
                            <td style="padding: 12px 8px;">{seller_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Resolution:</td>
                            <td style="padding: 12px 8px;">{resolution_icon} {resolution_text}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Winner:</td>
                            <td style="padding: 12px 8px;">{winner_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Resolved:</td>
                            <td style="padding: 12px 8px;">{timestamp_display}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e7f3ff; border-left: 4px solid #0066cc; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; color: #0066cc; font-size: 16px;">🎯 Recommended Action</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            Dispute has been resolved. Review the resolution to ensure both parties have been notified. Monitor for any follow-up issues or appeals.
                        </p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p style="margin: 0;">This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            # 1. Send email directly (synchronous)
            email_sent = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for dispute resolution: #{dispute_id}")
            else:
                logger.error(f"❌ Failed to send admin email for dispute resolution: #{dispute_id}")
            
            # 2. Send Telegram to all admins (proven pattern from dispute_chat.py)
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for dispute resolution notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application and application.bot:
                        telegram_message = f"""{resolution_icon} <b>DISPUTE RESOLVED - {status_label}</b>

🆔 Dispute: #{dispute_id}
📋 Trade: <code>{escrow_id}</code>
💰 Amount: {amount_display}
👤 Buyer: {buyer_info}
👥 Seller: {seller_info}
{resolution_icon} Resolution: {resolution_text}
🏆 Winner: {winner_info}
📅 Resolved: {timestamp_display}

✅ Status: Dispute resolved successfully

🎯 Action: Review resolution and monitor for follow-ups"""
                        
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=telegram_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
            
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error sending admin dispute resolution notification: {e}", exc_info=True)
            return False
    
    async def notify_escrow_expired(self, escrow_data: Dict[str, Any]) -> bool:
        """
        Send direct admin notification when escrow expires.
        Uses simple proven pattern: direct email + admin Telegram loop.
        """
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping escrow expiry notification")
                return False
            
            escrow_id = escrow_data.get('escrow_id', 'Unknown')
            amount = escrow_data.get('amount', 0)
            buyer_info = escrow_data.get('buyer_info', 'Unknown')
            seller_info = escrow_data.get('seller_info', 'Unknown')
            currency = escrow_data.get('currency', 'USD')
            expiry_reason = escrow_data.get('expiry_reason', 'Timeout')
            expired_at = escrow_data.get('expired_at', datetime.utcnow())
            refund_status = escrow_data.get('refund_status', 'Pending')
            
            amount_display = self._format_currency(amount, currency)
            timestamp_display = self._format_timestamp(expired_at)
            
            subject = f"⏰ URGENT: Escrow Expired - {escrow_id}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #ffc107; color: #212529; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0 0 10px 0;">⏰ Escrow Expired</h1>
                    <div style="display: inline-block; background: #fff; color: #ffc107; padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold;">
                        EXPIRED
                    </div>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <div style="background: #fff8e1; padding: 15px; margin-bottom: 20px; border-left: 4px solid #ffc107; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px;">📋 Quick Summary</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            <strong>Escrow ID:</strong> {escrow_id}<br>
                            <strong>Amount:</strong> {amount_display}<br>
                            <strong>Refund Status:</strong> {refund_status}
                        </p>
                    </div>
                    
                    <h2 style="margin: 20px 0 15px 0;">Expiry Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold; width: 40%;">Escrow ID:</td>
                            <td style="padding: 12px 8px;">{escrow_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Amount:</td>
                            <td style="padding: 12px 8px;">{amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Buyer:</td>
                            <td style="padding: 12px 8px;">{buyer_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Seller:</td>
                            <td style="padding: 12px 8px;">{seller_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Expiry Reason:</td>
                            <td style="padding: 12px 8px;">{expiry_reason}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Expired:</td>
                            <td style="padding: 12px 8px;">{timestamp_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Refund Status:</td>
                            <td style="padding: 12px 8px;">{refund_status}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e7f3ff; border-left: 4px solid #0066cc; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; color: #0066cc; font-size: 16px;">🎯 Recommended Action</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            Verify automatic refund is processing correctly. Monitor refund status and ensure buyer receives funds. Review expiry reason to identify any systemic issues.
                        </p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p style="margin: 0;">This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            # 1. Send email directly (synchronous)
            email_sent = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for escrow expiry: {escrow_id}")
            else:
                logger.error(f"❌ Failed to send admin email for escrow expiry: {escrow_id}")
            
            # 2. Send Telegram to all admins (proven pattern from dispute_chat.py)
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for escrow expiry notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application and application.bot:
                        telegram_message = f"""⏰ <b>ESCROW EXPIRED - URGENT</b>

📋 ID: <code>{escrow_id}</code>
💰 Amount: {amount_display}
👤 Buyer: {buyer_info}
🤝 Seller: {seller_info}
📝 Reason: {expiry_reason}
📅 Expired: {timestamp_display}
🔄 Refund: {refund_status}

⚠️ Status: Trade expired - automatic refund processing

🎯 Action: Verify refund processing and monitor status"""
                        
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=telegram_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
            
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error sending admin escrow expiry notification: {e}", exc_info=True)
            return False

    async def notify_user_onboarding_started(self, user_data: Dict[str, Any]) -> bool:
        """Send admin notification when new user starts onboarding"""
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping user onboarding started notification")
                return False
                
            # Extract user information
            user_data.get('user_id', 'Unknown')
            telegram_id = user_data.get('telegram_id', 'Unknown')
            username = user_data.get('username', 'N/A')
            first_name = user_data.get('first_name', 'Unknown')
            last_name = user_data.get('last_name', '')
            started_at = user_data.get('started_at', datetime.utcnow())
            
            user_info = self._format_user_info(telegram_id, username, first_name, last_name)
            timestamp_display = self._format_timestamp(started_at)
            
            subject = f"👤 NEW: User Onboarding Started - {first_name}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #007bff; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0 0 10px 0;">👤 New User Onboarding</h1>
                    <div style="display: inline-block; background: #fff; color: #007bff; padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold;">
                        IN PROGRESS
                    </div>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <div style="background: #d9ecf7; padding: 15px; margin-bottom: 20px; border-left: 4px solid #007bff; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px;">📋 Quick Summary</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            <strong>User:</strong> {user_info}<br>
                            <strong>Telegram ID:</strong> {telegram_id}<br>
                            <strong>Status:</strong> Onboarding in progress
                        </p>
                    </div>
                    
                    <h2 style="margin: 20px 0 15px 0;">User Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold; width: 40%;">Name:</td>
                            <td style="padding: 12px 8px;">{user_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Telegram ID:</td>
                            <td style="padding: 12px 8px;">{telegram_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Username:</td>
                            <td style="padding: 12px 8px;">@{username if username else 'N/A'}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Started:</td>
                            <td style="padding: 12px 8px;">{timestamp_display}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e7f3ff; border-left: 4px solid #0066cc; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; color: #0066cc; font-size: 16px;">🎯 Recommended Action</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            Monitor onboarding progress. If user doesn't complete within 24 hours, consider follow-up assistance or investigation.
                        </p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p style="margin: 0;">This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            # 1. Send email directly (synchronous)
            email_sent = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for user onboarding started: {telegram_id}")
            else:
                logger.error(f"❌ Failed to send admin email for user onboarding started: {telegram_id}")
            
            # 2. Send Telegram to all admins
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for user onboarding notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application and application.bot:
                        telegram_message = f"""👤 <b>NEW USER STARTED ONBOARDING - IN PROGRESS</b>

👤 Name: {user_info}
🆔 Telegram: <code>{telegram_id}</code>
📛 Username: @{username if username else 'N/A'}
📅 Started: {timestamp_display}

✅ Status: Onboarding in progress

🎯 Action: Monitor completion within 24 hours"""
                        
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=telegram_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
            
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error sending admin user onboarding started notification: {e}", exc_info=True)
            return False
    
    async def notify_user_onboarding_completed(self, user_data: Dict[str, Any]) -> bool:
        """Send admin notification when user completes onboarding"""
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping user onboarding completed notification")
                return False
                
            # Extract user information
            user_data.get('user_id', 'Unknown')
            telegram_id = user_data.get('telegram_id', 'Unknown')
            username = user_data.get('username', 'N/A')
            first_name = user_data.get('first_name', 'Unknown')
            last_name = user_data.get('last_name', '')
            email = user_data.get('email', 'Not provided')
            email_verified = user_data.get('email_verified', False)
            completed_at = user_data.get('completed_at', datetime.utcnow())
            
            user_info = self._format_user_info(telegram_id, username, first_name, last_name)
            timestamp_display = self._format_timestamp(completed_at)
            verification_status = "✅ Verified" if email_verified else "⚠️ Unverified"
            
            subject = f"✅ SUCCESS: User Onboarding Completed - {first_name}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #28a745; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0 0 10px 0;">✅ Onboarding Completed</h1>
                    <div style="display: inline-block; background: #fff; color: #28a745; padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold;">
                        ACTIVE USER
                    </div>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <div style="background: #e8f5e8; padding: 15px; margin-bottom: 20px; border-left: 4px solid #28a745; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px;">📋 Quick Summary</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            <strong>User:</strong> {user_info}<br>
                            <strong>Email:</strong> {email}<br>
                            <strong>Status:</strong> Ready to use platform
                        </p>
                    </div>
                    
                    <h2 style="margin: 20px 0 15px 0;">User Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold; width: 40%;">Name:</td>
                            <td style="padding: 12px 8px;">{user_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Telegram ID:</td>
                            <td style="padding: 12px 8px;">{telegram_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Username:</td>
                            <td style="padding: 12px 8px;">@{username if username else 'N/A'}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Email:</td>
                            <td style="padding: 12px 8px;">{email}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Verification:</td>
                            <td style="padding: 12px 8px;">{verification_status}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Completed:</td>
                            <td style="padding: 12px 8px;">{timestamp_display}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e7f3ff; border-left: 4px solid #0066cc; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; color: #0066cc; font-size: 16px;">🎯 Recommended Action</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            New user successfully onboarded and ready to trade. Monitor first transaction for smooth experience. Send welcome message if not automated.
                        </p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p style="margin: 0;">This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            # 1. Send email directly (synchronous)
            email_sent = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for user onboarding completed: {telegram_id}")
            else:
                logger.error(f"❌ Failed to send admin email for user onboarding completed: {telegram_id}")
            
            # 2. Send Telegram to all admins
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for user onboarding completed notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application and application.bot:
                        telegram_message = f"""✅ <b>USER ONBOARDING COMPLETED - ACTIVE USER</b>

👤 Name: {user_info}
🆔 Telegram: <code>{telegram_id}</code>
📛 Username: @{username if username else 'N/A'}
📧 Email: {email}
🔐 Status: {verification_status}
📅 Completed: {timestamp_display}

✅ User ready to use platform

🎯 Action: Monitor first transaction, send welcome if needed"""
                        
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=telegram_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
            
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error sending admin user onboarding completed notification: {e}", exc_info=True)
            return False
    
    async def notify_trade_creation_initiated(self, trade_data: Dict[str, Any]) -> bool:
        """Send admin notification when user initiates trade creation"""
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping trade creation initiated notification")
                return False
                
            # Extract trade information
            user_id = trade_data.get('user_id', 'Unknown')
            telegram_id = trade_data.get('telegram_id', user_id)
            username = trade_data.get('username', 'N/A')
            first_name = trade_data.get('first_name', 'Unknown')
            last_name = trade_data.get('last_name', '')
            initiated_at = trade_data.get('initiated_at', datetime.utcnow())
            
            user_info = self._format_user_info(telegram_id, username, first_name, last_name)
            timestamp_display = self._format_timestamp(initiated_at)
            
            subject = f"🛡️ Trade Creation Started - {first_name}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #6c757d; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0 0 10px 0;">🛡️ Trade Creation Initiated</h1>
                    <div style="display: inline-block; background: #fff; color: #6c757d; padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold;">
                        IN PROGRESS
                    </div>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <div style="background: #f8f9fa; padding: 15px; margin-bottom: 20px; border-left: 4px solid #6c757d; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px;">📋 Quick Summary</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            <strong>Buyer:</strong> {user_info}<br>
                            <strong>User ID:</strong> {user_id}<br>
                            <strong>Status:</strong> Creating new trade
                        </p>
                    </div>
                    
                    <h2 style="margin: 20px 0 15px 0;">Trade Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold; width: 40%;">Buyer:</td>
                            <td style="padding: 12px 8px;">{user_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">User ID:</td>
                            <td style="padding: 12px 8px;">{user_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Initiated:</td>
                            <td style="padding: 12px 8px;">{timestamp_display}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e7f3ff; border-left: 4px solid #0066cc; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; color: #0066cc; font-size: 16px;">🎯 Recommended Action</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            User started creating new escrow trade. Monitor for completion. If abandoned, consider follow-up to assist with any issues.
                        </p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p style="margin: 0;">This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            # 1. Send email directly (synchronous)
            email_sent = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for trade creation initiated: {user_id}")
            else:
                logger.error(f"❌ Failed to send admin email for trade creation initiated: {user_id}")
            
            # 2. Send Telegram to all admins
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for trade creation initiated notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application and application.bot:
                        telegram_message = f"""🛡️ <b>TRADE CREATION INITIATED - IN PROGRESS</b>

👤 Buyer: {user_info}
🆔 User ID: <code>{user_id}</code>
📅 Initiated: {timestamp_display}

🔄 Status: Creating new escrow trade

🎯 Action: Monitor for completion or abandonment"""
                        
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=telegram_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
            
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error sending admin trade creation initiated notification: {e}", exc_info=True)
            return False
    
    async def notify_add_funds_clicked(self, wallet_data: Dict[str, Any]) -> bool:
        """Send admin notification when user clicks Add Funds button"""
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping add funds clicked notification")
                return False
                
            # Extract wallet information
            user_id = wallet_data.get('user_id', 'Unknown')
            telegram_id = wallet_data.get('telegram_id', user_id)
            username = wallet_data.get('username', 'N/A')
            first_name = wallet_data.get('first_name', 'Unknown')
            last_name = wallet_data.get('last_name', '')
            clicked_at = wallet_data.get('clicked_at', datetime.utcnow())
            current_balance = wallet_data.get('current_balance', 0)
            
            user_info = self._format_user_info(telegram_id, username, first_name, last_name)
            timestamp_display = self._format_timestamp(clicked_at)
            balance_display = self._format_currency(current_balance, 'USD')
            
            subject = f"💳 User Wants to Add Funds - {first_name}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #17a2b8; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0 0 10px 0;">💳 Add Funds Clicked</h1>
                    <div style="display: inline-block; background: #fff; color: #17a2b8; padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold;">
                        ACTION NEEDED
                    </div>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <div style="background: #d9ecf7; padding: 15px; margin-bottom: 20px; border-left: 4px solid #17a2b8; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px;">📋 Quick Summary</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            <strong>User:</strong> {user_info}<br>
                            <strong>Current Balance:</strong> {balance_display}<br>
                            <strong>Status:</strong> Wants to add funds
                        </p>
                    </div>
                    
                    <h2 style="margin: 20px 0 15px 0;">User Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold; width: 40%;">User:</td>
                            <td style="padding: 12px 8px;">{user_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">User ID:</td>
                            <td style="padding: 12px 8px;">{user_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Current Balance:</td>
                            <td style="padding: 12px 8px;">{balance_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Clicked:</td>
                            <td style="padding: 12px 8px;">{timestamp_display}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e7f3ff; border-left: 4px solid #0066cc; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; color: #0066cc; font-size: 16px;">🎯 Recommended Action</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            User wants to add funds. Monitor for deposit address generation and incoming deposits. Ensure funding process is smooth.
                        </p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p style="margin: 0;">This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            # 1. Send email directly (synchronous)
            email_sent = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for add funds clicked: {user_id}")
            else:
                logger.error(f"❌ Failed to send admin email for add funds clicked: {user_id}")
            
            # 2. Send Telegram to all admins
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for add funds clicked notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application and application.bot:
                        telegram_message = f"""💳 <b>ADD FUNDS CLICKED - ACTION NEEDED</b>

👤 User: {user_info}
🆔 User ID: <code>{user_id}</code>
💰 Current Balance: {balance_display}
📅 Clicked: {timestamp_display}

🔄 Status: User wants to add funds

🎯 Action: Monitor deposit address generation and incoming deposits"""
                        
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=telegram_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
            
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error sending admin add funds clicked notification: {e}", exc_info=True)
            return False
    
    async def notify_wallet_address_generated(self, address_data: Dict[str, Any]) -> bool:
        """Send admin notification when wallet address is generated"""
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping wallet address generated notification")
                return False
                
            # Extract address information
            user_id = address_data.get('user_id', 'Unknown')
            telegram_id = address_data.get('telegram_id', user_id)
            username = address_data.get('username', 'N/A')
            first_name = address_data.get('first_name', 'Unknown')
            last_name = address_data.get('last_name', '')
            currency = address_data.get('currency', 'BTC')
            address = address_data.get('address', 'N/A')
            generated_at = address_data.get('generated_at', datetime.utcnow())
            
            user_info = self._format_user_info(telegram_id, username, first_name, last_name)
            timestamp_display = self._format_timestamp(generated_at)
            
            subject = f"🔑 Wallet Address Generated: {currency} - {first_name}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #ffc107; color: #212529; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0 0 10px 0;">🔑 Wallet Address Generated</h1>
                    <div style="display: inline-block; background: #fff; color: #ffc107; padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold;">
                        AWAITING DEPOSIT
                    </div>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <div style="background: #fff3cd; padding: 15px; margin-bottom: 20px; border-left: 4px solid #ffc107; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px;">📋 Quick Summary</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            <strong>User:</strong> {user_info}<br>
                            <strong>Currency:</strong> {currency}<br>
                            <strong>Status:</strong> Ready for deposit
                        </p>
                    </div>
                    
                    <h2 style="margin: 20px 0 15px 0;">Address Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold; width: 40%;">User:</td>
                            <td style="padding: 12px 8px;">{user_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">User ID:</td>
                            <td style="padding: 12px 8px;">{user_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Currency:</td>
                            <td style="padding: 12px 8px;">{currency}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Address:</td>
                            <td style="padding: 12px 8px; word-break: break-all; font-family: monospace; font-size: 11px;">{address}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Generated:</td>
                            <td style="padding: 12px 8px;">{timestamp_display}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e7f3ff; border-left: 4px solid #0066cc; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; color: #0066cc; font-size: 16px;">🎯 Recommended Action</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            Payment address generated. Monitor for incoming deposit. Ensure webhook notifications are working correctly.
                        </p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p style="margin: 0;">This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            # 1. Send email directly (synchronous)
            email_sent = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for wallet address generated: {user_id} - {currency}")
            else:
                logger.error(f"❌ Failed to send admin email for wallet address generated: {user_id}")
            
            # 2. Send Telegram to all admins
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for wallet address generated notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application.bot:
                        telegram_message = f"""🔑 <b>WALLET ADDRESS GENERATED - AWAITING DEPOSIT</b>

👤 User: {user_info}
🆔 User ID: <code>{user_id}</code>
💎 Currency: {currency}
📍 Address: <code>{address[:10]}...{address[-8:]}</code>
📅 Generated: {timestamp_display}

✅ Status: Payment address ready for deposits

🎯 Action: Monitor for incoming deposit"""
                        
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=telegram_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
            
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error sending admin wallet address generated notification: {e}", exc_info=True)
            return False
    
    async def notify_wallet_funded(self, funding_data: Dict[str, Any]) -> bool:
        """Send admin notification when wallet is successfully funded"""
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping wallet funded notification")
                return False
                
            # Extract funding information
            user_id = funding_data.get('user_id', 'Unknown')
            telegram_id = funding_data.get('telegram_id', user_id)
            username = funding_data.get('username', 'N/A')
            first_name = funding_data.get('first_name', 'Unknown')
            last_name = funding_data.get('last_name', '')
            amount_crypto = funding_data.get('amount_crypto', 0)
            currency = funding_data.get('currency', 'BTC')
            amount_usd = funding_data.get('amount_usd', 0)
            txid = funding_data.get('txid', 'N/A')
            funded_at = funding_data.get('funded_at', datetime.utcnow())
            
            user_info = self._format_user_info(telegram_id, username, first_name, last_name)
            timestamp_display = self._format_timestamp(funded_at)
            crypto_amount = f"{amount_crypto} {currency}"
            usd_value = self._format_currency(amount_usd, 'USD')
            
            subject = f"💰 SUCCESS: Wallet Funded {crypto_amount} - {first_name}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #28a745; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0 0 10px 0;">💰 Wallet Funded Successfully</h1>
                    <div style="display: inline-block; background: #fff; color: #28a745; padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold;">
                        CONFIRMED
                    </div>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <div style="background: #e8f5e8; padding: 15px; margin-bottom: 20px; border-left: 4px solid #28a745; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px;">📋 Quick Summary</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            <strong>User:</strong> {user_info}<br>
                            <strong>Amount:</strong> {crypto_amount}<br>
                            <strong>USD Value:</strong> {usd_value}
                        </p>
                    </div>
                    
                    <h2 style="margin: 20px 0 15px 0;">Funding Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold; width: 40%;">User:</td>
                            <td style="padding: 12px 8px;">{user_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">User ID:</td>
                            <td style="padding: 12px 8px;">{user_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Amount:</td>
                            <td style="padding: 12px 8px;">{crypto_amount}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">USD Value:</td>
                            <td style="padding: 12px 8px;">{usd_value}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Transaction ID:</td>
                            <td style="padding: 12px 8px; word-break: break-all; font-family: monospace; font-size: 11px;">{txid}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 12px 8px; font-weight: bold;">Funded:</td>
                            <td style="padding: 12px 8px;">{timestamp_display}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e7f3ff; border-left: 4px solid #0066cc; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; color: #0066cc; font-size: 16px;">🎯 Recommended Action</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            Deposit confirmed and credited. Verify transaction on blockchain. User can now trade or use funds for transactions.
                        </p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p style="margin: 0;">This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            # 1. Send email directly (synchronous) - PRIORITY: Email must always send
            email_sent = False
            try:
                email_sent = self.email_service.send_email(
                    to_email=self.admin_email,
                    subject=subject,
                    html_content=html_content
                )
                
                if email_sent:
                    logger.info(f"✅ Admin email sent for wallet funded: {user_id} - {self._format_currency(amount_usd, 'USD')}")
                else:
                    logger.error(f"❌ Failed to send admin email for wallet funded: {user_id}")
            except Exception as email_error:
                logger.error(f"❌ Exception while sending admin email for wallet funded: {email_error}", exc_info=True)
            
            # 2. Send Telegram to all admins (isolated from email - failures won't affect email)
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for wallet funded notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application and application.bot:
                        telegram_message = f"""💰 <b>WALLET FUNDED SUCCESSFULLY - CONFIRMED</b>

👤 User: {user_info}
🆔 User ID: <code>{user_id}</code>
💎 Amount: {crypto_amount}
💵 USD Value: {usd_value}
🔗 TX: <code>{txid[:16]}...{txid[-8:]}</code>
📅 Funded: {timestamp_display}

✅ Status: Deposit confirmed and credited

🎯 Action: Verify transaction on blockchain"""
                        
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=telegram_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
            
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error sending admin wallet funded notification: {e}", exc_info=True)
            return False
    
    async def notify_cashout_started(self, cashout_data: Dict[str, Any]) -> bool:
        """Send admin notification when cashout is initiated"""
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping cashout started notification")
                return False
                
            # Extract cashout information
            cashout_id = cashout_data.get('cashout_id', 'Unknown')
            user_id = cashout_data.get('user_id', 'Unknown')
            username = cashout_data.get('username', 'N/A')
            first_name = cashout_data.get('first_name', 'Unknown')
            last_name = cashout_data.get('last_name', '')
            amount = cashout_data.get('amount', 0)
            currency = cashout_data.get('currency', 'USD')
            target_currency = cashout_data.get('target_currency')  # Target crypto for conversion
            cashout_type = cashout_data.get('cashout_type', 'crypto')
            destination = cashout_data.get('destination', 'N/A')
            started_at = cashout_data.get('started_at', datetime.utcnow())
            
            full_name = f"{first_name} {last_name}".strip()
            user_info = f"{full_name} (@{username})" if username else full_name
            
            # Extract additional amount details for proper display
            net_amount = cashout_data.get('net_amount')  # Net USD after fees
            crypto_amount = cashout_data.get('crypto_amount')  # Actual crypto to send
            network_fee = cashout_data.get('network_fee', 0)  # Network fee in USD
            
            # CRITICAL FIX: Show proper amount display for crypto conversions with net crypto
            if target_currency and target_currency != currency:
                # Crypto conversion: show full breakdown
                if crypto_amount is not None:
                    # Best case: show exact crypto amount user expects
                    fee_display = f" (Fee: ${network_fee:.2f})" if network_fee else ""
                    amount_display = f"${amount:.2f} USD{fee_display} → <strong>~{crypto_amount:.8f} {target_currency}</strong>"
                elif net_amount is not None:
                    # Fallback: show net USD
                    amount_display = f"${amount:.2f} USD (Net: ${net_amount:.2f}) → {target_currency}"
                else:
                    # Basic fallback
                    amount_display = f"{self._format_currency(amount, currency)} → {target_currency}"
            else:
                # Direct cashout: show "60 USD"
                amount_display = self._format_currency(amount, currency)
            
            timestamp_display = self._format_timestamp(started_at)
            
            # Format destination based on cashout type
            if cashout_type == 'ngn_bank':
                destination_display = f"NGN Bank Account: {destination}"
            else:
                destination_display = f"Crypto Address: {destination[:12]}...{destination[-8:]}" if len(destination) > 20 else f"Crypto Address: {destination}"
            
            subject = f"💸 Cashout Started - {Config.PLATFORM_NAME}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #17a2b8; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1>💸 Cashout Started</h1>
                    <p>{Config.PLATFORM_NAME} Admin Alert</p>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <h2>Cashout Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Cashout ID:</td>
                            <td style="padding: 8px;">{cashout_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">User:</td>
                            <td style="padding: 8px;">{user_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">User ID:</td>
                            <td style="padding: 8px;">{user_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Amount:</td>
                            <td style="padding: 8px;">{amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Type:</td>
                            <td style="padding: 8px;">{cashout_type.upper()}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Destination:</td>
                            <td style="padding: 8px; word-break: break-all; font-size: 12px;">{destination_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Started:</td>
                            <td style="padding: 8px;">{timestamp_display}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #d1ecf1; border-left: 4px solid #17a2b8; border-radius: 4px;">
                        <p style="margin: 0;"><strong>Status:</strong> Cashout request initiated and processing</p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p>This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            # 1. Send email directly (synchronous)
            email_sent = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for cashout started: {cashout_id}")
            else:
                logger.error(f"❌ Failed to send admin email for cashout started: {cashout_id}")
            
            # 2. Send Telegram to all admins
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for cashout started notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application and application.bot:
                        telegram_message = f"""💸 <b>CASHOUT STARTED</b>

📋 ID: <code>{cashout_id}</code>
👤 User: {user_info}
🆔 User ID: <code>{user_id}</code>
💰 Amount: {amount_display}
🔧 Type: {cashout_type.upper()}
📍 Destination: {destination_display}
📅 Started: {timestamp_display}

⏳ Status: Processing cashout request"""
                        
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=telegram_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
            
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error sending admin cashout started notification: {e}", exc_info=True)
            return False
    
    async def notify_cashout_completed(self, cashout_data: Dict[str, Any]) -> bool:
        """Send admin notification when cashout is successfully completed"""
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping cashout completed notification")
                return False
                
            # Extract cashout information
            cashout_id = cashout_data.get('cashout_id', 'Unknown')
            user_id = cashout_data.get('user_id', 'Unknown')
            username = cashout_data.get('username', 'N/A')
            first_name = cashout_data.get('first_name', 'Unknown')
            last_name = cashout_data.get('last_name', '')
            amount = cashout_data.get('amount', 0)
            currency = cashout_data.get('currency', 'USD')
            usd_amount = cashout_data.get('usd_amount')  # Optional: Original USD amount before conversion
            crypto_amount = cashout_data.get('crypto_amount')  # Actual crypto amount sent
            cashout_data.get('net_amount')  # Net USD after fees
            network_fee = cashout_data.get('network_fee', 0)  # Network fee in USD
            cashout_type = cashout_data.get('cashout_type', 'crypto')
            destination = cashout_data.get('destination', 'N/A')
            txid = cashout_data.get('txid', 'N/A')
            completed_at = cashout_data.get('completed_at', datetime.utcnow())
            
            full_name = f"{first_name} {last_name}".strip()
            user_info = f"{full_name} (@{username})" if username else full_name
            
            # Format amount display with full breakdown
            if crypto_amount is not None and usd_amount:
                # Best case: show exact crypto amount that was sent
                fee_display = f" (Fee: ${network_fee:.2f})" if network_fee else ""
                amount_display = f"${usd_amount:.2f} USD{fee_display} → <strong>~{crypto_amount:.8f} {currency}</strong>"
            elif usd_amount and currency != 'USD':
                # Fallback: show crypto from USD
                usd_display = self._format_currency(usd_amount, 'USD')
                amount_display = f"{self._format_currency(amount, currency)} (from {usd_display})"
            else:
                # Direct currency display
                amount_display = self._format_currency(amount, currency)
            
            timestamp_display = self._format_timestamp(completed_at)
            
            # Format destination based on cashout type
            if cashout_type == 'ngn_bank':
                destination_display = f"NGN Bank Account: {destination}"
            else:
                destination_display = f"Crypto Address: {destination[:12]}...{destination[-8:]}" if len(destination) > 20 else f"Crypto Address: {destination}"
            
            subject = f"✅ Cashout Completed - {Config.PLATFORM_NAME}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: #28a745; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1>✅ Cashout Completed Successfully</h1>
                    <p>{Config.PLATFORM_NAME} Admin Alert</p>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <h2>Cashout Details</h2>
                    
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Cashout ID:</td>
                            <td style="padding: 8px;">{cashout_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">User:</td>
                            <td style="padding: 8px;">{user_info}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">User ID:</td>
                            <td style="padding: 8px;">{user_id}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Amount:</td>
                            <td style="padding: 8px;">{amount_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Type:</td>
                            <td style="padding: 8px;">{cashout_type.upper()}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Destination:</td>
                            <td style="padding: 8px; word-break: break-all; font-size: 12px;">{destination_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Transaction ID:</td>
                            <td style="padding: 8px; word-break: break-all; font-family: monospace; font-size: 11px;">{txid}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #eee;">
                            <td style="padding: 8px; font-weight: bold;">Completed:</td>
                            <td style="padding: 8px;">{timestamp_display}</td>
                        </tr>
                    </table>
                    
                    <div style="margin-top: 20px; padding: 15px; background-color: #e8f5e8; border-left: 4px solid #28a745; border-radius: 4px;">
                        <p style="margin: 0;"><strong>Status:</strong> Cashout successfully completed and funds sent</p>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #6c757d; font-size: 14px;">
                    <p>This is an automated notification from {Config.PLATFORM_NAME}</p>
                </div>
            </div>
            """
            
            # 1. Send email directly (synchronous)
            email_sent = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for cashout completed: {cashout_id}")
            else:
                logger.error(f"❌ Failed to send admin email for cashout completed: {cashout_id}")
            
            # 2. Send Telegram to all admins
            telegram_sent = False
            telegram_count = 0
            
            try:
                from utils.admin_security import AdminSecurityManager
                admin_manager = AdminSecurityManager()
                admin_ids = list(admin_manager.get_admin_ids())
                
                logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for cashout completed notification")
            except Exception as admin_e:
                logger.error(f"❌ Failed to get admin IDs: {admin_e}", exc_info=True)
                admin_ids = []
            
            if admin_ids:
                try:
                    from main import get_application_instance
                    application = get_application_instance()
                    
                    if application and application.bot:
                        telegram_message = f"""✅ <b>CASHOUT COMPLETED SUCCESSFULLY</b>

📋 ID: <code>{cashout_id}</code>
👤 User: {user_info}
🆔 User ID: <code>{user_id}</code>
💰 Amount: {amount_display}
🔧 Type: {cashout_type.upper()}
📍 Destination: {destination_display}
🔗 TX: <code>{txid[:16]}...{txid[-8:]}</code>
📅 Completed: {timestamp_display}

✅ Status: Funds successfully sent"""
                        
                        for admin_id in admin_ids:
                            try:
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                
                                await application.bot.send_message(
                                    chat_id=chat_id,
                                    text=telegram_message,
                                    parse_mode='HTML'
                                )
                                logger.info(f"✅ Telegram notification sent to admin {admin_id}")
                                telegram_sent = True
                                telegram_count += 1
                            except Exception as admin_notify_error:
                                logger.error(f"❌ Failed to notify admin {admin_id}: {admin_notify_error}", exc_info=True)
                        
                        logger.info(f"📊 Sent {telegram_count}/{len(admin_ids)} Telegram notifications")
                    else:
                        logger.error("❌ Application or bot instance not available")
                except Exception as e:
                    logger.error(f"❌ Failed to notify admins via Telegram: {e}", exc_info=True)
            
            return email_sent or telegram_sent
            
        except Exception as e:
            logger.error(f"Error sending admin cashout completed notification: {e}", exc_info=True)
            return False


# Global instance for easy access
admin_trade_notifications = AdminTradeNotificationService()