"""Email service for the Telegram Escrow Bot using Brevo (formerly SendinBlue)"""

import logging
import asyncio
from typing import Optional
import sib_api_v3_sdk
from sib_api_v3_sdk.rest import ApiException
from config import Config
from database import async_managed_session

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending email notifications using Brevo"""

    def __init__(self):
        self.api_key = Config.BREVO_API_KEY
        self.from_email = Config.FROM_EMAIL

        if not self.api_key:
            logger.warning(
                "Brevo API key not configured - email notifications disabled"
            )
            self.enabled = False
        else:
            self.enabled = True
            # Configure Brevo API client
            configuration = sib_api_v3_sdk.Configuration()
            configuration.api_key["api-key"] = self.api_key
            self.api_instance = sib_api_v3_sdk.TransactionalEmailsApi(
                sib_api_v3_sdk.ApiClient(configuration)
            )

    async def send_email_async(
        self,
        to_email: str,
        subject: str,
        text_content: Optional[str] = None,
        html_content: Optional[str] = None,
    ) -> bool:
        """Send an email asynchronously using Brevo"""
        return await asyncio.to_thread(
            self.send_email,
            to_email=to_email,
            subject=subject,
            text_content=text_content,
            html_content=html_content
        )

    def send_email(
        self,
        to_email: str,
        subject: str,
        text_content: Optional[str] = None,
        html_content: Optional[str] = None,
    ) -> bool:
        """Send an email using Brevo"""

        if not self.enabled:
            logger.error("❌ Email sending FAILED - BREVO_API_KEY not configured")
            logger.error(f"   Recipient: {to_email}")
            logger.error(f"   Subject: {subject}")
            logger.error("   🔧 FIX: Set BREVO_API_KEY in production secrets and redeploy")
            return False

        try:
            # Create email object
            send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
                to=[{"email": to_email}],
                sender={"email": self.from_email, "name": Config.FROM_NAME},
                subject=subject,
            )

            if html_content:
                send_smtp_email.html_content = html_content
            if text_content:
                send_smtp_email.text_content = text_content

            if not html_content and not text_content:
                logger.error("No email content provided")
                return False

            # Send email via Brevo
            api_response = self.api_instance.send_transac_email(send_smtp_email)

            # Handle API response properly
            try:
                message_id = getattr(api_response, "message_id", None)
                if message_id:
                    logger.info(
                        f"Email sent successfully to {to_email} - Message ID: {message_id}"
                    )
                else:
                    logger.info(f"Email sent successfully to {to_email}")
                logger.info(f"Brevo API response: {api_response}")
            except (AttributeError, TypeError):
                logger.info(f"Email sent successfully to {to_email}")
            return True

        except ApiException as e:
            logger.error(f"Brevo API error: {e}")
            return False
        except Exception as e:
            logger.error(f"Email sending error: {e}")
            return False

    def send_email_with_reply_to(
        self,
        to_email: str,
        subject: str,
        text_content: Optional[str] = None,
        html_content: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> bool:
        """Send an email with Reply-To header for webhook routing"""

        if not self.enabled:
            logger.error("❌ Email sending FAILED - BREVO_API_KEY not configured")
            logger.error(f"   Recipient: {to_email}")
            logger.error(f"   Subject: {subject}")
            logger.error(f"   Reply-To: {reply_to}")
            logger.error("   🔧 FIX: Set BREVO_API_KEY in production secrets and redeploy")
            return False

        try:
            # Create email object
            send_smtp_email = sib_api_v3_sdk.SendSmtpEmail(
                to=[{"email": to_email}],
                sender={"email": self.from_email, "name": Config.FROM_NAME},
                subject=subject,
            )

            # Set Reply-To header if provided
            if reply_to:
                send_smtp_email.reply_to = {"email": reply_to}

            if html_content:
                send_smtp_email.html_content = html_content
            if text_content:
                send_smtp_email.text_content = text_content

            if not html_content and not text_content:
                logger.error("No email content provided")
                return False

            # Send email via Brevo
            api_response = self.api_instance.send_transac_email(send_smtp_email)

            # Handle API response properly
            try:
                message_id = getattr(api_response, "message_id", None)
                if message_id:
                    logger.info(
                        f"Email sent successfully to {to_email} with Reply-To: {reply_to} - Message ID: {message_id}"
                    )
                else:
                    logger.info(f"Email sent successfully to {to_email} with Reply-To: {reply_to}")
                logger.info(f"Brevo API response: {api_response}")

            except (AttributeError, TypeError):
                logger.info(f"Email sent successfully to {to_email} with Reply-To: {reply_to}")

            return True

        except ApiException as e:
            logger.error(f"Brevo API error: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending email with Reply-To: {e}")
            return False

    async def send_dual_verification_links(
        self,
        current_email: str,
        new_email: str,
        user_name: str,
        current_token: str,
        new_token: str,
    ) -> bool:
        """Send verification links to both current and new email addresses"""
        if not self.enabled:
            logger.info(
                f"Email service disabled - would send dual verification to {current_email} and {new_email}"
            )
            return False

        try:
            from config import Config

            current_link = f"{Config.WEBAPP_URL}/verify-email-change?token={current_token}&type=current"
            new_link = (
                f"{Config.WEBAPP_URL}/verify-email-change?token={new_token}&type=new"
            )

            # Send to current email
            current_success = self.send_email(
                current_email,
                f"🔐 Confirm Email Change - {Config.PLATFORM_NAME}",
                html_content=f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                    <div style="background: #dc3545; color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
                        <h1>🔐 Confirm Email Change</h1>
                        <p>{Config.PLATFORM_NAME}</p>
                    </div>
                    <div style="background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px;">
                        <h2>Hello {user_name}!</h2>
                        <p><strong>Someone requested to change your email address from {current_email} to {new_email}.</strong></p>
                        
                        <div style="background: #fff3cd; padding: 15px; border-radius: 5px; border-left: 4px solid #ffc107; margin: 20px 0;">
                            <strong>⚠️ Security Alert:</strong> If this was you, click the button below to confirm. If not, ignore this email.
                        </div>
                        
                        <div style="text-align: center; margin: 30px 0;">
                            <a href="{current_link}" style="display: inline-block; background: #dc3545; color: white; padding: 15px 30px; font-size: 18px; font-weight: bold; text-decoration: none; border-radius: 10px;">
                                ✅ Confirm Email Change
                            </a>
                        </div>
                        
                        <p>This link expires in 24 hours.</p>
                    </div>
                </div>
                """,
            )

            # Send to new email
            new_success = self.send_email(
                new_email,
                f"🔐 Verify New Email Address - {Config.PLATFORM_NAME}",
                html_content=f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                    <div style="background: #28a745; color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
                        <h1>📧 Verify New Email</h1>
                        <p>{Config.PLATFORM_NAME}</p>
                    </div>
                    <div style="background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px;">
                        <h2>Hello {user_name}!</h2>
                        <p><strong>Please verify this email address to complete your email change.</strong></p>
                        
                        <div style="text-align: center; margin: 30px 0;">
                            <a href="{new_link}" style="display: inline-block; background: #28a745; color: white; padding: 15px 30px; font-size: 18px; font-weight: bold; text-decoration: none; border-radius: 10px;">
                                ✅ Verify New Email
                            </a>
                        </div>
                        
                        <p><strong>Important:</strong> Both emails must be verified to complete the change.</p>
                        <p>This link expires in 24 hours.</p>
                    </div>
                </div>
                """,
            )

            return current_success and new_success

        except Exception as e:
            logger.error(f"Error sending dual verification links: {e}")
            return False

    async def send_dual_verification_email(
        self,
        email: str,
        first_name: str,
        verification_code: str,
        email_type: str,
        other_email: Optional[str] = None,
    ) -> bool:
        """Send verification email for dual email change verification"""
        try:
            if email_type == "current":
                subject = "🔐 Confirm Email Change - Current Address"
                title = "Confirm Email Change Request"
                message = f"""
                Hi {first_name},
                
                You requested to change your email address from {email} to {other_email}.
                
                For security, please verify this change by entering the code below:
                """
            else:
                subject = "🔐 Verify New Email Address"
                title = "Verify Your New Email"
                message = f"""
                Hi {first_name},
                
                Please verify this email address to complete your email change.
                
                Enter the verification code below:
                """

            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <title>{title}</title>
            </head>
            <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; background-color: #f5f5f5; padding: 20px;">
                <div style="background-color: white; border-radius: 10px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <!-- Header -->
                    <div style="text-align: center; margin-bottom: 30px;">
                        <h1 style="color: #1e3a8a; margin: 0; font-size: 24px;">🔐 {title}</h1>
                        <p style="color: #64748b; margin: 5px 0 0 0;">Secure your account</p>
                    </div>
                    
                    <!-- Main content -->
                    <div style="text-align: center; margin-bottom: 30px;">
                        <p style="color: #374151; font-size: 16px; margin-bottom: 20px;">
                            {message}
                        </p>
                        
                        <!-- Verification Code Box -->
                        <div style="background: linear-gradient(135deg, #1e3a8a, #3b82f6); padding: 20px; border-radius: 10px; margin: 25px 0;">
                            <p style="color: white; margin: 0 0 10px 0; font-size: 14px;">Your verification code:</p>
                            <div style="font-size: 32px; font-weight: bold; color: white; letter-spacing: 5px; font-family: monospace;">
                                {verification_code}
                            </div>
                        </div>
                        
                        <p style="color: #ef4444; font-size: 14px; margin: 20px 0;">
                            ⚠️ This code expires in 15 minutes
                        </p>
                    </div>
                    
                    <!-- Security Notice -->
                    <div style="background: #f8fafc; padding: 20px; border-radius: 8px; border-left: 4px solid #3b82f6;">
                        <h3 style="color: #1e40af; margin: 0 0 10px 0; font-size: 16px;">🛡️ Security Notice</h3>
                        <ul style="color: #475569; margin: 0; padding-left: 20px; font-size: 14px;">
                            <li>Enter this code in the {Config.PLATFORM_NAME}</li>
                            <li>Never share this code with anyone</li>
                            <li>If you didn't request this change, contact support immediately</li>
                        </ul>
                    </div>
                    
                    <!-- Footer -->
                    <div style="text-align: center; margin-top: 30px; padding-top: 20px; border-top: 1px solid #e5e7eb;">
                        <p style="color: #9ca3af; font-size: 12px; margin: 0;">
                            This is an automated security message from {Config.PLATFORM_NAME}<br>
                            For support, contact us through the bot
                        </p>
                    </div>
                </div>
            </body>
            </html>
            """

            return self.send_email(email, subject, html_content=html_content)

        except Exception as e:
            logger.error(f"Error sending dual verification email to {email}: {e}")
            return False

    async def send_verification_link_email(
        self, user_email: str, user_name: str, verification_link: str
    ) -> bool:
        """Send email verification email with clickable link to user"""
        if not self.enabled:
            logger.info(
                f"Email service disabled - would send verification to {user_email}"
            )
            return False

        subject = f"Verify Your Email - {Config.PLATFORM_NAME}"

        # Create verification content with clickable link
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
                <h1>🔒 Email Verification</h1>
                <p>{Config.PLATFORM_NAME}</p>
            </div>
            <div style="background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px;">
                <h2>Hello {user_name}!</h2>
                <p>Please click the button below to verify your email address and activate your account:</p>
                
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{verification_link}" style="display: inline-block; background: #667eea; color: white; padding: 15px 30px; font-size: 18px; font-weight: bold; text-decoration: none; border-radius: 10px; border: none;">
                        ✅ Verify Email Address
                    </a>
                </div>
                
                <p><strong>Or copy this link:</strong></p>
                <p style="word-break: break-all; background: #e9e9e9; padding: 10px; border-radius: 5px; font-family: monospace; font-size: 12px;">{verification_link}</p>
                
                <p><strong>Important:</strong></p>
                <ol>
                    <li>Click the verification button above</li>
                    <li>You'll be redirected to complete verification</li>
                    <li>Your email will be verified instantly</li>
                </ol>
                
                <p style="background: #fff3cd; padding: 15px; border-radius: 5px; border-left: 4px solid #ffc107;">
                    <strong>⚠️ Important:</strong> This link expires in 24 hours for security.
                </p>
                
                <p><strong>Why verify your email?</strong></p>
                <ul>
                    <li>🔐 Enhanced account security</li>
                    <li>📧 Important transaction notifications</li>
                    <li>🔄 Account recovery options</li>
                    <li>📊 Trade completion alerts</li>
                </ul>
                
                <p style="margin-top: 30px; color: #666; font-size: 14px;">
                    If you didn't request this verification, please ignore this email.
                </p>
            </div>
        </div>
        """

        text_content = f"""
Verify Your Email - {Config.PLATFORM_NAME}

Hello {user_name}!

Please click the link below to verify your email address:

{verification_link}

How to verify:
1. Click the verification link above
2. You'll be redirected to complete verification
3. Your email will be verified instantly

⚠️ Important: This link expires in 24 hours for security.

Why verify your email?
- Enhanced account security
- Important transaction notifications
- Account recovery options
- Trade completion alerts

If you didn't request this verification, please ignore this email.

Best regards,
{Config.PLATFORM_NAME} Team
        """

        return self.send_email(user_email, subject, text_content, html_content)

    async def send_welcome_email(self, user_email: str, user_name: str) -> bool:
        """Send welcome email to new user"""
        subject = f"Welcome to {Config.PLATFORM_NAME}! 🎉"

        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0; }}
                .content {{ background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px; }}
                .button {{ display: inline-block; background: #667eea; color: white; padding: 12px 30px; text-decoration: none; border-radius: 5px; margin: 20px 0; }}
                .footer {{ text-align: center; margin-top: 30px; color: #666; font-size: 14px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🔒 Welcome to {Config.PLATFORM_NAME}!</h1>
                    <p>Secure crypto transactions made simple</p>
                </div>
                <div class="content">
                    <h2>Hello {user_name}! 👋</h2>
                    
                    <p>Thank you for joining {Config.PLATFORM_NAME}. You're now part of a secure platform for cryptocurrency transactions with built-in buyer and seller protection.</p>
                    
                    <h3>What you can do:</h3>
                    <ul>
                        <li>✅ Create secure trade transactions</li>
                        <li>💬 Communicate safely with trading partners</li>
                        <li>🏆 Build your reputation through successful trades</li>
                        <li>💰 Manage your multi-currency wallet</li>
                        <li>🛡️ Dispute resolution with admin support</li>
                    </ul>
                    
                    <h3>Security Features:</h3>
                    <ul>
                        <li>🔐 Funds held in secure trade until completion</li>
                        <li>⚡ Fast dispute resolution process</li>
                        <li>📊 Transparent fee structure ({Config.ESCROW_FEE_PERCENTAGE}%)</li>
                        <li>🌐 Multi-currency support (USDT, USDC, ETH, BTC)</li>
                    </ul>
                    
                    <p>Get started by creating your first trade transaction!</p>
                    
                    <a href="https://t.me/{Config.BOT_USERNAME}" class="button">Open {Config.PLATFORM_NAME}</a>
                </div>
                <div class="footer">
                    <p>Questions? Contact our support team anytime.<br>
                    This email was sent because you registered for {Config.PLATFORM_NAME}.</p>
                </div>
            </div>
        </body>
        </html>
        """

        text_content = f"""
        Welcome to {Config.PLATFORM_NAME}, {user_name}!
        
        Thank you for joining our secure cryptocurrency trading platform.
        
        What you can do:
        • Create secure trade transactions
        • Communicate safely with trading partners  
        • Build your reputation through successful trades
        • Manage your multi-currency wallet
        • Get dispute resolution with admin support
        
        Security Features:
        • Funds held in secure trade until completion
        • Fast dispute resolution process
        • Transparent fee structure ({Config.ESCROW_FEE_PERCENTAGE}%)
        • Multi-currency support (USDT, USDC, ETH, BTC)
        
        Get started: https://t.me/{Config.BOT_USERNAME}
        
        Questions? Contact our support team anytime.
        """

        return self.send_email(user_email, subject, text_content, html_content)

    async def send_trade_notification(
        self,
        user_email: str,
        user_name: str,
        escrow_id: str,
        notification_type: str,
        details: dict,
    ) -> bool:
        """Send trade-related notification email"""

        notification_templates = {
            "trade_invitation": {
                "subject": f"💰 Trade Invitation #{escrow_id}",
                "message": "New secured trade waiting for your acceptance.",
            },
            "trade_created": {
                "subject": f"Trade #{escrow_id} Created Successfully",
                "message": "Trade created - seller invited.",
            },
            "seller_accepted": {
                "subject": f"✅ Trade Started #{escrow_id}",
                "message": "Seller accepted - trade now active.",
            },
            "seller_trade_accepted": {
                "subject": f"🎉 Trade Accepted Successfully #{escrow_id}",
                "message": "You have successfully accepted this trade. The buyer has been notified and the funds are secured in escrow.",
            },
            "seller_declined": {
                "subject": f"Seller Declined Trade #{escrow_id}",
                "message": "The seller has declined your trade request.",
            },
            "deposit_confirmed": {
                "subject": f"Deposit Confirmed for Trade #{escrow_id}",
                "message": "Your deposit has been confirmed. The trade is now active.",
            },
            "funds_released": {
                "subject": f"Funds Released - Trade #{escrow_id}",
                "message": "The funds have been released and credited to your wallet.",
            },
            "dispute_opened": {
                "subject": f"Dispute Opened - Trade #{escrow_id}",
                "message": "A dispute has been opened for this trade. Admin will review shortly.",
            },
            "dispute_resolved": {
                "subject": f"Dispute Resolved - Trade #{escrow_id}",
                "message": "The dispute has been resolved by our admin team.",
            },
            "delivery_confirmed": {
                "subject": f"📦 Item Delivered - Trade #{escrow_id}",
                "message": "The seller has marked the item as delivered. Please confirm receipt to release funds.",
            },
            "delivery_marked": {
                "subject": f"✅ Delivery Confirmed - Trade #{escrow_id}",
                "message": f"You have successfully marked the item as delivered for trade #{escrow_id}. The buyer has been notified to review and release the funds. Once the buyer confirms receipt, you'll receive payment to your wallet.",
            },
            "trade_cancelled": {
                "subject": f"❌ Trade Cancelled - Trade #{escrow_id}",
                "message": "This trade has been cancelled. Any refunds will be processed automatically.",
            },
        }

        template = notification_templates.get(notification_type)
        if not template:
            logger.error(f"Unknown notification type: {notification_type}")
            return False

        amount = details.get("amount", 0)
        currency = details.get("currency", "CRYPTO")

        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #667eea; color: white; padding: 20px; text-align: center; }}
                .content {{ background: #f9f9f9; padding: 30px; }}
                .escrow-details {{ background: white; padding: 20px; border-radius: 5px; margin: 20px 0; }}
                .button {{ display: inline-block; background: #667eea; color: white; padding: 12px 30px; text-decoration: none; border-radius: 5px; }}
                .footer {{ text-align: center; margin-top: 30px; color: #666; font-size: 14px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>🔒 {Config.PLATFORM_NAME} Notification</h2>
                </div>
                <div class="content">
                    <h3>Hello {user_name},</h3>
                    
                    <p>{template['message']}</p>
                    
                    <div class="escrow-details">
                        <h4>Trade Details:</h4>
                        <p><strong>🆔 Trade ID:</strong> {escrow_id}</p>
                        <p><strong>Amount:</strong> {float(amount):.2f} {currency}</p>
                        <p><strong>Status:</strong> {details.get('status', 'Unknown')}</p>
                    </div>
                    
                    <p>You can manage this escrow and view full details in the bot.</p>
                    
                    <a href="https://t.me/{Config.BOT_USERNAME}" class="button">Open {Config.PLATFORM_NAME}</a>
                </div>
                <div class="footer">
                    <p>This is an automated notification from {Config.PLATFORM_NAME}.<br>
                    If you didn't expect this email, please contact support.</p>
                </div>
            </div>
        </body>
        </html>
        """

        text_content = f"""
        {Config.PLATFORM_NAME} Notification
        
        Hello {user_name},
        
        {template['message']}
        
        Trade Details:
        • Trade ID: {escrow_id}
        • Amount: {float(amount):.2f} {currency}
        • Status: {details.get('status', 'Unknown')}
        
        Manage this trade: https://t.me/{Config.BOT_USERNAME}
        
        This is an automated notification from {Config.PLATFORM_NAME}.
        """

        return self.send_email(
            user_email, template["subject"], text_content, html_content
        )

    async def send_cashout_notification(
        self,
        user_email: str,
        user_name: str,
        cashout_id: int,
        amount: float,
        currency: str,
        status: str,
    ) -> bool:
        """Send cashout notification email"""

        status_messages = {
            "pending": "Cashout submitted - pending approval.",
            "processing": "Processing your cashout now.",
            "completed": "Cashout sent successfully.",
            "failed": "Cashout failed - contact support.",
        }

        subject = f"✅ Cashout #{cashout_id} Complete" if status == "completed" else f"Cashout #{cashout_id} - {status.title()}"
        message = status_messages.get(status, "CashOut status update.")

        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #28a745; color: white; padding: 20px; text-align: center; }}
                .content {{ background: #f9f9f9; padding: 30px; }}
                .cashout-details {{ background: white; padding: 20px; border-radius: 5px; margin: 20px 0; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>💸 CashOut Update</h2>
                </div>
                <div class="content">
                    <h3>Hello {user_name},</h3>
                    
                    <p>{message}</p>
                    
                    <div class="cashout-details">
                        <h4>CashOut Details:</h4>
                        <p><strong>Request ID:</strong> #{cashout_id}</p>
                        <p><strong>Amount:</strong> {amount} {currency}</p>
                        <p><strong>Status:</strong> {status.title()}</p>
                    </div>
                    
                    <p>You can check your cashout history in the bot.</p>
                </div>
            </div>
        </body>
        </html>
        """

        text_content = f"""
        CashOut Update - {Config.PLATFORM_NAME}
        
        Hello {user_name},
        
        {message}
        
        CashOut Details:
        • Request ID: #{cashout_id}  
        • Amount: {amount} {currency}
        • Status: {status.title()}
        
        Check your history: https://t.me/{Config.BOT_USERNAME}
        """

        return self.send_email(user_email, subject, text_content, html_content)

    async def send_ngn_cashout_notification(
        self,
        user_email: str,
        user_name: str,
        cashout_id: int,
        usd_amount: float,
        ngn_amount: float,
        bank_name: str,
        account_number: str,
        account_name: str,
        transaction_ref: str,
        earnings_amount: float = 0.0,
    ) -> bool:
        """Send NGN bank transfer cashout notification email"""

        subject = f"🏦 NGN CashOut #{cashout_id} - Completed Successfully"

        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #28a745; color: white; padding: 20px; text-align: center; }}
                .content {{ background: #f9f9f9; padding: 30px; }}
                .details {{ background: white; padding: 20px; border-radius: 5px; margin: 20px 0; }}
                .earnings {{ background: #e8f5e8; padding: 15px; border-radius: 5px; margin: 15px 0; border-left: 4px solid #28a745; }}
                .amount {{ font-size: 1.2em; font-weight: bold; color: #28a745; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>🏦 NGN CashOut Completed</h2>
                </div>
                <div class="content">
                    <h3>Hello {user_name},</h3>
                    
                    <p>Great news! Your NGN bank transfer has been completed successfully.</p>
                    
                    <div class="details">
                        <h4>💰 Transaction Details:</h4>
                        <p><strong>{Config.PLATFORM_NAME} Transaction ID:</strong> <span class="amount">#{cashout_id}</span></p>
                        <p><strong>USD Deducted:</strong> <span class="amount">${usd_amount:.2f} USD</span></p>
                        <p><strong>NGN Sent:</strong> <span class="amount">₦{ngn_amount:,.2f}</span></p>
                        <p><strong>Bank:</strong> {bank_name}</p>
                        <p><strong>Account:</strong> {account_number} ({account_name})</p>
                        <p><strong>Bank Reference:</strong> {transaction_ref}</p>
                        <p><strong>Status:</strong> ✅ Completed</p>
                    </div>
                    
                    {"<div class='earnings'><h4>🎉 Money Earned:</h4><p>You earned <strong>$" + f"{earnings_amount:.2f}" + " USD</strong> added to your wallet!</p></div>" if earnings_amount > 0 else ""}
                    
                    <p><strong>What's Next?</strong></p>
                    <ul>
                        <li>The funds have been sent to your bank account</li>
                        <li>Processing typically takes 1-5 minutes</li>
                        <li>Check your bank app or SMS notifications</li>
                        <li>Contact support if you don't receive funds within 30 minutes</li>
                    </ul>
                    
                    <p>Thank you for using {Config.PLATFORM_NAME}! 🙏</p>
                </div>
            </div>
        </body>
        </html>
        """

        text_content = f"""
        NGN CashOut Completed - {Config.PLATFORM_NAME}
        
        Hello {user_name},
        
        Your NGN bank transfer has been completed successfully!
        
        Transaction Details:
        • {Config.PLATFORM_NAME} Transaction ID: #{cashout_id}
        • USD Deducted: ${usd_amount:.2f} USD
        • NGN Sent: ₦{ngn_amount:,.2f}
        • Bank: {bank_name}
        • Account: {account_number} ({account_name})
        • Bank Reference: {transaction_ref}
        • Status: ✅ Completed
        
        {"Money Saved: $" + f"{0:.2f}" + " USD compared to traditional exchanges!" if False else ""}
        
        The funds have been sent to your bank account. Processing typically takes 1-5 minutes.
        
        Thank you for using {Config.PLATFORM_NAME}!
        """

        return self.send_email(user_email, subject, text_content, html_content)

    async def send_escrow_notification(
        self,
        user_email: str,
        user_name: str,
        escrow_id: str,
        notification_type: str,
        details: dict,
    ) -> bool:
        """Send escrow-related notification email"""

        notification_templates = {
            "payment_confirmed": {
                "subject": f"✅ Payment Confirmed - Trade {escrow_id}",
                "title": "🔒 Payment Secured in Escrow",
                "message": "Your payment has been confirmed and secured in escrow.",
                "action": "Your seller has been notified and has 24 hours to accept the trade.",
            },
            "trade_accepted": {
                "subject": f"🤝 Trade Accepted - {escrow_id}",
                "title": "🤝 Trade Accepted",
                "message": "Your trade has been accepted by the seller.",
                "action": "The funds are now held securely in escrow until completion.",
            },
            "trade_completed": {
                "subject": f"✅ Trade Completed - {escrow_id}",
                "title": "✅ Trade Completed Successfully",
                "message": "Your trade has been completed successfully.",
                "action": "Funds have been released to the seller.",
            },
        }

        template = notification_templates.get(notification_type, notification_templates["payment_confirmed"])
        
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                .header {{ background: #007bff; color: white; padding: 20px; text-align: center; }}
                .content {{ background: #f9f9f9; padding: 30px; }}
                .trade-details {{ background: white; padding: 20px; border-radius: 5px; margin: 20px 0; }}
                .amount {{ font-size: 1.2em; font-weight: bold; color: #007bff; }}
                .status {{ background: #e8f5e8; padding: 15px; border-radius: 5px; margin: 15px 0; border-left: 4px solid #28a745; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h2>{template["title"]}</h2>
                </div>
                <div class="content">
                    <h3>Hello {user_name},</h3>
                    
                    <p>{template["message"]}</p>
                    
                    <div class="trade-details">
                        <h4>🔐 Trade Details:</h4>
                        <p><strong>Trade ID:</strong> {escrow_id}</p>
                        <p><strong>Amount:</strong> <span class="amount">${details.get('escrow_amount', 0):.2f} USD</span></p>
                        <p><strong>Seller:</strong> {details.get('seller_info', 'Unknown')}</p>
                        {"<p><strong>Transaction Hash:</strong> " + details.get('transaction_hash', 'N/A')[:16] + "...</p>" if details.get('transaction_hash') else ""}
                    </div>
                    
                    <div class="status">
                        <p><strong>Next Steps:</strong> {template["action"]}</p>
                    </div>
                    
                    <p>You can track your trade progress by visiting your {Config.PLATFORM_NAME} bot.</p>
                    
                    <p>Thank you for using {Config.PLATFORM_NAME}! 🙏</p>
                </div>
            </div>
        </body>
        </html>
        """

        text_content = f"""
        {template["title"]} - {Config.PLATFORM_NAME}
        
        Hello {user_name},
        
        {template["message"]}
        
        Trade Details:
        • Trade ID: {escrow_id}
        • Amount: ${details.get('escrow_amount', 0):.2f} USD
        • Seller: {details.get('seller_info', 'Unknown')}
        {"• Transaction Hash: " + details.get('transaction_hash', 'N/A')[:16] + "..." if details.get('transaction_hash') else ""}
        
        Next Steps: {template["action"]}
        
        Track your trade: https://t.me/{Config.BOT_USERNAME}
        
        Thank you for using {Config.PLATFORM_NAME}!
        """

        return self.send_email(user_email, template["subject"], text_content, html_content)

    async def send_exchange_notification(
        self,
        user_email: str,
        user_name: str,
        order_id: str,
        notification_type: str,
        details: dict,
    ) -> bool:
        """Send exchange-related notification email"""

        notification_templates = {
            "order_created": {
                "subject": f"📋 Exchange Order Created - Order #{order_id}",
                "message": "Your exchange order has been created successfully! You can now proceed with payment to complete the transaction.",
                "color": "#007bff",
            },
            "order_expired": {
                "subject": f"⏰ Exchange Quote Expired - Order #{order_id}",
                "message": "Your exchange rate quote has expired. Please request a new quote to continue with current market rates.",
                "color": "#dc3545",
            },
            "deposit_confirmed": {
                "subject": f"✅ Crypto Deposit Confirmed - Order #{order_id}",
                "message": "Your crypto deposit has been confirmed and is being processed.",
                "color": "#28a745",
            },
            "payment_confirmed": {
                "subject": f"✅ NGN Payment Confirmed - Order #{order_id}",
                "message": "Your NGN payment has been confirmed and your crypto is being processed.",
                "color": "#28a745",
            },
            "exchange_completed": {
                "subject": f"🎉 Exchange Completed - Order #{order_id}",
                "message": "Your currency exchange has been completed successfully!",
                "color": "#28a745",
            },
        }

        template = notification_templates.get(notification_type)
        if not template:
            logger.error(f"Unknown exchange notification type: {notification_type}")
            return False

        # Generate detailed message based on type
        details_html = self._generate_exchange_details_html(notification_type, details)

        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{template['subject']} - {Config.PLATFORM_NAME}</title>
        </head>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; background-color: #f4f4f4;">
            <div style="max-width: 600px; margin: 20px auto; background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 0 20px rgba(0,0,0,0.1);">
                
                <!-- Header -->
                <div style="background: {template['color']}; color: white; padding: 30px; text-align: center;">
                    <h1 style="margin: 0; font-size: 28px;">📱 {Config.PLATFORM_NAME}</h1>
                    <p style="margin: 10px 0 0 0; font-size: 16px; opacity: 0.9;">{template['subject']}</p>
                </div>
                
                <!-- Content -->
                <div style="padding: 40px 30px;">
                    <h2 style="color: #333; margin: 0 0 20px 0;">Hello {user_name}!</h2>
                    
                    <p style="font-size: 16px; margin-bottom: 20px;">{template['message']}</p>
                    
                    <!-- Exchange Details -->
                    <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; border-left: 4px solid {template['color']}; margin: 20px 0;">
                        <h3 style="margin: 0 0 15px 0; color: #333;">Exchange Details:</h3>
                        {details_html}
                    </div>
                    
                    <!-- Action Button -->
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="https://t.me/{Config.BOT_USERNAME}" 
                           style="display: inline-block; background: {template['color']}; color: white; padding: 15px 30px; 
                                  text-decoration: none; border-radius: 25px; font-weight: bold; font-size: 16px;">
                            📱 Open Bot
                        </a>
                    </div>
                    
                    <p style="font-size: 14px; color: #666; text-align: center; margin: 30px 0 0 0;">
                        Thank you for using {Config.PLATFORM_NAME}! 
                        <br>Questions? Contact our support team anytime.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """

        text_content = f"""
        {template['subject']} - {Config.PLATFORM_NAME}
        
        Hello {user_name},
        
        {template['message']}
        
        Order ID: #{order_id}
        
        Open Bot: https://t.me/{Config.BOT_USERNAME}
        
        Thank you for using {Config.PLATFORM_NAME}!
        """

        return self.send_email(
            user_email, template["subject"], text_content, html_content
        )

    def _generate_exchange_details_html(
        self, notification_type: str, details: dict
    ) -> str:
        """Generate HTML for exchange details based on notification type"""
        order_id = details.get("order_id", "N/A")

        if notification_type == "order_created":
            order_type = details.get("type", "exchange")
            source_currency = details.get("source_currency", "")
            target_currency = details.get("target_currency", "")
            source_amount = details.get("source_amount", 0)
            target_amount = details.get("target_amount", 0)
            expires_minutes = details.get("expires_minutes", 45)
            
            return f"""
                <p><strong>Order ID:</strong> #{order_id}</p>
                <p><strong>Type:</strong> {order_type.replace('_', ' ').title()}</p>
                <p><strong>Exchange:</strong> {source_amount} {source_currency} → {target_amount} {target_currency}</p>
                <p><strong>Status:</strong> Awaiting Payment</p>
                <p style="color: #007bff;"><strong>Expires in:</strong> {expires_minutes} minutes</p>
            """

        elif notification_type == "order_expired":
            order_type = details.get("type", "exchange")
            return f"""
                <p><strong>Order ID:</strong> #{order_id}</p>
                <p><strong>Type:</strong> {order_type.replace('_', ' ').title()}</p>
                <p><strong>Status:</strong> Expired</p>
                <p style="color: #dc3545;"><strong>Action Required:</strong> Request new quote for current rates</p>
            """

        elif notification_type == "deposit_confirmed":
            crypto = details.get("crypto", "CRYPTO")
            amount = details.get("amount", 0)
            ngn_amount = details.get("ngn_amount", 0)
            return f"""
                <p><strong>Order ID:</strong> #{order_id}</p>
                <p><strong>Received:</strong> {amount} {crypto}</p>
                <p><strong>NGN Value:</strong> ₦{ngn_amount:,.2f}</p>
                <p><strong>Status:</strong> Processing NGN Transfer</p>
            """

        elif notification_type == "payment_confirmed":
            ngn_amount = details.get("ngn_amount", 0)
            crypto = details.get("crypto", "CRYPTO")
            crypto_amount = details.get("crypto_amount", 0)
            return f"""
                <p><strong>Order ID:</strong> #{order_id}</p>
                <p><strong>Paid:</strong> ₦{ngn_amount:,.2f}</p>
                <p><strong>Converting to:</strong> {crypto_amount} {crypto}</p>
                <p><strong>Status:</strong> Processing Crypto Transfer</p>
            """

        elif notification_type == "exchange_completed":
            exchange_type = details.get("type", "exchange")
            amount = details.get("amount", 0)

            if exchange_type == "crypto_to_ngn":
                return f"""
                    <p><strong>Order ID:</strong> #{order_id}</p>
                    <p><strong>Completed:</strong> Crypto → NGN Exchange</p>
                    <p><strong>Received:</strong> ₦{amount:,.2f} NGN</p>
                    <p><strong>Status:</strong> ✅ Completed Successfully</p>
                """
            else:  # ngn_to_crypto
                crypto = details.get("crypto", "CRYPTO")
                return f"""
                    <p><strong>Order ID:</strong> #{order_id}</p>
                    <p><strong>Completed:</strong> NGN → Crypto Exchange</p>
                    <p><strong>Received:</strong> {amount} {crypto}</p>
                    <p><strong>Status:</strong> ✅ Completed Successfully</p>
                """

        return f"<p><strong>Order ID:</strong> #{order_id}</p>"


    async def send_exchange_completion_email(self, to_email: str, order, tx_hash: str, bank_reference: str) -> bool:
        """Send professional exchange completion email with transaction details"""
        try:
            source_amount = getattr(order, 'source_amount', 0)
            source_currency = getattr(order, 'source_currency', 'USD')
            final_amount = getattr(order, 'final_amount', 0)
            
            subject = f"🎉 Exchange Completed - ₦{final_amount:,.2f} Transfer Initiated"
            
            html_content = f"""
            <div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.1);">
                <!-- Header -->
                <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 12px 12px 0 0; text-align: center;">
                    <h1 style="color: white; margin: 0; font-size: 28px; font-weight: 700;">🎉 Exchange Completed!</h1>
                    <p style="color: rgba(255,255,255,0.9); margin: 10px 0 0 0; font-size: 16px;">Your crypto has been converted successfully</p>
                </div>
                
                <!-- Content -->
                <div style="padding: 40px 30px;">
                    <!-- Transaction Summary -->
                    <div style="background: #f8fafc; border-radius: 8px; padding: 25px; margin-bottom: 30px; border-left: 4px solid #10b981;">
                        <h2 style="color: #1f2937; margin: 0 0 20px 0; font-size: 20px; font-weight: 600;">Transaction Summary</h2>
                        
                        <div style="padding: 12px 0; border-bottom: 1px solid #e5e7eb;">
                            <span style="color: #6b7280; font-weight: 500;">Crypto Received: </span>
                            <span style="color: #1f2937; font-weight: 600; font-size: 16px;">{source_amount} {source_currency}</span>
                        </div>
                        
                        <div style="padding: 12px 0; border-bottom: 1px solid #e5e7eb;">
                            <span style="color: #6b7280; font-weight: 500;">NGN Transfer: </span>
                            <span style="color: #059669; font-weight: 700; font-size: 18px;">₦{final_amount:,.2f}</span>
                        </div>
                        
                        <div style="padding: 12px 0; border-bottom: 1px solid #e5e7eb;">
                            <span style="color: #6b7280; font-weight: 500;">Bank Reference: </span>
                            <span style="color: #7c3aed; font-weight: 600; font-family: monospace; background: #f3f4f6; padding: 4px 8px; border-radius: 4px;">{bank_reference or 'Processing...'}</span>
                        </div>
                        
                        <div style="padding: 12px 0;">
                            <span style="color: #6b7280; font-weight: 500;">Transaction Hash: </span>
                            <span style="color: #6b7280; font-weight: 500; font-family: monospace; font-size: 12px;">{tx_hash[:20]}...</span>
                        </div>
                    </div>
                    
                    <!-- Status -->
                    <div style="background: #ecfdf5; border-radius: 8px; padding: 20px; margin-bottom: 30px; border: 1px solid #d1fae5;">
                        <span style="color: #065f46; font-weight: 600; font-size: 16px;">⚡ Transfer In Progress</span>
                        <p style="color: #059669; margin: 10px 0 0 0; font-size: 14px;">Your NGN is being transferred to your bank account. Expected arrival: 2-10 minutes.</p>
                    </div>
                    
                    <!-- Next Steps -->
                    <div style="background: #fef3c7; border-radius: 8px; padding: 20px; border: 1px solid #fde68a;">
                        <h3 style="color: #92400e; margin: 0 0 15px 0; font-size: 16px; font-weight: 600;">📱 What's Next?</h3>
                        <ul style="color: #b45309; margin: 0; padding-left: 20px; line-height: 1.6;">
                            <li>Check your bank account in 2-10 minutes</li>
                            <li>Save the bank reference for your records</li>
                            <li>Contact support if you have any questions</li>
                        </ul>
                    </div>
                </div>
                
                <!-- Footer -->
                <div style="background: #f9fafb; padding: 25px 30px; border-radius: 0 0 12px 12px; text-align: center; border-top: 1px solid #e5e7eb;">
                    <p style="color: #6b7280; margin: 0; font-size: 14px;">Thank you for using {Config.PLATFORM_NAME} - Safe Money Exchange</p>
                    <p style="color: #9ca3af; margin: 5px 0 0 0; font-size: 12px;">This is an automated message. Please do not reply to this email.</p>
                </div>
            </div>
            """
            
            return self.send_email(to_email, subject, html_content)
            
        except Exception as e:
            logger.error(f"Failed to send exchange completion email: {e}")
            return False
    
    async def send_transfer_receipt_email(self, to_email: str, order, bank_reference: str) -> bool:
        """Send final transfer receipt email"""
        try:
            from datetime import datetime
            
            final_amount = getattr(order, 'final_amount', 0)
            subject = f"💰 Transfer Receipt - ₦{final_amount:,.2f} Completed"
            
            html_content = f"""
            <div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.1);">
                <!-- Header -->
                <div style="background: linear-gradient(135deg, #10b981 0%, #059669 100%); padding: 30px; border-radius: 12px 12px 0 0; text-align: center;">
                    <h1 style="color: white; margin: 0; font-size: 28px; font-weight: 700;">💰 Transfer Complete!</h1>
                    <p style="color: rgba(255,255,255,0.9); margin: 10px 0 0 0; font-size: 16px;">Your NGN transfer has been completed successfully</p>
                </div>
                
                <!-- Content -->
                <div style="padding: 40px 30px;">
                    <!-- Receipt Details -->
                    <div style="background: #f0fdf4; border-radius: 8px; padding: 25px; margin-bottom: 30px; border: 2px solid #10b981;">
                        <h2 style="color: #065f46; margin: 0 0 20px 0; font-size: 20px; font-weight: 600; text-align: center;">🧾 Transfer Receipt</h2>
                        
                        <div style="background: white; border-radius: 6px; padding: 20px;">
                            <div style="padding: 15px 0; border-bottom: 1px solid #e5e7eb;">
                                <span style="color: #6b7280; font-weight: 500;">Amount Transferred: </span>
                                <span style="color: #059669; font-weight: 700; font-size: 20px;">₦{final_amount:,.2f}</span>
                            </div>
                            
                            <div style="padding: 15px 0; border-bottom: 1px solid #e5e7eb;">
                                <span style="color: #6b7280; font-weight: 500;">Bank Reference: </span>
                                <span style="color: #7c3aed; font-weight: 600; font-family: monospace; background: #f3f4f6; padding: 6px 10px; border-radius: 4px;">{bank_reference}</span>
                            </div>
                            
                            <div style="padding: 15px 0; border-bottom: 1px solid #e5e7eb;">
                                <span style="color: #6b7280; font-weight: 500;">Completed At: </span>
                                <span style="color: #1f2937; font-weight: 600;">{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</span>
                            </div>
                            
                            <div style="padding: 15px 0;">
                                <span style="color: #6b7280; font-weight: 500;">Status: </span>
                                <span style="color: #059669; font-weight: 700; background: #dcfce7; padding: 4px 12px; border-radius: 20px; font-size: 14px;">✅ COMPLETED</span>
                            </div>
                        </div>
                    </div>
                    
                    <!-- Success Message -->
                    <div style="text-align: center; background: #ecfdf5; border-radius: 8px; padding: 25px; margin-bottom: 30px;">
                        <div style="font-size: 48px; margin-bottom: 15px;">🎉</div>
                        <h3 style="color: #065f46; margin: 0 0 10px 0; font-size: 18px; font-weight: 600;">Transfer Completed Successfully!</h3>
                        <p style="color: #059669; margin: 0; font-size: 14px;">Your NGN should now be available in your bank account.</p>
                    </div>
                    
                    <!-- Support -->
                    <div style="background: #f8fafc; border-radius: 8px; padding: 20px; text-align: center;">
                        <h3 style="color: #374151; margin: 0 0 15px 0; font-size: 16px; font-weight: 600;">Need Help?</h3>
                        <p style="color: #6b7280; margin: 0 0 15px 0; font-size: 14px;">If you have any questions about this transfer, please contact our support team.</p>
                        <p style="color: #9ca3af; margin: 0; font-size: 12px;">Keep this receipt for your records.</p>
                    </div>
                </div>
                
                <!-- Footer -->
                <div style="background: #f9fafb; padding: 25px 30px; border-radius: 0 0 12px 12px; text-align: center; border-top: 1px solid #e5e7eb;">
                    <p style="color: #6b7280; margin: 0; font-size: 14px;">Thank you for using {Config.PLATFORM_NAME} - Safe Money Exchange</p>
                    <p style="color: #9ca3af; margin: 5px 0 0 0; font-size: 12px;">This is an automated receipt. Please do not reply to this email.</p>
                </div>
            </div>
            """
            
            return self.send_email(to_email, subject, html_content)
            
        except Exception as e:
            logger.error(f"Failed to send transfer receipt email: {e}")
            return False

    async def send_otp_email(
        self,
        email: str,
        name: str,
        otp: str,
        purpose: str,
        amount: Optional[str] = None,
    ) -> bool:
        """Send OTP verification email for cashouts and other secure operations"""
        
        if not self.enabled:
            logger.info(f"Email service disabled - would send OTP to {email}")
            return False
            
        try:
            # Create subject based on purpose with proper formatting
            if purpose:
                if "ngn" in purpose.lower():
                    formatted_purpose = "Security Verification"  # Simple subject for NGN
                else:
                    formatted_purpose = purpose.replace("_", " ").title() + " Verification"
                subject = f"🔐 {formatted_purpose}"
            else:
                subject = f"🔐 {Config.PLATFORM_NAME} - Verification Code"
            
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Verification Code - {Config.PLATFORM_NAME}</title>
            </head>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; background-color: #f4f4f4;">
                <div style="max-width: 600px; margin: 20px auto; background: white; border-radius: 10px; overflow: hidden; box-shadow: 0 0 20px rgba(0,0,0,0.1);">
                    
                    <!-- Header -->
                    <div style="background: #dc3545; color: white; padding: 30px; text-align: center;">
                        <h1 style="margin: 0; font-size: 28px;">🔐 Security Verification</h1>
                        <p style="margin: 10px 0 0 0; font-size: 16px; opacity: 0.9;">{Config.PLATFORM_NAME}</p>
                    </div>
                    
                    <!-- Content -->
                    <div style="padding: 40px 30px;">
                        <h2 style="color: #333; margin: 0 0 20px 0;">Hello {name}!</h2>
                        
                        <p style="font-size: 16px; margin-bottom: 20px;">
                            {"A verification code is required to complete your " + purpose + "." if purpose else "Your verification code is ready."}
                            {f"<br><strong>Transaction Amount:</strong> {amount}" if amount else ""}
                        </p>
                        
                        <!-- OTP Code Box -->
                        <div style="background: #f8f9fa; padding: 25px; border-radius: 8px; border-left: 4px solid #dc3545; margin: 25px 0; text-align: center;">
                            <h3 style="margin: 0 0 15px 0; color: #333;">Your Verification Code:</h3>
                            <div style="font-size: 32px; font-weight: bold; color: #dc3545; letter-spacing: 8px; font-family: monospace; background: white; padding: 15px; border-radius: 5px; margin: 10px 0;">
                                {otp}
                            </div>
                            <p style="margin: 15px 0 0 0; color: #666; font-size: 14px;">
                                🕐 This code expires in <strong>15 minutes</strong>
                            </p>
                        </div>
                        
                        <!-- Security Notice -->
                        <div style="background: #fff3cd; padding: 20px; border-radius: 5px; border-left: 4px solid #ffc107; margin: 25px 0;">
                            <h4 style="margin: 0 0 10px 0; color: #856404;">🔒 Security Notice:</h4>
                            <ul style="margin: 0; padding-left: 20px; color: #856404; font-size: 14px; line-height: 1.5;">
                                <li>Never share this code with anyone</li>
                                <li>We will never ask for this code via phone or email</li>
                                <li>If you didn't request this code, please ignore this email</li>
                                <li>Enter the code only in the official {Config.PLATFORM_NAME} bot</li>
                            </ul>
                        </div>
                        
                        <!-- Action Button -->
                        <div style="text-align: center; margin: 30px 0;">
                            <a href="https://t.me/{Config.BOT_USERNAME}" 
                               style="display: inline-block; background: #dc3545; color: white; padding: 15px 30px; 
                                      text-decoration: none; border-radius: 25px; font-weight: bold; font-size: 16px;">
                                📱 Open Bot
                            </a>
                        </div>
                        
                        <p style="font-size: 14px; color: #666; text-align: center; margin: 30px 0 0 0;">
                            Need help? Contact our support team anytime.
                            <br>This verification code was sent for your security.
                        </p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            text_content = f"""
            {Config.PLATFORM_NAME} - Security Verification
            
            Hello {name},
            
            {"A verification code is required to complete your " + purpose.lower() + "." if purpose else "Your verification code is ready."}
            {f"Transaction Amount: {amount}" if amount else ""}
            
            Your Verification Code: {otp}
            
            ⏰ This code expires in 15 minutes
            
            🛡️ Security Notice:
            • Never share this code with anyone
            • We will never ask for this code via phone or email  
            • If you didn't request this code, please ignore this email
            • Enter the code only in the official {Config.PLATFORM_NAME} bot
            
            Open Bot: https://t.me/{Config.BOT_USERNAME}
            
            Need help? Contact our support team anytime.
            """
            
            return self.send_email(email, subject, text_content, html_content)
            
        except Exception as e:
            logger.error(f"Failed to send OTP email to {email}: {e}")
            return False
    
    async def send_verification_otp(self, email: str, user_id: int, verification_type: str, context_data: Optional[dict] = None) -> tuple[bool, str]:
        """Send OTP for verification with context data"""
        import secrets
        import string
        from models import User
        from datetime import datetime, timedelta
        from sqlalchemy import select
        
        try:
            # Generate 6-digit OTP
            otp_code = ''.join(secrets.choice(string.digits) for _ in range(6))
            
            # Get user details
            async with async_managed_session() as session:
                stmt = select(User).where(User.id == user_id)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if not user:
                    return False, "User not found"
                
                user_name = str(user.first_name) if user.first_name is not None else (str(user.username) if user.username is not None else "User")
            
            # Format purpose based on verification type
            purpose_map = {
                "cashout": f"{context_data.get('currency', 'Crypto')} Cashout" if context_data and context_data.get('currency') else "Crypto Cashout",
                "ngn_cashout": "ngn bank transfer",  # Keep lowercase as user expects
                "bank_verification": "Bank Account Verification",
                "crypto_withdrawal": "Crypto Withdrawal"
            }
            purpose = purpose_map.get(verification_type, "Account Verification")
            
            # Format amount if provided
            amount_text = None
            if context_data and context_data.get('amount'):
                amount = context_data['amount']
                try:
                    # For NGN cashouts, amount is already formatted in local currency (₦)
                    if verification_type == "ngn_cashout":
                        amount_text = str(amount)  # Use formatted amount: "₦2,972.58"
                    elif verification_type == "cashout":
                        # For crypto cashouts, amount is already formatted as crypto (like NGN)
                        # Just display it directly like NGN does (no additional formatting)
                        amount_text = str(amount)  # Use formatted amount: "0.0176 LTC"
                    elif isinstance(amount, (int, float)):
                        amount_text = f"${amount:.2f} USD"
                    else:
                        # Remove extra $ if already present to avoid double symbols
                        amount_str = str(amount)
                        if amount_str.startswith('$'):
                            amount_text = amount_str
                        else:
                            amount_text = f"${amount_str}"
                except (ValueError, TypeError):
                    # Fallback to string representation
                    amount_text = str(amount)
            
            # Send OTP email
            email_sent = await self.send_otp_email(
                email=email,
                name=user_name,
                otp=otp_code,
                purpose=purpose,
                amount=amount_text if amount_text is not None else ""
            )
            
            if email_sent:
                # Store OTP in database with expiration
                from database import SessionLocal
                
                session = SessionLocal()
                try:
                    # CRITICAL FIX: Atomic UPSERT to prevent race conditions
                    # Use INSERT...ON CONFLICT DO UPDATE for PostgreSQL atomic operation
                    import json
                    from sqlalchemy import text
                    
                    serialized_context = json.dumps(context_data) if context_data else None
                    current_time = datetime.now()
                    expires_time = current_time + timedelta(minutes=15)
                    
                    # Atomic UPSERT query - no race condition possible
                    # First, try to create the constraint if it doesn't exist
                    try:
                        session.execute(text("""
                            ALTER TABLE otp_verifications 
                            ADD CONSTRAINT uq_otp_user_type 
                            UNIQUE (user_id, verification_type)
                        """))
                        session.commit()
                        logger.info("✅ Added unique constraint for OTP race condition prevention")
                    except Exception:
                        session.rollback()  # Constraint might already exist
                    
                    # Atomic UPSERT query - works with existing schema
                    upsert_query = text("""
                        INSERT INTO otp_verifications 
                        (user_id, email, otp_code, verification_type, context_data, expires_at, created_at)
                        VALUES (:user_id, :email, :otp_code, :verification_type, :context_data, :expires_at, :created_at)
                        ON CONFLICT (user_id, verification_type) 
                        DO UPDATE SET
                            email = EXCLUDED.email,
                            otp_code = EXCLUDED.otp_code, 
                            context_data = EXCLUDED.context_data,
                            expires_at = EXCLUDED.expires_at,
                            created_at = EXCLUDED.created_at
                    """)
                    
                    session.execute(upsert_query, {
                        'user_id': user_id,
                        'email': email,
                        'otp_code': otp_code,
                        'verification_type': verification_type,
                        'context_data': serialized_context,
                        'expires_at': expires_time,
                        'created_at': current_time
                    })
                    session.commit()
                    
                    logger.info(f"✅ OTP sent to {email} for {verification_type} (User: {user_id})")
                    return True, "OTP sent successfully"
                    
                except Exception as db_error:
                    session.rollback()
                    logger.error(f"Failed to store OTP in database: {db_error}")
                    return False, "Failed to store verification code"
                finally:
                    session.close()
            else:
                return False, "Failed to send email"
                
        except Exception as e:
            logger.error(f"Failed to send verification OTP: {e}")
            return False, f"Verification failed: {str(e)}"
    
    async def verify_otp(self, email: str, otp: str, user_id: int, verification_type: str) -> tuple[bool, str, dict]:
        """Verify OTP code and return context data for cashout lookup"""
        from models import OTPVerification
        from datetime import datetime
        from sqlalchemy import select, text
        import json
        
        try:
            async with async_managed_session() as session:
                # Find valid OTP
                stmt = select(OTPVerification).where(
                    OTPVerification.user_id == user_id,
                    OTPVerification.email == email,
                    OTPVerification.verification_type == verification_type
                )
                result = await session.execute(stmt)
                otp_record = result.scalar_one_or_none()
                
                if not otp_record:
                    return False, "No verification code found", {}
                
                # Extract values to avoid Column type issues
                from datetime import datetime as dt
                expires_at: datetime = otp_record.expires_at  # type: ignore
                otp_code = str(otp_record.otp_code) if otp_record.otp_code is not None else ""
                context_data_str = str(otp_record.context_data) if otp_record.context_data is not None else None
                otp_id: int = otp_record.id  # type: ignore
                
                # Check if expired
                if dt.now() > expires_at:
                    # ATOMIC DELETE for expired OTP
                    await session.execute(text("""
                        DELETE FROM otp_verifications 
                        WHERE user_id = :user_id 
                        AND verification_type = :verification_type 
                        AND expires_at < :current_time
                    """), {
                        'user_id': user_id,
                        'verification_type': verification_type,
                        'current_time': datetime.now()
                    })
                    await session.commit()
                    return False, "Verification code has expired", {}
                
                # Check if code matches
                if otp_code != otp:
                    return False, "Invalid verification code", {}
                
                # Extract context data before marking as used
                # SECURITY FIX: Removed dangerous eval() - use secure parsing only
                import ast
                context_data = {}
                if context_data_str:
                    try:
                        # First try JSON parsing (for new records)
                        context_data = json.loads(context_data_str)
                        logger.info(f"🔍 OTP context_data retrieved via JSON: {context_data}")
                    except (json.JSONDecodeError, ValueError, TypeError) as json_error:
                        logger.warning(f"JSON parsing failed: {json_error}")
                        try:
                            # SECURE FALLBACK: Use ast.literal_eval for legacy dict strings
                            # This safely parses Python literals without code execution
                            context_data = ast.literal_eval(context_data_str)
                            logger.info(f"🔍 OTP context_data recovered via ast.literal_eval: {context_data}")
                        except (ValueError, SyntaxError) as ast_error:
                            logger.error(f"SECURITY: Could not safely parse context_data - using empty dict: {ast_error}")
                            logger.error(f"Raw context_data: {repr(context_data_str)}")
                            context_data = {}  # Safe fallback - no code execution risk
                
                # ATOMIC DELETE for used OTP - only delete the exact record we verified
                await session.execute(text("""
                    DELETE FROM otp_verifications 
                    WHERE id = :otp_id
                """), {'otp_id': otp_id})
                await session.commit()
                
                logger.info(f"✅ OTP verified successfully for user {user_id} ({verification_type})")
                return True, "OTP verified successfully", context_data
                
        except Exception as e:
            logger.error(f"Failed to verify OTP: {e}")
            return False, f"Verification failed: {str(e)}", {}


# Global email service instance  
email_service = EmailService()
