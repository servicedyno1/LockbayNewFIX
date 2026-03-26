"""
Comprehensive Admin Error Notification Service
Handles all types of cashout and transaction errors with specific templates and actionable information
"""

import logging
import time
import hashlib
import hmac
from datetime import datetime

from config import Config
from services.email import email_service
from utils.helpers import format_amount

logger = logging.getLogger(__name__)


class ComprehensiveAdminNotificationService:
    """Comprehensive service for all admin error notifications with specific templates per error type"""

    def __init__(self):
        self.enabled = Config.ADMIN_EMAIL_ALERTS and email_service.enabled
        self.admin_email = Config.ADMIN_ALERT_EMAIL
        
        if not self.enabled:
            logger.info("Comprehensive admin notifications disabled via configuration")
        else:
            logger.info(f"Comprehensive admin notifications enabled - sending to {self.admin_email}")

    @classmethod
    def generate_action_token(cls, transaction_id: str, action: str) -> str:
        """Generate secure token for admin action buttons"""
        try:
            # Check if admin email actions are enabled (secure secret available)
            if not getattr(Config, 'ADMIN_EMAIL_ACTIONS_ENABLED', False):
                logger.warning(f"🚨 SECURITY: Admin email actions disabled - cannot generate token for {action} on {transaction_id}")
                return "DISABLED_FOR_SECURITY"
            
            timestamp = int(time.time())
            secret_key = getattr(Config, 'ADMIN_EMAIL_SECRET')
            
            if not secret_key:
                logger.error("🚨 CRITICAL: ADMIN_EMAIL_SECRET not available for token generation")
                return "SECURITY_ERROR"
            
            token_data = f"admin_action:{action}:{transaction_id}:{timestamp}"
            
            signature = hmac.new(
                secret_key.encode('utf-8'),
                token_data.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            return f"{timestamp}:{signature}"
        except Exception as e:
            logger.error(f"Failed to generate admin action token: {e}")
            return "invalid_token"

    @classmethod
    def validate_action_token(cls, transaction_id: str, action: str, token: str) -> bool:
        """Validate admin action token"""
        try:
            # Check for security-disabled tokens
            if token in ["DISABLED_FOR_SECURITY", "SECURITY_ERROR", "invalid_token"]:
                logger.warning(f"🚨 SECURITY: Invalid token type '{token}' for {action} on {transaction_id}")
                return False
            
            if not token or ':' not in token:
                return False
                
            timestamp_str, signature = token.split(':', 1)
            timestamp = int(timestamp_str)
            
            # Check if token is expired (24 hours)
            current_time = int(time.time())
            if current_time - timestamp > 86400:  # 24 hours in seconds
                logger.warning(f"Expired admin action token for {action} on {transaction_id}")
                return False
            
            # Check if admin email actions are enabled
            if not getattr(Config, 'ADMIN_EMAIL_ACTIONS_ENABLED', False):
                logger.warning(f"🚨 SECURITY: Admin email actions disabled - token validation refused for {action} on {transaction_id}")
                return False
            
            secret_key = getattr(Config, 'ADMIN_EMAIL_SECRET')
            if not secret_key:
                logger.error("🚨 CRITICAL: ADMIN_EMAIL_SECRET not available for token validation")
                return False
            
            token_data = f"admin_action:{action}:{transaction_id}:{timestamp}"
            
            expected_signature = hmac.new(
                secret_key.encode('utf-8'),
                token_data.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            is_valid = hmac.compare_digest(signature, expected_signature)
            if not is_valid:
                logger.warning(f"Invalid admin action token for {action} on {transaction_id}")
            
            return is_valid
            
        except Exception as e:
            logger.error(f"Error validating admin action token: {e}")
            return False

    def _create_email_template(self, alert_type: str, subject: str, content: str, urgency: str = "normal") -> str:
        """Create HTML email template for admin notifications"""
        urgency_colors = {
            "low": "#28a745",      # Green
            "normal": "#007bff",   # Blue  
            "high": "#fd7e14",     # Orange
            "critical": "#dc3545"  # Red
        }
        
        urgency_icons = {
            "low": "ℹ️", 
            "normal": "📊", 
            "high": "⚠️", 
            "critical": "🚨"
        }
        
        color = urgency_colors.get(urgency, "#007bff")
        icon = urgency_icons.get(urgency, "📊")
        
        return f"""
        <div style="font-family: Arial, sans-serif; max-width: 700px; margin: 0 auto; padding: 20px;">
            <div style="background: {color}; color: white; padding: 20px; text-align: center; border-radius: 10px 10px 0 0;">
                <h1>{icon} Cashout Error Alert</h1>
                <p style="margin: 0; opacity: 0.9;">{Config.PLATFORM_NAME} Admin Notification</p>
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
        if user_data.get("email"):
            user_info.append(f"Email: {user_data['email']}")

        return " • ".join(user_info) if user_info else "Unknown User"

    async def send_user_insufficient_balance_alert(
        self,
        cashout_id: str,
        requested_amount: float,
        available_balance: float,
        currency: str,
        user_data: dict,
        error_details: dict = None
    ) -> bool:
        """Send admin alert for user wallet insufficient balance"""
        
        if not self.enabled:
            return True

        try:
            error_details = error_details or {}
            user_info = self._format_user_info(user_data)
            shortage = requested_amount - available_balance
            
            # Generate action tokens
            refund_token = self.generate_action_token(cashout_id, "refund_partial")
            contact_token = self.generate_action_token(cashout_id, "contact_user")
            
            content = f"""
            <div style="background: #fff3cd; border: 1px solid #ffeaa7; border-radius: 8px; padding: 20px; margin: 20px 0;">
                <h2>💳 User Wallet Insufficient Balance</h2>
                <p style="margin: 5px 0; color: #856404; font-weight: bold;">📊 User Error - Requires User Action or Admin Decision</p>
                
                <div style="background: white; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <h3>📋 Transaction Details</h3>
                    <p><strong>🆔 Cashout ID:</strong> <code>{cashout_id}</code></p>
                    <p><strong>💰 Requested Amount:</strong> {format_amount(requested_amount, currency)}</p>
                    <p><strong>💳 Available Balance:</strong> {format_amount(available_balance, currency)}</p>
                    <p><strong>❌ Shortage:</strong> <span style="color: #dc3545;">{format_amount(shortage, currency)}</span></p>
                    <p><strong>👤 User:</strong> {user_info}</p>
                    <p><strong>⏰ Time:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
                </div>

                <div style="background: #e7f3ff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <h3>🔍 Analysis & Recommendations</h3>
                    <ul style="margin: 10px 0; padding-left: 20px;">
                        <li><strong>User Needs:</strong> {format_amount(shortage, currency)} more to complete withdrawal</li>
                        <li><strong>Possible Causes:</strong> Recent transaction reduced balance, user miscalculated available funds</li>
                        <li><strong>User Status:</strong> User sees "processing" status - unaware of balance issue</li>
                        <li><strong>Recommended Action:</strong> Contact user to deposit more funds or process partial refund</li>
                    </ul>
                </div>

                <div style="text-align: center; margin: 25px 0;">
                    <a href="{Config.WEBHOOK_URL}/admin/actions/contact_user/{cashout_id}?token={contact_token}" 
                       style="background: #007bff; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; margin: 0 10px; display: inline-block; font-weight: bold;">
                        📧 Contact User About Balance
                    </a>
                    
                    <a href="{Config.WEBHOOK_URL}/admin/actions/refund_partial/{cashout_id}?token={refund_token}" 
                       style="background: #28a745; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; margin: 0 10px; display: inline-block; font-weight: bold;">
                        💰 Process Available Balance Refund
                    </a>
                </div>

                <div style="background: #e2e3e5; padding: 10px; border-radius: 5px; margin: 15px 0; font-size: 12px;">
                    <strong>👤 User Status:</strong> User sees "processing" status - unaware of insufficient balance.<br>
                    <strong>💡 Recommendation:</strong> Contact user first to allow them to deposit shortage amount.<br>
                    <strong>⚡ Alternative:</strong> Process refund of available balance if user doesn't respond.<br>
                    <strong>🔒 Security:</strong> Action links expire in 24 hours and are cryptographically secured.
                </div>
            </div>
            """

            subject = f"💳 User Insufficient Balance - {format_amount(requested_amount, currency)} - {cashout_id}"
            html_content = self._create_email_template("USER_INSUFFICIENT_BALANCE", subject, content, urgency="high")

            success = email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )

            if success:
                logger.info(f"✅ User insufficient balance alert sent for {cashout_id} - shortage: {format_amount(shortage, currency)}")
            else:
                logger.error(f"❌ Failed to send user insufficient balance alert for {cashout_id}")

            return success

        except Exception as e:
            logger.error(f"Error sending user insufficient balance alert: {e}")
            return False

    async def send_invalid_address_alert(
        self,
        cashout_id: str,
        invalid_address: str,
        currency: str,
        user_data: dict,
        validation_error: str,
        error_details: dict = None
    ) -> bool:
        """Send admin alert for invalid destination address"""
        
        if not self.enabled:
            return True

        try:
            error_details = error_details or {}
            user_info = self._format_user_info(user_data)
            
            # Generate action tokens
            correct_token = self.generate_action_token(cashout_id, "request_correct_address")
            cancel_token = self.generate_action_token(cashout_id, "cancel_invalid_address")
            
            # Truncate long addresses for display
            display_address = invalid_address[:50] + "..." if len(invalid_address) > 50 else invalid_address
            
            content = f"""
            <div style="background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 8px; padding: 20px; margin: 20px 0;">
                <h2>🚫 Invalid Destination Address</h2>
                <p style="margin: 5px 0; color: #721c24; font-weight: bold;">📊 Address Validation Failed - User Input Error</p>
                
                <div style="background: white; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <h3>📋 Transaction Details</h3>
                    <p><strong>🆔 Cashout ID:</strong> <code>{cashout_id}</code></p>
                    <p><strong>💰 Currency:</strong> {currency}</p>
                    <p><strong>🚫 Invalid Address:</strong> <code style="color: #dc3545;">{display_address}</code></p>
                    <p><strong>❌ Validation Error:</strong> {validation_error}</p>
                    <p><strong>👤 User:</strong> {user_info}</p>
                    <p><strong>⏰ Time:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
                </div>

                <div style="background: #e7f3ff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <h3>🔍 Address Analysis & Requirements</h3>
                    <ul style="margin: 10px 0; padding-left: 20px;">
                        <li><strong>Currency Type:</strong> {currency} wallet address required</li>
                        <li><strong>Validation Issue:</strong> {validation_error}</li>
                        <li><strong>User Impact:</strong> Transaction cannot proceed with invalid address</li>
                        <li><strong>Resolution Needed:</strong> User must provide correct {currency} address</li>
                    </ul>
                    
                    <div style="background: #d1ecf1; padding: 10px; border-radius: 5px; margin: 10px 0;">
                        <strong>Valid {currency} Address Format:</strong><br>
                        {'• BTC: Starts with 1, 3, or bc1 (Bech32)' if currency == 'BTC' else ''}
                        {'• ETH: 42-character hex string starting with 0x' if currency == 'ETH' else ''}
                        {'• LTC: Starts with L, M, 3, or ltc1' if currency == 'LTC' else ''}
                        {'• Check specific format requirements for ' + currency if currency not in ['BTC', 'ETH', 'LTC'] else ''}
                    </div>
                </div>

                <div style="text-align: center; margin: 25px 0;">
                    <a href="{Config.WEBHOOK_URL}/admin/actions/request_correct_address/{cashout_id}?token={correct_token}" 
                       style="background: #007bff; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; margin: 0 10px; display: inline-block; font-weight: bold;">
                        📧 Request Correct Address
                    </a>
                    
                    <a href="{Config.WEBHOOK_URL}/admin/actions/cancel_invalid_address/{cashout_id}?token={cancel_token}" 
                       style="background: #dc3545; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; margin: 0 10px; display: inline-block; font-weight: bold;">
                        ❌ Cancel & Refund
                    </a>
                </div>

                <div style="background: #e2e3e5; padding: 10px; border-radius: 5px; margin: 15px 0; font-size: 12px;">
                    <strong>👤 User Status:</strong> User sees "processing" status - unaware of address validation failure.<br>
                    <strong>💡 Recommendation:</strong> Contact user to request correct {currency} address.<br>
                    <strong>⚡ Alternative:</strong> Cancel transaction and refund if user cannot provide valid address.<br>
                    <strong>🔒 Security:</strong> Action links expire in 24 hours and are cryptographically secured.
                </div>
            </div>
            """

            subject = f"🚫 Invalid Address - {currency} - {cashout_id}"
            html_content = self._create_email_template("INVALID_ADDRESS", subject, content, urgency="high")

            success = email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )

            if success:
                logger.info(f"✅ Invalid address alert sent for {cashout_id} - {currency} address validation failed")
            else:
                logger.error(f"❌ Failed to send invalid address alert for {cashout_id}")

            return success

        except Exception as e:
            logger.error(f"Error sending invalid address alert: {e}")
            return False

    async def send_api_authentication_alert(
        self,
        service_name: str,
        error_message: str,
        affected_transactions: list = None,
        error_details: dict = None
    ) -> bool:
        """Send admin alert for API authentication failures"""
        
        if not self.enabled:
            return True

        try:
            error_details = error_details or {}
            affected_transactions = affected_transactions or []
            
            # Generate action tokens
            check_token = self.generate_action_token(service_name, "check_api_credentials")
            
            # Assess impact
            impact_level = "CRITICAL" if len(affected_transactions) > 5 else "HIGH" if len(affected_transactions) > 0 else "MEDIUM"
            urgency = "critical" if impact_level == "CRITICAL" else "high"
            
            content = f"""
            <div style="background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 8px; padding: 20px; margin: 20px 0;">
                <h2>🔐 API Authentication Failure</h2>
                <p style="margin: 5px 0; color: #721c24; font-weight: bold;">📊 System Error - {impact_level} Impact</p>
                
                <div style="background: white; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <h3>📋 Authentication Failure Details</h3>
                    <p><strong>🏢 Service:</strong> {service_name}</p>
                    <p><strong>❌ Error Message:</strong> <code style="color: #dc3545;">{error_message}</code></p>
                    <p><strong>📊 Affected Transactions:</strong> {len(affected_transactions)} transactions impacted</p>
                    <p><strong>⚠️ Impact Level:</strong> <span style="color: {'#dc3545' if impact_level == 'CRITICAL' else '#fd7e14'};">{impact_level}</span></p>
                    <p><strong>⏰ Time:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
                </div>

                <div style="background: #e7f3ff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <h3>🔍 Impact Analysis & Troubleshooting</h3>
                    <ul style="margin: 10px 0; padding-left: 20px;">
                        <li><strong>Service Status:</strong> {service_name} API authentication failed</li>
                        <li><strong>Possible Causes:</strong> Expired API keys, revoked credentials, service account suspended</li>
                        <li><strong>User Impact:</strong> {"All new transactions will fail" if impact_level == "CRITICAL" else f"{len(affected_transactions)} transactions affected"}</li>
                        <li><strong>Urgency:</strong> {"IMMEDIATE attention required" if impact_level == "CRITICAL" else "Prompt resolution needed"}</li>
                    </ul>
                    
                    <div style="background: #d1ecf1; padding: 10px; border-radius: 5px; margin: 10px 0;">
                        <strong>Troubleshooting Steps:</strong><br>
                        1. Check {service_name} account status and permissions<br>
                        2. Verify API key expiration dates<br>
                        3. Confirm service account has required permissions<br>
                        4. Test API connectivity from development environment
                    </div>
                </div>

                {"<div style='background: #f8d7da; padding: 15px; border-radius: 5px; margin: 15px 0;'><h3>🚨 Affected Transactions</h3>" + "<br>".join([f"• {tx}" for tx in affected_transactions[:10]]) + ("..." if len(affected_transactions) > 10 else "") + "</div>" if affected_transactions else ""}

                <div style="text-align: center; margin: 25px 0;">
                    <a href="{Config.WEBHOOK_URL}/admin/actions/check_api_credentials/{service_name}?token={check_token}" 
                       style="background: #dc3545; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; margin: 0 10px; display: inline-block; font-weight: bold;">
                        🔧 Check API Configuration
                    </a>
                </div>

                <div style="background: #e2e3e5; padding: 10px; border-radius: 5px; margin: 15px 0; font-size: 12px;">
                    <strong>🚨 System Impact:</strong> {service_name} operations are currently failing.<br>
                    <strong>💡 Action Required:</strong> Verify and update API credentials immediately.<br>
                    <strong>📧 Monitoring:</strong> Additional alerts will be sent if issue persists.<br>
                    <strong>🔒 Security:</strong> Action links expire in 24 hours and are cryptographically secured.
                </div>
            </div>
            """

            subject = f"🔐 API AUTH FAILURE - {service_name} - {impact_level} Impact"
            html_content = self._create_email_template("API_AUTHENTICATION_FAILED", subject, content, urgency=urgency)

            success = email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )

            if success:
                logger.info(f"✅ API authentication failure alert sent for {service_name} - impact: {impact_level}")
            else:
                logger.error(f"❌ Failed to send API authentication alert for {service_name}")

            return success

        except Exception as e:
            logger.error(f"Error sending API authentication alert: {e}")
            return False

    async def send_generic_provider_funding_alert(
        self,
        provider_name: str,
        cashout_id: str,
        amount: float,
        currency: str,
        user_data: dict,
        error_message: str,
        provider_config: dict = None
    ) -> bool:
        """Send admin alert for generic service provider funding issues"""
        
        if not self.enabled:
            return True

        try:
            provider_config = provider_config or {}
            user_info = self._format_user_info(user_data)
            
            # Generate action tokens
            fund_token = self.generate_action_token(cashout_id, "fund_generic_provider")
            cancel_token = self.generate_action_token(cashout_id, "cancel_provider_funding")
            
            content = f"""
            <div style="background: #fff3cd; border: 1px solid #ffeaa7; border-radius: 8px; padding: 20px; margin: 20px 0;">
                <h2>💰 Provider Funding Required</h2>
                <p style="margin: 5px 0; color: #856404; font-weight: bold;">📊 Service Provider Insufficient Funds</p>
                
                <div style="background: white; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <h3>📋 Transaction Details</h3>
                    <p><strong>🆔 Cashout ID:</strong> <code>{cashout_id}</code></p>
                    <p><strong>🏢 Provider:</strong> {provider_name}</p>
                    <p><strong>💰 Amount:</strong> {format_amount(amount, currency)}</p>
                    <p><strong>❌ Error:</strong> <code style="color: #dc3545;">{error_message}</code></p>
                    <p><strong>👤 User:</strong> {user_info}</p>
                    <p><strong>⏰ Time:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
                </div>

                <div style="background: #e7f3ff; padding: 15px; border-radius: 5px; margin: 15px 0;">
                    <h3>🔍 Provider Funding Instructions</h3>
                    <ul style="margin: 10px 0; padding-left: 20px;">
                        <li><strong>Provider Account:</strong> {provider_name} needs funding for {currency} transactions</li>
                        <li><strong>Required Amount:</strong> At least {format_amount(amount, currency)} to complete this transaction</li>
                        <li><strong>Account Access:</strong> Log into {provider_name} dashboard/admin panel</li>
                        <li><strong>Funding Method:</strong> {'Bank transfer, crypto deposit, or account top-up' if not provider_config.get('funding_method') else provider_config['funding_method']}</li>
                    </ul>
                    
                    <div style="background: #d1ecf1; padding: 10px; border-radius: 5px; margin: 10px 0;">
                        <strong>Next Steps:</strong><br>
                        1. Access {provider_name} admin/dashboard<br>
                        2. Check current {currency} balance<br>
                        3. Add sufficient funds to cover this and future transactions<br>
                        4. Use "Fund & Complete" button below to retry transaction
                    </div>
                </div>

                <div style="text-align: center; margin: 25px 0;">
                    <a href="{Config.WEBHOOK_URL}/admin/actions/fund_generic_provider/{cashout_id}?token={fund_token}" 
                       style="background: #fd7e14; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; margin: 0 10px; display: inline-block; font-weight: bold;">
                        💰 Fund & Complete Transaction
                    </a>
                    
                    <a href="{Config.WEBHOOK_URL}/admin/actions/cancel_provider_funding/{cashout_id}?token={cancel_token}" 
                       style="background: #dc3545; color: white; padding: 12px 25px; text-decoration: none; border-radius: 5px; margin: 0 10px; display: inline-block; font-weight: bold;">
                        ❌ Cancel & Refund
                    </a>
                </div>

                <div style="background: #e2e3e5; padding: 10px; border-radius: 5px; margin: 15px 0; font-size: 12px;">
                    <strong>👤 User Status:</strong> User sees "processing" status - unaware of funding issue.<br>
                    <strong>💡 Recommendation:</strong> Fund {provider_name} account and use retry button.<br>
                    <strong>⚡ Alternative:</strong> Cancel transaction and refund user if funding not possible.<br>
                    <strong>🔒 Security:</strong> Action links expire in 24 hours and are cryptographically secured.
                </div>
            </div>
            """

            subject = f"💰 {provider_name} Funding Required - {format_amount(amount, currency)} - {cashout_id}"
            html_content = self._create_email_template("PROVIDER_FUNDING_REQUIRED", subject, content, urgency="high")

            success = email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )

            if success:
                logger.info(f"✅ Generic provider funding alert sent for {provider_name} - {cashout_id}")
            else:
                logger.error(f"❌ Failed to send generic provider funding alert for {cashout_id}")

            return success

        except Exception as e:
            logger.error(f"Error sending generic provider funding alert: {e}")
            return False


# Global instance for easy import
comprehensive_admin_notifications = ComprehensiveAdminNotificationService()