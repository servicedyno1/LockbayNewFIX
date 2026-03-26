"""
Smart Admin Funding Notification Service
Provides informational notifications with auto-retry status when external services need funding.
Transformed from "action required" to "informational + emergency override" approach.
"""

import logging
import time
import hashlib
import hmac
from typing import Optional
from datetime import datetime

from config import Config
from models import CashoutType
from utils.helpers import format_amount

# Email service for admin notifications
from services.email import EmailService

# UNIFIED NOTIFICATION SYSTEM INTEGRATION
from services.consolidated_notification_service import (
    ConsolidatedNotificationService
)

logger = logging.getLogger(__name__)


class AdminFundingNotificationService:
    """Smart service for informational admin notifications with auto-retry status.
    Transformed from urgent action emails to informational monitoring with emergency override capability."""

    def __init__(self):
        self.notification_service = ConsolidatedNotificationService()
        self.email_service = EmailService()
        self.enabled = Config.ADMIN_EMAIL_ALERTS
        self.admin_email = Config.ADMIN_ALERT_EMAIL
        
        if not self.enabled:
            logger.info("Admin funding notifications disabled via configuration")
        else:
            logger.info(f"Admin funding notifications enabled - sending to {self.admin_email}")

    @classmethod
    def generate_funding_token(cls, cashout_id: str, action: str) -> str:
        """Generate secure token for funding action buttons"""
        try:
            # Create token data
            timestamp = int(time.time())
            
            # Check if admin email actions are enabled (secure secret available)
            if not getattr(Config, 'ADMIN_EMAIL_ACTIONS_ENABLED', False):
                logger.warning(f"🚨 SECURITY: Admin email actions disabled - cannot generate funding token for {action} on {cashout_id}")
                return "DISABLED_FOR_SECURITY"
            
            # Create signature using HMAC with secret key
            secret_key = getattr(Config, 'ADMIN_EMAIL_SECRET')
            
            if not secret_key:
                logger.error("🚨 CRITICAL: ADMIN_EMAIL_SECRET not available for funding token generation")
                return "SECURITY_ERROR"
            
            # Token data includes action, cashout ID and timestamp
            token_data = f"funding:{action}:{cashout_id}:{timestamp}"
            
            # Create HMAC signature
            signature = hmac.new(
                secret_key.encode('utf-8'),
                token_data.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            # Combine data and signature
            token = f"{timestamp}.{signature}"
            
            logger.info(f"Generated funding token for {action} on cashout {cashout_id}")
            return token
            
        except Exception as e:
            logger.error(f"Error generating funding token: {e}")
            return "invalid_token"

    @classmethod
    def validate_funding_token(cls, cashout_id: str, action: str, token: str) -> bool:
        """Validate funding action token"""
        try:
            # Parse token
            if '.' not in token:
                logger.warning(f"Invalid funding token format for {action} on {cashout_id}")
                return False
            
            timestamp_str, signature = token.split('.', 1)
            timestamp = int(timestamp_str)
            
            # Check if token is expired (24 hours)
            current_time = int(time.time())
            token_age_hours = (current_time - timestamp) / 3600
            
            if token_age_hours > 24:
                logger.warning(f"Expired funding token for {action} on {cashout_id} (age: {token_age_hours:.1f}h)")
                return False
            
            # Check if admin email actions are enabled
            if not getattr(Config, 'ADMIN_EMAIL_ACTIONS_ENABLED', False):
                logger.warning(f"🚨 SECURITY: Admin email actions disabled - funding token validation refused for {action} on {cashout_id}")
                return False
            
            # Validate signature
            secret_key = getattr(Config, 'ADMIN_EMAIL_SECRET')
            if not secret_key:
                logger.error("🚨 CRITICAL: ADMIN_EMAIL_SECRET not available for funding token validation")
                return False
            token_data = f"funding:{action}:{cashout_id}:{timestamp}"
            
            expected_signature = hmac.new(
                secret_key.encode('utf-8'),
                token_data.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            if not hmac.compare_digest(signature, expected_signature):
                logger.warning(f"Invalid funding token signature for {action} on {cashout_id}")
                return False
            
            logger.info(f"Valid funding token for {action} on {cashout_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error validating funding token: {e}")
            return False

    def _format_retry_timeline(self, next_retry_seconds: int) -> str:
        """Format next retry time in human-readable format"""
        if next_retry_seconds < 60:
            return f"{next_retry_seconds} seconds"
        elif next_retry_seconds < 3600:
            minutes = next_retry_seconds // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''}"
        else:
            hours = next_retry_seconds // 3600
            minutes = (next_retry_seconds % 3600) // 60
            if minutes > 0:
                return f"{hours}h {minutes}m"
            return f"{hours} hour{'s' if hours != 1 else ''}"

    def _create_auto_retry_status(self, attempt: int, max_attempts: int, next_retry_time: str, is_retryable: bool) -> str:
        """Create auto-retry status display for email"""
        if not is_retryable:
            return """
            <div style="background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 5px; padding: 15px; margin: 15px 0;">
                <h3>🔴 Manual Resolution Required</h3>
                <p>This error requires manual admin intervention - auto-retry is not applicable.</p>
            </div>
            """

        remaining = max_attempts - attempt
        progress_percentage = (attempt / max_attempts) * 100

        if attempt >= max_attempts:
            return """
            <div style="background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 5px; padding: 15px; margin: 15px 0;">
                <h3>🔴 Auto-Retry Exhausted</h3>
                <p>All automatic retry attempts have been completed. Manual intervention now required.</p>
            </div>
            """

        return f"""
        <div style="background: #d1ecf1; border: 1px solid #bee5eb; border-radius: 5px; padding: 15px; margin: 15px 0;">
            <h3>🔄 Auto-Retry Active</h3>
            <div style="margin: 10px 0;">
                <strong>📊 Progress:</strong> Attempt {attempt} of {max_attempts}
                <div style="background: #e9ecef; border-radius: 10px; height: 20px; margin: 5px 0;">
                    <div style="background: #007bff; height: 20px; border-radius: 10px; width: {progress_percentage:.1f}%;"></div>
                </div>
            </div>
            <p><strong>⏰ Next Retry:</strong> In {next_retry_time}</p>
            <p><strong>🤖 System Status:</strong> Monitoring and retrying automatically</p>
            <p><strong>🎯 Expected Outcome:</strong> Will succeed once funding is added (remaining attempts: {remaining})</p>
        </div>
        """

    def _should_show_emergency_buttons(self, error_code: str, is_retryable: bool, attempt: int, max_attempts: int) -> bool:
        """Determine if emergency override buttons should be shown"""
        # Show emergency buttons only in these scenarios:
        # 1. Non-retryable errors that need manual resolution
        # 2. Retryable errors that have exhausted all auto-attempts
        # 3. Critical situations that may need immediate override
        
        if not is_retryable:
            return True  # Manual resolution required
            
        if attempt >= max_attempts:
            return True  # Auto-retry exhausted
            
        # For retryable errors with remaining attempts, hide buttons
        # (admin should fund account and let auto-retry succeed)
        return False

    def _create_emergency_override_section(self, cashout_id: str, service: str, fund_token: str, cancel_token: str) -> str:
        """Create action buttons section for immediate retry after funding"""
        return f"""
        <div style="background: #d4edda; border: 1px solid #c3e6cb; border-radius: 8px; padding: 20px; margin: 20px 0;">
            <h3>🔧 Admin Action Required</h3>
            <p><strong>Next Steps:</strong> Fund the {service} account, then click "Retry Transaction" to complete the cashout immediately.</p>
            
            <div style="margin: 15px 0;">
                <p><strong>📋 Instructions:</strong></p>
                <ul>
                    <li><strong>Retry Transaction:</strong> Click this after funding {service} to immediately complete the cashout</li>
                    <li><strong>Cancel & Refund:</strong> Refund user and cancel the cashout if unable to process</li>
                </ul>
            </div>

            <div style="text-align: center; margin: 25px 0;">
                <a href="{Config.WEBHOOK_URL}/admin/funding/fund_and_complete/{cashout_id}?token={fund_token}" 
                   style="background: #28a745; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; margin: 0 10px; display: inline-block; font-weight: bold;">
                    ✅ Retry Transaction
                </a>
                
                <a href="{Config.WEBHOOK_URL}/admin/funding/cancel_and_refund/{cashout_id}?token={cancel_token}" 
                   style="background: #dc3545; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; margin: 0 10px; display: inline-block; font-weight: bold;">
                    ❌ Cancel & Refund
                </a>
            </div>

            <div style="background: #e2e3e5; padding: 10px; border-radius: 5px; margin: 15px 0; font-size: 12px;">
                <strong>🔧 Next Steps:</strong> Fund {service}, then click "Retry Transaction" to complete the cashout.<br>
                <strong>🔒 Security:</strong> Action links expire in 24 hours and are cryptographically secured.
            </div>
        </div>
        """

    def _create_funding_instructions_section(self, service: str, funding_instructions: str) -> str:
        """Create funding instructions section (replaces action buttons for auto-retryable errors)"""
        return f"""
        <div style="background: #d4edda; border: 1px solid #c3e6cb; border-radius: 8px; padding: 20px; margin: 20px 0;">
            <h3>💡 How to Resolve (Recommended)</h3>
            <div style="margin: 15px 0;">
                {funding_instructions}
            </div>
            
            <div style="background: #f8f9fa; padding: 15px; border-radius: 5px; margin: 15px 0;">
                <h4>✅ What Happens Next:</h4>
                <ol style="margin: 10px 0; padding-left: 20px;">
                    <li>🏦 You add funds to your {service} account</li>
                    <li>🤖 System detects funding and automatically retries</li>
                    <li>✅ Cashout completes successfully without manual action</li>
                    <li>📧 You receive success confirmation email</li>
                </ol>
            </div>
            
            <p style="margin: 10px 0; font-style: italic; color: #6c757d;">
                💡 <strong>Pro Tip:</strong> Keep sufficient balance in {service} to avoid future interruptions. 
                The system will handle all retries automatically.
            </p>
        </div>
        """

    async def send_address_configuration_alert(
        self,
        cashout_id: str,
        currency: str,
        address: str,
        user_data: dict,
        amount: float = 0.0,
        error_details: Optional[str] = None,
        cashout_status: Optional[str] = None,
        crypto_amount: Optional[float] = None,
        net_usd_amount: Optional[float] = None
    ) -> bool:
        """Send admin alert when Kraken address needs to be configured with action buttons"""
        
        if not self.enabled:
            logger.debug("Address config alert skipped - admin email alerts disabled")
            return True

        try:
            user_info = self._format_user_info(user_data)
            
            # Check if cashout is in terminal state (don't send retry links for completed cashouts)
            is_terminal_state = cashout_status in ['success', 'failed', 'cancelled'] if cashout_status else False
            
            # Generate action tokens for admin buttons (only for non-terminal states)
            retry_token = None if is_terminal_state else self.generate_funding_token(cashout_id, "retry_after_address_config")
            cancel_token = None if is_terminal_state else self.generate_funding_token(cashout_id, "cancel_address_config")
            
            # Get user's recent transaction history (fetch database user.id from telegram_id)
            db_user_id = self._get_database_user_id(user_data.get('telegram_id', 0))
            recent_transactions = self._get_user_recent_transactions(db_user_id)
            
            # Display full address for admin visibility
            display_address = address
            
            # Format amount display - show both gross USD and net crypto amount
            if crypto_amount is not None:
                # Show the exact crypto amount to send (what user expects)
                amount_display = f"""
                    <p><strong>💵 Gross Amount:</strong> ${amount:.2f} USD</p>
                    <p><strong>💸 Network Fee:</strong> ${amount - (net_usd_amount or 0):.2f} USD</p>
                    <p style="background: #d4edda; padding: 10px; border-radius: 5px; font-size: 16px;">
                        <strong>🎯 SEND THIS AMOUNT:</strong> <span style="color: #155724; font-weight: bold;">~{crypto_amount:.8f} {currency}</span>
                    </p>
                """
            elif net_usd_amount is not None:
                # Show net USD if crypto amount not available
                amount_display = f"""
                    <p><strong>💵 Gross Amount:</strong> ${amount:.2f} USD</p>
                    <p><strong>💸 Net Amount (after fees):</strong> ${net_usd_amount:.2f} USD</p>
                """
            else:
                # Fallback to gross amount only
                amount_display = f"<p><strong>💰 Amount:</strong> ${amount:.2f} USD</p>"
            
            content = f"""
            <div style="background: #fff3cd; border: 1px solid #ffeaa7; border-radius: 8px; padding: 20px; margin: 20px 0;">
                <h2>🔐 Kraken Address Configuration Required</h2>
                <p style="margin: 5px 0; color: #856404; font-weight: bold;">📊 Address Setup Needed - Manual Action Required</p>
                
                <div style="background: white; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <h3>📋 Transaction Details</h3>
                    <p><strong>🆔 Cashout ID:</strong> <code>{cashout_id}</code></p>
                    <p><strong>💰 Currency:</strong> {currency}</p>
                    {amount_display}
                    <p><strong>📍 Address to Add:</strong> <code style="color: #0c5aa6;">{display_address}</code></p>
                    <p><strong>👤 User:</strong> {user_info}</p>
                    <p><strong>⏰ Time:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
                </div>

                <div style="background: #e7f3ff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <h3>🔧 Setup Instructions</h3>
                    <ol style="margin: 10px 0; padding-left: 20px;">
                        <li><strong>Log into Kraken:</strong> Access your Kraken exchange account</li>
                        <li><strong>Navigate to:</strong> Funding → Withdraw → {currency}</li>
                        <li><strong>Add Address:</strong> Add the above {currency} address to your address book</li>
                        <li><strong>Verify & Save:</strong> Complete any required verification steps</li>
                        <li><strong>Click Retry:</strong> Use the "Retry Transaction" button below</li>
                    </ol>
                </div>

                <div style="background: #f0f9ff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <h3>📊 Recent User Activity (Last 5 Transactions)</h3>
                    {recent_transactions}
                </div>

                <div style="text-align: center; margin: 25px 0;">
                    <a href="{Config.WEBHOOK_URL}/admin/address_config/retry/{cashout_id}?token={retry_token}" 
                       style="background: #28a745; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; margin: 0 10px; display: inline-block; font-weight: bold;">
                        ✅ Retry Transaction
                    </a>
                    
                    <a href="{Config.WEBHOOK_URL}/admin/address_config/cancel/{cashout_id}?token={cancel_token}" 
                       style="background: #dc3545; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; margin: 0 10px; display: inline-block; font-weight: bold;">
                        ❌ Cancel & Refund
                    </a>
                </div>

                <div style="background: #e2e3e5; padding: 10px; border-radius: 5px; margin: 15px 0; font-size: 12px;">
                    <strong>👤 User Status:</strong> User sees "processing" status - unaware of address configuration requirement.<br>
                    <strong>🔧 Next Steps:</strong> Add address to Kraken, then click "Retry Transaction" to complete the cashout.<br>
                    <strong>⚡ Alternative:</strong> Click "Cancel & Refund" if address cannot be added.<br>
                    <strong>🔒 Security:</strong> Action links expire in 24 hours and are cryptographically secured.
                </div>
            </div>
            """

            subject = f"🔐 Kraken Address Setup Required - {currency} - {cashout_id}"
            html_content = self._create_email_template("ADDRESS_CONFIG_REQUIRED", subject, content, urgency="high")

            success = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )

            if success:
                logger.info(f"✅ Address configuration alert sent for {cashout_id} - {currency} address setup required")
            else:
                logger.error(f"❌ Failed to send address configuration alert for {cashout_id}")

            return success

        except Exception as e:
            logger.error(f"Error sending address configuration alert: {e}")
            return False

    async def send_funding_required_alert(
        self,
        cashout_id: str,
        service: str,  # "Fincra" or "Kraken"
        amount: float,
        currency: str,
        user_data: dict,
        service_currency: Optional[str] = None,
        service_amount: Optional[float] = None,
        retry_info: Optional[dict] = None
    ) -> bool:
        """Send informational admin email when external service needs funding (auto-retry enabled)"""
        
        if not self.enabled:
            logger.debug("Funding alert skipped - admin email alerts disabled")
            return True

        try:
            # Get retry information for smart email logic
            retry_info = retry_info or {}
            error_code = retry_info.get('error_code', 'UNKNOWN')
            attempt_number = retry_info.get('attempt_number', 1)
            max_attempts = retry_info.get('max_attempts', 5)
            next_retry_seconds = retry_info.get('next_retry_seconds', 300)
            is_auto_retryable = retry_info.get('is_auto_retryable', True)
            
            # Generate tokens only for emergency override scenarios
            fund_token = self.generate_funding_token(cashout_id, "fund_and_complete")
            cancel_token = self.generate_funding_token(cashout_id, "cancel_and_refund")
            
            # Get additional cashout details (destination address)
            destination_address = self._get_cashout_destination(cashout_id)
            
            # Get user's recent transaction history (fetch database user.id from telegram_id)
            db_user_id = self._get_database_user_id(user_data.get('telegram_id', 0))
            recent_transactions = self._get_user_recent_transactions(db_user_id)
            
            # Format user information
            user_info = self._format_user_info(user_data)
            
            # Create service-specific content with auto-retry context
            if service.lower() == "fincra":
                service_icon = "🏦"
                service_name = "Fincra NGN"
                service_desc = "Nigerian Naira bank transfer"
                conversion_info = f"${amount:.2f} USD → ₦{service_amount:,.0f} NGN" if service_amount else f"${amount:.2f} USD"
                funding_instructions = "💡 <strong>How to Enable Auto-Success:</strong><br>• Log into your Fincra dashboard<br>• Add funds to your NGN wallet<br>• System will automatically retry and succeed"
            elif service.lower() == "kraken":
                service_icon = "🔐"
                service_name = f"Kraken {currency}"
                service_desc = f"{currency} cryptocurrency withdrawal"
                conversion_info = f"${amount:.2f} USD → {service_amount:.8f} {currency}" if service_amount else f"${amount:.2f} USD"
                funding_instructions = f"💡 <strong>How to Enable Auto-Success:</strong><br>• Log into your Kraken account<br>• Deposit {currency} to your wallet<br>• System will automatically retry and succeed"
            else:
                service_icon = "💰"
                service_name = service
                service_desc = f"{currency} withdrawal"
                conversion_info = f"{format_amount(amount, currency)}"
                funding_instructions = f"💡 <strong>How to Enable Auto-Success:</strong><br>• Add funds to {service} account<br>• System will automatically retry"      
            
            # Calculate auto-retry timeline
            next_retry_time = self._format_retry_timeline(next_retry_seconds)
            auto_retry_status = self._create_auto_retry_status(attempt_number, max_attempts, next_retry_time, is_auto_retryable)
            
            # CRITICAL FIX: Always show emergency buttons for admin intervention capability
            # Admin should always have override options available regardless of auto-retry status
            show_auto_retry_guidance = is_auto_retryable and attempt_number < max_attempts

            # Build smart email content with both auto-retry info AND emergency controls
            content = f"""
            <div style="background: #e7f3ff; border: 1px solid #b0d4f1; border-radius: 8px; padding: 20px; margin: 20px 0;">
                <h2>{service_icon} {service_name} Funding Status Update</h2>
                <p style="margin: 5px 0; color: #0c5aa6; font-weight: bold;">📊 Admin Notification - {"Auto-Retry Active" if show_auto_retry_guidance else "Manual Intervention Required"}</p>
                
                <div style="background: white; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <h3>📋 Transaction Details</h3>
                    <p><strong>🆔 Cashout ID:</strong> <code>{cashout_id}</code></p>
                    <p><strong>💰 Amount:</strong> {conversion_info}</p>
                    <p><strong>🎯 Service:</strong> {service_desc}</p>
                    <p><strong>📍 Destination:</strong> <code>{destination_address}</code></p>
                    <p><strong>👤 User:</strong> {user_info}</p>
                    <p><strong>⏰ Time:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
                    <p><strong>🔧 Error Code:</strong> {error_code}</p>
                </div>

                {auto_retry_status}

                <div style="background: #f0f9ff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <h3>📊 Recent User Activity (Last 5 Transactions)</h3>
                    {recent_transactions}
                </div>

                {self._create_funding_instructions_section(service_name, funding_instructions) if show_auto_retry_guidance else ""}
                {self._create_emergency_override_section(cashout_id, service_name, fund_token, cancel_token)}

                <div style="background: #e2e3e5; padding: 10px; border-radius: 5px; margin: 15px 0; font-size: 12px;">
                    <strong>👤 User Status:</strong> User sees "Processing" status - no failure indication.<br>
                    <strong>🤖 System Status:</strong> {"Auto-retry active - funding will auto-complete when resolved" if show_auto_retry_guidance else "Manual intervention required"}<br>
                    <strong>⚡ Admin Options:</strong> Emergency override buttons available above for immediate intervention if needed.<br>
                    <strong>📧 Next Update:</strong> You'll receive confirmation when {'auto-retry succeeds or' if show_auto_retry_guidance else ''} manual action is completed.
                </div>
            </div>
            """

            # Create and send email with appropriate urgency based on retry status
            if show_auto_retry_guidance:
                subject = f"💰 {service_name} Funding Required - Auto-Retry Active - {format_amount(amount, 'USD')} ({cashout_id})"
                urgency = "normal"  # Auto-retry active, normal priority
                alert_type = "FUNDING_REQUIRED_AUTO_RETRY"
            else:
                subject = f"🚨 {service_name} Funding Required - Manual Intervention - {format_amount(amount, 'USD')} ({cashout_id})"
                urgency = "high"  # No auto-retry, high priority
                alert_type = "FUNDING_REQUIRED_MANUAL"
                
            html_content = self._create_email_template(
                alert_type, 
                subject, 
                content, 
                urgency=urgency
            )

            success = self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )

            if success:
                if show_auto_retry_guidance:
                    logger.info(f"✅ Smart admin alert sent: {service} funding required with auto-retry active for {cashout_id} (attempt {attempt_number}/{max_attempts}) - emergency buttons included")
                else:
                    logger.info(f"✅ Smart admin alert sent: {service} funding required with manual intervention for {cashout_id} - emergency buttons included")
            else:
                logger.error(f"❌ Failed to send smart admin funding alert for {cashout_id}")

            return success

        except Exception as e:
            logger.error(f"Error sending admin funding alert: {e}")
            return False

    def _format_user_info(self, user_data: dict) -> str:
        """Format user information for email display"""
        if not user_data:
            return "Unknown User"

        user_info = []
        if user_data.get("username"):
            user_info.append(f"@{user_data['username']}")
        if user_data.get("first_name"):
            user_info.append(user_data["first_name"])
        if user_data.get("id"):
            user_info.append(f"ID: {user_data['id']}")
        if user_data.get("telegram_id"):
            user_info.append(f"TG: {user_data['telegram_id']}")

        return " • ".join(user_info) if user_info else "Unknown User"

    def _get_cashout_destination(self, cashout_id: str) -> str:
        """Get the destination address/account for a cashout"""
        try:
            from database import SessionLocal
            from models import Cashout
            
            # Use sync session for this helper method
            with SessionLocal() as session:
                cashout = session.query(Cashout).filter_by(cashout_id=cashout_id).first()
                
                if not cashout:
                    return "Unknown cashout"
                
                destination = cashout.destination or "Not specified"
                cashout_type = cashout.cashout_type or CashoutType.CRYPTO.value
                
                # Format destination based on cashout type (access attributes inside session)
                if cashout_type == CashoutType.NGN_BANK.value:
                    # For NGN cashouts, destination contains bank account info
                    if destination and destination != "Not specified":
                        return f"🏦 Bank Account: {destination}"
                    else:
                        return "🏦 Bank Account: Details pending"
                else:
                    # For crypto cashouts, destination is a wallet address
                    if destination and destination != "Not specified":
                        return f"🔐 Wallet: {destination}"
                    else:
                        return "🔐 Wallet: Address not specified"
            
        except Exception as e:
            logger.error(f"Error getting cashout destination: {e}")
            return "Error retrieving destination"

    def _get_database_user_id(self, telegram_id: int) -> int:
        """Get database user.id from telegram_id"""
        try:
            from database import SessionLocal
            from models import User
            
            with SessionLocal() as session:
                user = session.query(User).filter_by(telegram_id=telegram_id).first()
                if user:
                    return user.id
                return 0
        except Exception as e:
            logger.error(f"Error getting database user ID for telegram_id {telegram_id}: {e}")
            return 0
    
    def _get_user_recent_transactions(self, user_id: int) -> str:
        """Get user's last 5 transactions formatted for email"""
        try:
            from database import SessionLocal
            from models import Transaction
            
            # Use sync session for this helper method  
            with SessionLocal() as session:
                transactions = session.query(Transaction).filter_by(
                    user_id=user_id
                ).order_by(Transaction.created_at.desc()).limit(5).all()
                
                logger.info(f"📊 TRANSACTION_HISTORY: Fetched {len(transactions)} transactions for user {user_id}")
                
                if not transactions:
                    logger.warning(f"⚠️ NO_TRANSACTIONS: User {user_id} has no transaction history")
                    return "<p>No recent transactions found.</p>"
            
            transaction_html = "<table style='width: 100%; border-collapse: collapse; font-size: 12px;'>"
            transaction_html += """
                <tr style='background: #e9ecef;'>
                    <th style='border: 1px solid #ddd; padding: 8px; text-align: left;'>Date</th>
                    <th style='border: 1px solid #ddd; padding: 8px; text-align: left;'>Type</th>
                    <th style='border: 1px solid #ddd; padding: 8px; text-align: right;'>Amount</th>
                    <th style='border: 1px solid #ddd; padding: 8px; text-align: left;'>Description</th>
                </tr>
            """
            
            for tx in transactions:
                # Format amount with color coding
                amount_val = tx.amount
                tx_amount = float(amount_val) if amount_val is not None else 0.0  # type: ignore[arg-type]
                amount_color = "#d73027" if tx_amount < 0 else "#1a9641"
                amount_sign = "" if tx_amount < 0 else "+"
                amount_text = f"{amount_sign}{tx_amount:.2f} {tx.currency}"
                
                # Format date
                date_str = tx.created_at.strftime("%m/%d %H:%M")
                
                # Type emoji
                tx_type = str(tx.transaction_type)
                type_emoji = {
                    'deposit': '📥',
                    'cashout': '📤', 
                    'refund': '🔄',
                    'fee': '💸',
                    'escrow_hold': '🔒',
                    'escrow_release': '✅'
                }.get(tx_type, '💰')
                
                # Format description
                tx_desc = str(tx.description) if tx.description is not None else ""
                desc_display = f"{tx_desc[:50]}{'...' if len(tx_desc) > 50 else ''}"
                
                transaction_html += f"""
                    <tr>
                        <td style='border: 1px solid #ddd; padding: 6px;'>{date_str}</td>
                        <td style='border: 1px solid #ddd; padding: 6px;'>{type_emoji} {tx_type.title()}</td>
                        <td style='border: 1px solid #ddd; padding: 6px; text-align: right; color: {amount_color}; font-weight: bold;'>{amount_text}</td>
                        <td style='border: 1px solid #ddd; padding: 6px; font-size: 11px;'>{desc_display}</td>
                    </tr>
                """
            
            transaction_html += "</table>"
            
            return transaction_html
            
        except Exception as e:
            logger.error(f"Error getting user transactions: {e}")
            return f"<p style='color: #d73027;'>Error loading transaction history: {str(e)}</p>"

    def _create_email_template(
        self, alert_type: str, title: str, content: str, urgency: str = "normal"
    ) -> str:
        """Create HTML email template for admin alerts"""
        urgency_colors = {
            "low": "#28a745",  # Green
            "normal": "#007bff",  # Blue  
            "high": "#fd7e14",  # Orange
            "critical": "#dc3545",  # Red
        }

        urgency_icons = {"low": "ℹ️", "normal": "📊", "high": "⚠️", "critical": "🚨"}

        color = urgency_colors.get(urgency, "#007bff")
        icon = urgency_icons.get(urgency, "📊")

        return f"""
        <div style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto; padding: 20px;">
            <div style="background: {color}; color: white; padding: 20px; text-align: center; border-radius: 10px 10px 0 0;">
                <h1>{icon} {title}</h1>
                <p style="margin: 0; opacity: 0.9;">{Config.PLATFORM_NAME} Admin Alert</p>
            </div>
            <div style="background: #f8f9fa; padding: 25px; border-radius: 0 0 10px 10px; border: 1px solid #dee2e6;">
                {content}
                <hr style="margin: 20px 0; border: none; border-top: 1px solid #dee2e6;">
                <p style="margin: 0; color: #6c757d; font-size: 12px;">
                    <strong>Alert Time:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC<br>
                    <strong>Platform:</strong> {Config.PLATFORM_NAME}<br>
                    <strong>Alert Type:</strong> {alert_type}
                </p>
            </div>
        </div>
        """


