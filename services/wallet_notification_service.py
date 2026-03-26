"""
Wallet-specific notification service
Handles confirmation messages for wallet deposits separate from exchange/escrow payments
"""

import logging
from typing import Optional
from decimal import Decimal
from config import Config
from models import User
from utils.atomic_transactions import atomic_transaction
from services.email import EmailService

# UNIFIED NOTIFICATION SYSTEM INTEGRATION
from services.consolidated_notification_service import (
    ConsolidatedNotificationService,
    NotificationRequest,
    NotificationCategory,
    NotificationPriority,
    NotificationChannel
)

logger = logging.getLogger(__name__)


class WalletNotificationService:
    """Service for wallet-specific notifications that distinguish from exchange/escrow payments"""
    
    def __init__(self):
        self.notification_service = ConsolidatedNotificationService()
    
    @classmethod
    async def send_crypto_deposit_confirmation(
        cls, 
        user_id: int, 
        amount_crypto: Decimal, 
        currency: str, 
        amount_usd: Optional[Decimal], 
        txid_in: str
    ) -> bool:
        """Send confirmation for cryptocurrency wallet deposit - SAFE TRANSACTION PATTERN"""
        try:
            # SAFE PATTERN: First, get user data in separate, quick transaction
            user_data = None
            with atomic_transaction() as session:
                user = session.query(User).filter(User.id == user_id).first()
                if not user:
                    logger.warning(f"User {user_id} not found for crypto deposit confirmation")
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
            
            # SAFE PATTERN: Now send notifications OUTSIDE transaction context using UNIFIED SERVICE
            
            # Create wallet-specific crypto confirmation message
            if amount_usd is not None:
                message = (
                    f"💰 Wallet: +${amount_usd:.2f} USD\n\n"
                    f"{amount_crypto} {currency} deposited\n"
                    f"TX: {txid_in[:8]}...{txid_in[-4:]}\n\n"
                    f"/wallet to view"
                )
                title = f"Crypto Deposit Confirmed: ${amount_usd:.2f} USD"
            else:
                # Handle case when USD amount is unavailable due to rate failure
                message = (
                    f"💰 Wallet: +{amount_crypto} {currency}\n\n"
                    f"USD value: Rate temporarily unavailable\n"
                    f"TX: {txid_in[:8]}...{txid_in[-4:]}\n\n"
                    f"/wallet to view"
                )
                title = f"Crypto Deposit Confirmed: {amount_crypto} {currency}"
            
            # Create notification request for UNIFIED SERVICE
            notification_request = NotificationRequest(
                user_id=user_id,
                category=NotificationCategory.PAYMENTS,
                priority=NotificationPriority.HIGH,  # Crypto deposits are high priority
                title=title,
                message=message,
                template_data={
                    'amount_crypto': str(amount_crypto),
                    'currency': currency,
                    'amount_usd': str(amount_usd) if amount_usd is not None else None,
                    'txid_in': txid_in,
                    'user_name': f"{user_data['first_name'] or ''} {user_data['last_name'] or ''}".strip() or user_data['username'] or "User"
                },
                channels=[NotificationChannel.TELEGRAM, NotificationChannel.EMAIL],
                require_delivery=True,  # Important financial notification
                broadcast_mode=True,  # Send to BOTH channels simultaneously
                idempotency_key=f"wallet_crypto_deposit_{user_id}_{txid_in}_confirmed"
            )
            
            # Send via UNIFIED NOTIFICATION SERVICE
            try:
                notification_service = ConsolidatedNotificationService()
                await notification_service.initialize()  # Ensure service is ready
                
                delivery_results = await notification_service.send_notification(notification_request)
                
                telegram_sent = any(
                    result.status.value in ['sent', 'delivered'] 
                    for channel, result in delivery_results.items() 
                    if channel == 'telegram'
                )
                email_sent = any(
                    result.status.value in ['sent', 'delivered'] 
                    for channel, result in delivery_results.items() 
                    if channel == 'email'
                )
                
                logger.info(f"✅ UNIFIED_WALLET_NOTIFICATION: Crypto deposit notification sent via consolidated service (telegram: {telegram_sent}, email: {email_sent})")
                
            except Exception as notification_error:
                logger.error(f"❌ Failed to send crypto deposit notification via unified service: {notification_error}")
                telegram_sent = False
                email_sent = False
            
            # Return True if at least one channel succeeded
            if telegram_sent or email_sent:
                logger.info(f"✅ Crypto deposit confirmation delivered to user {user_id} (telegram_sent={telegram_sent}, email_sent={email_sent})")
                return True
            else:
                logger.warning(f"❌ Failed to deliver crypto deposit confirmation to user {user_id} via any channel (telegram_sent={telegram_sent}, email_sent={email_sent})")
                return False
                
        except Exception as e:
            logger.error(f"Error sending crypto wallet deposit confirmation: {e}")
            return False
    
    @classmethod
    async def _send_crypto_deposit_email(
        cls,
        user_email: str,
        user_name: str,
        amount_crypto: Decimal,
        currency: str,
        amount_usd: Decimal,
        txid_in: str
    ) -> bool:
        """Send email confirmation for crypto wallet deposit"""
        try:
            email_service = EmailService()
            
            subject = f"Wallet Deposit Confirmed - {Config.BRAND}"
            
            # Create HTML email content with handling for None USD amount
            usd_display = f"${amount_usd:.2f} USD" if amount_usd is not None else "Rate temporarily unavailable"
            
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Wallet Deposit Confirmed</title>
                <style>
                    body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; background-color: #f8f9fa; }}
                    .container {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; }}
                    .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; }}
                    .header h1 {{ margin: 0; font-size: 28px; font-weight: 600; }}
                    .content {{ padding: 30px; }}
                    .deposit-card {{ background: linear-gradient(135deg, #4CAF50 0%, #45a049 100%); color: white; padding: 25px; border-radius: 12px; margin: 20px 0; text-align: center; }}
                    .amount {{ font-size: 32px; font-weight: bold; margin: 10px 0; }}
                    .crypto-details {{ background-color: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0; }}
                    .detail-row {{ display: flex; justify-content: space-between; margin: 10px 0; padding: 8px 0; border-bottom: 1px solid #e0e0e0; }}
                    .detail-label {{ font-weight: 600; color: #666; }}
                    .detail-value {{ color: #333; font-family: 'Courier New', monospace; }}
                    .transaction-id {{ word-break: break-all; font-size: 12px; }}
                    .footer {{ background-color: #f8f9fa; padding: 20px; text-align: center; color: #666; font-size: 14px; }}
                    .button {{ display: inline-block; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; margin: 10px 0; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>💰 Deposit Confirmed</h1>
                        <p>Your wallet has been successfully funded</p>
                    </div>
                    
                    <div class="content">
                        <p>Hi {user_name},</p>
                        
                        <p>Great news! Your cryptocurrency deposit has been confirmed and added to your {Config.BRAND} wallet.</p>
                        
                        <div class="deposit-card">
                            <div class="amount">{amount_crypto} {currency}</div>
                            <p>{usd_display}</p>
                        </div>
                        
                        <div class="crypto-details">
                            <div class="detail-row">
                                <span class="detail-label">Crypto Amount:</span>
                                <span class="detail-value">{amount_crypto} {currency}</span>
                            </div>
                            <div class="detail-row">
                                <span class="detail-label">USD Value:</span>
                                <span class="detail-value">{usd_display}</span>
                            </div>
                            <div class="detail-row">
                                <span class="detail-label">Transaction ID:</span>
                                <span class="detail-value transaction-id">{txid_in}</span>
                            </div>
                        </div>
                        
                        <p>Your funds are now available for:</p>
                        <ul>
                            <li>🔄 Quick crypto exchanges</li>
                            <li>🤝 Secure escrow trades</li>
                            <li>💸 Instant cashouts</li>
                        </ul>
                        
                        <div style="text-align: center; margin: 30px 0;">
                            <a href="{Config.WEBAPP_URL}" class="button">View Wallet</a>
                        </div>
                        
                        <p style="color: #666; font-size: 14px; margin-top: 30px;">
                            Need help? Reply to this email or contact our support team.
                        </p>
                    </div>
                    
                    <div class="footer">
                        <p>&copy; {Config.BRAND} - Safe Money Exchange</p>
                        <p>This email was sent regarding your wallet activity.</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            # Text version for email clients that don't support HTML
            text_content = f"""
            Wallet Deposit Confirmed - {Config.BRAND}
            
            Hi {user_name},
            
            Your cryptocurrency deposit has been confirmed!
            
            Amount: {amount_crypto} {currency}
            USD Value: {usd_display}
            Transaction: {txid_in}
            
            Your funds are now available in your wallet for trades and cashouts.
            
            Visit {Config.WEBAPP_URL} to view your wallet.
            
            Thanks for using {Config.BRAND}!
            """
            
            success = await email_service.send_email(
                to_email=user_email,
                subject=subject,
                text_content=text_content,
                html_content=html_content
            )
            
            if success:
                logger.info(f"Crypto deposit email sent to {user_email}")
            else:
                logger.warning(f"Failed to send crypto deposit email to {user_email}")
                
            return success
            
        except Exception as e:
            logger.error(f"Error sending crypto deposit email: {e}")
            return False
    
    @classmethod
    async def send_ngn_deposit_confirmation(
        cls, 
        user_id: int, 
        amount_usd: Decimal, 
        amount_ngn: Decimal, 
        reference: str
    ) -> bool:
        """Send confirmation for NGN wallet deposit - SAFE TRANSACTION PATTERN WITH UNIFIED SERVICE"""
        try:
            # SAFE PATTERN: First, get user data in separate, quick transaction
            user_data = None
            with atomic_transaction() as session:
                user = session.query(User).filter(User.id == user_id).first()
                if not user:
                    logger.warning(f"User {user_id} not found for NGN deposit confirmation")
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
            
            # SAFE PATTERN: Now send notifications OUTSIDE transaction context using UNIFIED SERVICE
            
            # Create wallet-specific NGN confirmation message
            message = (
                f"💰 Wallet: +${amount_usd:.2f} USD\n\n"
                f"₦{amount_ngn:,.0f} deposited\n"
                f"Ref: {reference}\n\n"
                f"/wallet to view"
            )
            title = f"NGN Deposit Confirmed: ${amount_usd:.2f} USD"
            
            # Create notification request for UNIFIED SERVICE
            notification_request = NotificationRequest(
                user_id=user_id,
                category=NotificationCategory.PAYMENTS,
                priority=NotificationPriority.HIGH,  # NGN deposits are high priority
                title=title,
                message=message,
                template_data={
                    'amount_usd': str(amount_usd),
                    'amount_ngn': str(amount_ngn),
                    'reference': reference,
                    'user_name': f"{user_data['first_name'] or ''} {user_data['last_name'] or ''}".strip() or user_data['username'] or "User"
                },
                channels=[NotificationChannel.TELEGRAM, NotificationChannel.EMAIL],
                require_delivery=True,  # Important financial notification
                broadcast_mode=True,  # Send to BOTH channels simultaneously
                idempotency_key=f"wallet_ngn_deposit_{user_id}_{reference}_confirmed"
            )
            
            # Send via UNIFIED NOTIFICATION SERVICE
            try:
                notification_service = ConsolidatedNotificationService()
                await notification_service.initialize()  # Ensure service is ready
                
                delivery_results = await notification_service.send_notification(notification_request)
                
                telegram_sent = any(
                    result.status.value in ['sent', 'delivered'] 
                    for channel, result in delivery_results.items() 
                    if channel == 'telegram'
                )
                email_sent = any(
                    result.status.value in ['sent', 'delivered'] 
                    for channel, result in delivery_results.items() 
                    if channel == 'email'
                )
                
                logger.info(f"✅ UNIFIED_WALLET_NOTIFICATION: NGN deposit notification sent via consolidated service (telegram: {telegram_sent}, email: {email_sent})")
                
                # Return True if at least one channel succeeded
                if telegram_sent or email_sent:
                    logger.info(f"✅ NGN deposit confirmation delivered to user {user_id} (telegram_sent={telegram_sent}, email_sent={email_sent})")
                    return True
                else:
                    logger.warning(f"❌ Failed to deliver NGN deposit confirmation to user {user_id} via any channel")
                    return False
                
            except Exception as notification_error:
                logger.error(f"❌ Failed to send NGN deposit notification via unified service: {notification_error}")
                return False
                
        except Exception as e:
            logger.error(f"Error sending NGN wallet deposit confirmation: {e}")
            return False
    
    @classmethod
    async def _send_ngn_deposit_email(
        cls,
        user_email: str,
        user_name: str,
        amount_usd: Decimal,
        amount_ngn: Decimal,
        reference: str
    ) -> bool:
        """Send email confirmation for NGN wallet deposit"""
        try:
            email_service = EmailService()
            
            subject = f"NGN Deposit Confirmed - {Config.BRAND}"
            
            # Create HTML email content (simplified for NGN)
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>NGN Deposit Confirmed</title>
                <style>
                    body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; background-color: #f8f9fa; }}
                    .container {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; }}
                    .header {{ background: linear-gradient(135deg, #2E8B57 0%, #228B22 100%); color: white; padding: 30px; text-align: center; }}
                    .content {{ padding: 30px; }}
                    .deposit-card {{ background: linear-gradient(135deg, #4CAF50 0%, #45a049 100%); color: white; padding: 25px; border-radius: 12px; margin: 20px 0; text-align: center; }}
                    .amount {{ font-size: 32px; font-weight: bold; margin: 10px 0; }}
                    .details {{ background-color: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0; }}
                    .footer {{ background-color: #f8f9fa; padding: 20px; text-align: center; color: #666; font-size: 14px; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>🏦 NGN Deposit Confirmed</h1>
                        <p>Your bank transfer has been processed</p>
                    </div>
                    
                    <div class="content">
                        <p>Hi {user_name},</p>
                        
                        <p>Your Nigerian Naira bank transfer has been confirmed and converted to USD.</p>
                        
                        <div class="deposit-card">
                            <div class="amount">${amount_usd:.2f} USD</div>
                            <p>Added to your wallet balance</p>
                        </div>
                        
                        <div class="details">
                            <p><strong>NGN Amount:</strong> ₦{amount_ngn:,.0f}</p>
                            <p><strong>USD Value:</strong> ${amount_usd:.2f} USD</p>
                            <p><strong>Reference:</strong> {reference}</p>
                        </div>
                        
                        <p>Your funds are now available for trades and cashouts.</p>
                    </div>
                    
                    <div class="footer">
                        <p>&copy; {Config.BRAND} - Safe Money Exchange</p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            # Text version
            text_content = f"""
            NGN Deposit Confirmed - {Config.BRAND}
            
            Hi {user_name},
            
            Your Nigerian Naira bank transfer has been confirmed!
            
            Amount: ₦{amount_ngn:,.0f}
            USD Value: ${amount_usd:.2f} USD
            Reference: {reference}
            
            Your funds are now available in your wallet.
            
            Thanks for using {Config.BRAND}!
            """
            
            success = await email_service.send_email(
                to_email=user_email,
                subject=subject,
                text_content=text_content,
                html_content=html_content
            )
            
            if success:
                logger.info(f"NGN deposit email sent to {user_email}")
            else:
                logger.warning(f"Failed to send NGN deposit email to {user_email}")
                
            return success
            
        except Exception as e:
            logger.error(f"Error sending NGN deposit email: {e}")
            return False


# Global instance
wallet_notification_service = WalletNotificationService()