# Initialize global instance
admin_funding_notifications = AdminFundingNotificationService()


# Convenience functions for backward compatibility with test imports
async def send_retry_exhausted_alert(transaction_id: str, **kwargs) -> bool:
    """Send alert when retry attempts are exhausted for a transaction"""
    try:
        from services.consolidated_notification_service import ConsolidatedNotificationService, NotificationPriority
        
        # Extract context from kwargs
        retry_count = kwargs.get('retry_count', 0)
        error_code = kwargs.get('error_code', 'UNKNOWN')
        error_message = kwargs.get('error_message', 'No error message provided')
        provider = kwargs.get('provider', 'unknown')
        context = kwargs.get('context', {})
        
        # Build comprehensive alert message
        retry_history = context.get('retry_history', [])
        retry_timeline = "\n".join([
            f"  - Attempt {r.get('attempt', 'N/A')}: {r.get('error', 'N/A')} at {r.get('timestamp', 'N/A')}"
            for r in retry_history
        ]) if retry_history else "No retry history available"
        
        message = f"""
🚨 RETRY EXHAUSTED - Manual Intervention Required

Transaction: {transaction_id}
Provider: {provider}
Total Attempts: {retry_count}
Error Code: {error_code}
Error: {error_message}

Retry History:
{retry_timeline}

Action Required:
- Review transaction details
- Investigate root cause ({error_code})
- Manually process or refund transaction
- Check provider service status: {provider}

This transaction requires immediate admin attention.
        """.strip()
        
        # Initialize notification service
        notification_service = ConsolidatedNotificationService()
        
        # Send admin alert
        await notification_service.send_admin_alert(
            title=f"🚨 Retry Exhausted: {transaction_id}",
            message=message,
            priority=NotificationPriority.HIGH,
            additional_data={
                "transaction_id": transaction_id,
                "retry_count": retry_count,
                "error_code": error_code,
                "error_message": error_message,
                "provider": provider,
                "retry_history": retry_history,
                "alert_type": "retry_exhausted"
            }
        )
        
        logger.info(f"✅ Retry exhausted alert sent for transaction {transaction_id} ({retry_count} attempts, {provider})")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to send retry exhausted alert for {transaction_id}: {e}")
        return False


async def send_funding_alert(service: str, cashout_id: str, **kwargs) -> bool:
    """Send funding alert for external service"""
    try:
        return await admin_funding_notifications.send_funding_required_alert(
            cashout_id=cashout_id, 
            service=service,
            **kwargs
        )
    except Exception as e:
        logger.error(f"❌ Failed to send funding alert for {service} cashout {cashout_id}: {e}")
        return False


async def send_address_whitelist_alert(service: str, address: str, cashout_id: str, **kwargs) -> bool:
    """Send alert when address needs to be whitelisted"""
    try:
        return await admin_funding_notifications.send_address_configuration_alert(
            cashout_id=cashout_id,
            currency=service, 
            address=address,
            **kwargs
        )
    except Exception as e:
        logger.error(f"❌ Failed to send address whitelist alert for {address}: {e}")
        return False