"""Admin Email Alert Service for instant transaction and system notifications"""

import logging
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

from config import Config
from services.email import email_service
from models import User, Cashout
from utils.helpers import format_amount

logger = logging.getLogger(__name__)


class AdminEmailAlertService:
    """Service for sending instant email alerts to admin for transactions and system events"""

    def __init__(self):
        self.enabled = Config.ADMIN_EMAIL_ALERTS and email_service.enabled
        self.admin_email = Config.ADMIN_ALERT_EMAIL
        self.threshold = Config.ADMIN_TRANSACTION_EMAIL_THRESHOLD
        self.frequency = Config.EMAIL_ALERT_FREQUENCY
        self.auto_cashout_alerts = Config.AUTO_CASHOUT_ADMIN_ALERTS

        if not self.enabled:
            logger.info("Admin email alerts disabled via configuration")
        else:
            logger.info(f"Admin email alerts enabled - sending to {self.admin_email}")

    def _should_send_alert(self, amount: float = 0.0) -> bool:
        """Check if alert should be sent based on threshold and configuration"""
        if not self.enabled:
            return False

        return amount >= self.threshold

    def _format_user_info(self, user: User) -> str:
        """
        Format user information for email display with robust DetachedInstanceError handling
        
        CRITICAL FIX: Prevents DetachedInstanceError when User objects lose session binding
        """
        if not user:
            return "Unknown User"

        user_info = []
        
        # Use inspect to check if the object is detached before accessing any attributes
        from sqlalchemy import inspect
        
        try:
            # Check if the user object is attached to a session
            state = inspect(user)
            if state.detached:
                logger.warning("User object is detached from session, using minimal info")
                # For detached objects, only access attributes that are already loaded
                try:
                    # For detached objects, only access attributes that are already loaded
                    # Check if attributes are in the object's __dict__ (already loaded)
                    if hasattr(user, 'id') and 'id' in user.__dict__ and user.id is not None:
                        user_info.append(f"ID: {user.id}")
                    
                    # Try telegram_id as it's usually eagerly loaded
                    if hasattr(user, 'telegram_id') and 'telegram_id' in user.__dict__ and user.telegram_id is not None:
                        user_info.append(f"TG: {user.telegram_id}")
                            
                except Exception as attr_error:
                    logger.debug(f"Could not access detached user attributes: {attr_error}")
                    pass
                    
                # Return what we could gather
                return " • ".join(user_info) if user_info else "User (detached from session)"
            
            # Object is attached to session, safe to access all attributes
            if hasattr(user, "username") and getattr(user, "username", None):
                user_info.append(f"@{getattr(user, 'username', '')}")
            if hasattr(user, "first_name") and getattr(user, "first_name", None):
                user_info.append(getattr(user, "first_name", ""))
            if hasattr(user, "id"):
                user_info.append(f"ID: {getattr(user, 'id', '')}")
            if hasattr(user, "telegram_id"):
                user_info.append(f"TG: {getattr(user, 'telegram_id', '')}")
                
        except Exception as e:
            # Comprehensive error handling for any SQLAlchemy session issues
            import sqlalchemy.orm.exc
            
            error_type = type(e).__name__
            if "DetachedInstanceError" in str(e) or isinstance(e, sqlalchemy.orm.exc.DetachedInstanceError):
                logger.warning(f"DetachedInstanceError in user formatting: {e}")
                return "User (session detached)"
            elif "InvalidRequestError" in str(e) or "ObjectDeletedError" in str(e):
                logger.warning(f"SQLAlchemy session error in user formatting: {error_type} - {e}")
                return "User (session error)"
            else:
                logger.error(f"Unexpected error formatting user info: {error_type} - {e}")
                return "User (error accessing info)"

        return " • ".join(user_info) if user_info else "Unknown User"

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
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
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

    async def send_transaction_alert(
        self,
        transaction_type: str,
        amount: float,
        currency: str,
        user: User,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send instant email alert for transaction events"""
        if not self._should_send_alert(amount):
            logger.debug(
                f"Transaction alert skipped - ${amount} below threshold ${self.threshold}"
            )
            return True

        details = details or {}
        user_info = self._format_user_info(user)

        # Determine urgency based on amount and type
        urgency = "normal"
        if amount >= 1000:
            urgency = "high"
        elif amount >= 5000:
            urgency = "critical"

        # Create content based on transaction type
        content_map = {
            "ESCROW_CREATED": {
                "title": "New Escrow Created",
                "icon": "💼",
                "description": "A new escrow trade has been initiated",
            },
            "DEPOSIT_CONFIRMED": {
                "title": "Deposit Confirmed",
                "icon": "💰",
                "description": "Cryptocurrency deposit has been confirmed",
            },
            "CASHOUT_REQUESTED": {
                "title": "Cashout Requested",
                "icon": "💸",
                "description": "User has requested a cashout",
            },
            "TRADE_COMPLETED": {
                "title": "Trade Completed",
                "icon": "✅",
                "description": "Escrow trade has been successfully completed",
            },
            "DISPUTE_OPENED": {
                "title": "Dispute Opened",
                "icon": "⚖️",
                "description": "A dispute has been opened for an escrow trade",
                "urgency": "high",
            },
            "CASHOUT_PROCESSED": {
                "title": "Cashout Processed",
                "icon": "🏦",
                "description": "Cashout has been successfully processed",
            },
            "CASHOUT_FAILED": {
                "title": "Cashout Failed",
                "icon": "❌",
                "description": "Cashout processing has failed",
                "urgency": "high",
            },
        }

        transaction_info = content_map.get(
            transaction_type,
            {
                "title": "Transaction Alert",
                "icon": "📊",
                "description": f"{transaction_type.replace('_', ' ').title()} event",
            },
        )

        # Override urgency if specified in transaction info
        if "urgency" in transaction_info:
            urgency = transaction_info["urgency"]

        # Build detailed content
        content_sections = [
            f"<h2>{transaction_info['icon']} {transaction_info['description']}</h2>",
            f"<p><strong>💰 Amount:</strong> {format_amount(amount, currency)}</p>",
            f"<p><strong>👤 User:</strong> {user_info}</p>",
        ]

        # Add specific details based on type
        if details:
            if "escrow_id" in details:
                content_sections.append(
                    f"<p><strong>🆔 Escrow ID:</strong> #{details['escrow_id']}</p>"
                )
            if "transaction_id" in details:
                content_sections.append(
                    f"<p><strong>🔗 Transaction ID:</strong> {details['transaction_id']}</p>"
                )
            if "network" in details:
                content_sections.append(
                    f"<p><strong>🌐 Network:</strong> {details['network']}</p>"
                )
            if "destination" in details:
                content_sections.append(
                    f"<p><strong>📍 Destination:</strong> {details['destination'][:20]}...</p>"
                )

        # Add admin action suggestions
        action_suggestions = self._get_action_suggestions(
            transaction_type, amount, details
        )
        if action_suggestions:
            content_sections.append(
                "<div style='background: #fff3cd; padding: 15px; border-radius: 5px; margin: 15px 0;'>"
            )
            content_sections.append(
                f"<strong>💡 Suggested Actions:</strong><br>{action_suggestions}"
            )
            content_sections.append("</div>")

        content = "".join(content_sections)

        # Create and send email
        subject = f"🚨 {transaction_info['title']} - {format_amount(amount, currency)}"
        html_content = self._create_email_template(
            transaction_type, subject, content, urgency
        )

        try:
            success = email_service.send_email(
                to_email=self.admin_email, subject=subject, html_content=html_content
            )

            if success:
                logger.info(
                    f"Admin transaction alert sent: {transaction_type} - ${amount} {currency}"
                )
            else:
                logger.error(
                    f"Failed to send admin transaction alert: {transaction_type}"
                )

            return success

        except Exception as e:
            logger.error(f"Error sending admin transaction alert: {e}")
            return False

    def _get_action_suggestions(
        self,
        transaction_type: str,
        amount: float,
        details: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Get suggested admin actions based on transaction type and details"""
        suggestions = []

        if transaction_type == "CASHOUT_REQUESTED":
            if amount >= 1000:
                suggestions.append("• Review high-value cashout for processing status")
            suggestions.append("• Monitor cashout processing for any backend issues")

        elif transaction_type == "DISPUTE_OPENED":
            suggestions.append("• Review dispute details and evidence")
            suggestions.append("• Consider contacting both parties")

        elif transaction_type == "CASHOUT_FAILED":
            suggestions.append("• Check cashout logs for failure reason")
            suggestions.append("• Verify user's cashout destination")
            suggestions.append("• Consider manual processing if needed")

        elif transaction_type == "ESCROW_CREATED" and amount >= 5000:
            suggestions.append("• Monitor high-value escrow progress")

        return "<br>".join(suggestions) if suggestions else ""

    async def send_auto_cashout_alert(
        self,
        alert_type: str,
        message: str,
        user: Optional[User] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Send email alerts when auto-cashout is OFF and admin intervention needed"""
        if not self.auto_cashout_alerts or not self.enabled:
            return True

        details = details or {}

        # Auto-cashout alert types and configurations
        alert_configs = {
            "MANUAL_APPROVAL_REQUIRED": {
                "title": "Manual Approval Required",
                "icon": "🔒",
                "urgency": "high",
                "description": "Cashout requires manual admin approval",
            },
            "PENDING_CASHOUT_QUEUE": {
                "title": "Pending Cashout Queue",
                "icon": "⏳",
                "urgency": "normal",
                "description": "Multiple cashouts awaiting approval",
            },
            "PROCESSING_FAILURE": {
                "title": "Auto-Processing Failed",
                "icon": "⚠️",
                "urgency": "high",
                "description": "Automatic processing failed, manual intervention needed",
            },
            "HIGH_VALUE_REVIEW": {
                "title": "High Value Transaction Review",
                "icon": "💎",
                "urgency": "high",
                "description": "High-value transaction requires review",
            },
            "FIRST_TIME_CASHOUT": {
                "title": "First-Time CashOut",
                "icon": "🆕",
                "urgency": "normal",
                "description": "New user's first cashout requires verification",
            },
        }

        alert_config = alert_configs.get(
            alert_type,
            {
                "title": "System Alert",
                "icon": "🔔",
                "urgency": "normal",
                "description": alert_type.replace("_", " ").title(),
            },
        )

        # Build content
        content_sections = [
            f"<h2>{alert_config['icon']} {alert_config['description']}</h2>",
            f"<p>{message}</p>",
        ]

        if user:
            user_info = self._format_user_info(user)
            content_sections.append(f"<p><strong>👤 User:</strong> {user_info}</p>")

        # Add specific details
        if details:
            if "pending_count" in details:
                content_sections.append(
                    f"<p><strong>📊 Pending Count:</strong> {details['pending_count']}</p>"
                )
            if "amount" in details:
                content_sections.append(
                    f"<p><strong>💰 Amount:</strong> {format_amount(details['amount'], 'USD')}</p>"
                )
            if "cashout_id" in details:
                content_sections.append(
                    f"<p><strong>🆔 CashOut ID:</strong> {details['cashout_id']}</p>"
                )

        # Add notification info box for streamlined approach
        if Config.CASHOUT_ADMIN_NOTIFICATIONS:
            content_sections.append(
                """
            <div style='background: #d1ecf1; padding: 15px; border-radius: 5px; margin: 15px 0; border-left: 4px solid #bee5eb;'>
                <strong>📋 Streamlined Processing Active:</strong><br>
                All cashouts provide immediate success UX. Backend processing issues trigger admin notifications.<br>
                <em>Check admin panel for any cashouts requiring configuration or manual intervention.</em>
            </div>
            """
            )

        content = "".join(content_sections)

        # Create and send email
        subject = f"🔔 {alert_config['title']} - {Config.PLATFORM_NAME}"
        html_content = self._create_email_template(
            alert_type, subject, content, alert_config["urgency"]
        )

        try:
            success = email_service.send_email(
                to_email=self.admin_email, subject=subject, html_content=html_content
            )

            if success:
                logger.info(f"Admin auto-cashout alert sent: {alert_type}")
            else:
                logger.error(f"Failed to send admin auto-cashout alert: {alert_type}")

            return success

        except Exception as e:
            logger.error(f"Error sending admin auto-cashout alert: {e}")
            return False

    async def send_system_status_alert(
        self, status_type: str, message: str, severity: str = "normal"
    ) -> bool:
        """Send system status alerts to admin"""
        if not self.enabled:
            return True

        status_configs = {
            "BALANCE_LOW": {
                "title": "Low Balance Alert",
                "icon": "📉",
                "urgency": "high",
            },
            "SERVICE_DOWN": {
                "title": "Service Disruption",
                "icon": "🚨",
                "urgency": "critical",
            },
            "SECURITY_ALERT": {
                "title": "Security Alert",
                "icon": "🛡️",
                "urgency": "critical",
            },
            "PERFORMANCE_ISSUE": {
                "title": "Performance Alert",
                "icon": "⚡",
                "urgency": "normal",
            },
        }

        config = status_configs.get(
            status_type, {"title": "System Alert", "icon": "⚠️", "urgency": severity}
        )

        content = f"<h2>{config['icon']} System Status Update</h2><p>{message}</p>"
        subject = f"🚨 {config['title']} - {Config.PLATFORM_NAME}"
        html_content = self._create_email_template(
            status_type, subject, content, config["urgency"]
        )

        try:
            success = email_service.send_email(
                to_email=self.admin_email, subject=subject, html_content=html_content
            )

            if success:
                logger.info(f"Admin system alert sent: {status_type}")

            return success

        except Exception as e:
            logger.error(f"Error sending admin system alert: {e}")
            return False

    async def check_pending_processing_issues(self, session: Session) -> bool:
        """Check for cashouts requiring admin attention due to processing issues"""
        if not Config.CASHOUT_ADMIN_NOTIFICATIONS or not self.auto_cashout_alerts:
            return True

        try:
            # Count cashouts with configuration or processing issues
            from models import CashoutStatus
            
            problem_cashouts = (
                session.query(Cashout).filter(
                    Cashout.status.in_([
                        CashoutStatus.FAILED.value,
                        # Only alert for genuine issues, not normal processing
                    ])
                ).count()
            )

            if problem_cashouts > 0:
                message = f"There are {problem_cashouts} cashouts requiring admin attention due to processing issues."

                await self.send_auto_cashout_alert(
                    "CASHOUT_PROCESSING_ISSUES",
                    message,
                    details={"issue_count": problem_cashouts},
                )

            return True

        except Exception as e:
            logger.error(f"Error checking cashout processing issues: {e}")
            return False

    async def send_failure_alert_with_actions(
        self,
        cashout_id: str,
        amount: float,
        currency: str,
        user_name: str,
        error_message: str,
        admin_user_id: Optional[int] = None,
        failure_reason: Optional[str] = None,
        user_telegram_id: Optional[int] = None
    ) -> bool:
        """
        Send failure alert email with secure action buttons
        
        Args:
            cashout_id: Failed cashout ID
            amount: Transaction amount
            currency: Transaction currency
            user_name: User's name
            error_message: Error that caused failure
            admin_user_id: Telegram admin ID if triggered from bot
            failure_reason: Additional failure context
            user_telegram_id: User's Telegram ID
            
        Returns:
            bool: True if email sent successfully
        """
        if not self.enabled:
            logger.debug("Admin email alerts disabled, skipping failure alert")
            return True

        try:
            from services.admin_failure_service import admin_failure_service
            from models import AdminActionType
            from utils.database_pool_manager import database_pool
            
            # Generate secure action tokens
            action_tokens = {}
            
            with database_pool.get_session("failure_alert_tokens") as session:
                for action in [AdminActionType.RETRY, AdminActionType.REFUND, AdminActionType.DECLINE]:
                    token = admin_failure_service.generate_secure_token(
                        session, cashout_id, action, self.admin_email, admin_user_id
                    )
                    if token:
                        action_tokens[action.value] = token.token
                    else:
                        logger.error(f"Failed to generate {action.value} token for cashout {cashout_id}")
            
            if not action_tokens:
                logger.error(f"No action tokens generated for cashout {cashout_id}")
                return False
            
            # Build action buttons HTML using the persistent admin action URL
            base_url = Config.ADMIN_ACTION_BASE_URL
            action_buttons_html = self._build_action_buttons_html(base_url, action_tokens)
            
            # Format error message for display
            display_error = error_message[:200] + "..." if len(error_message) > 200 else error_message
            
            # Build comprehensive email content
            content_sections = [
                "<h2>🚨 Transaction Failure Requiring Admin Intervention</h2>",
                "<p><strong>A cashout transaction has failed and requires immediate admin attention.</strong></p>",
                
                "<div style='background: #fff3cd; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #ffc107;'>",
                "<h3>💳 Transaction Details</h3>",
                f"<p><strong>🆔 Transaction ID:</strong> <code>{cashout_id}</code></p>",
                f"<p><strong>💰 Amount:</strong> {format_amount(amount, currency)}</p>",
                f"<p><strong>👤 User:</strong> {user_name}",
                
                # Add user Telegram ID if available
                f" (TG: {user_telegram_id})" if user_telegram_id else "",
                "</p>",
                f"<p><strong>⏰ Failed At:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>",
                "</div>",
                
                "<div style='background: #f8d7da; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #dc3545;'>",
                "<h3>❌ Error Information</h3>",
                f"<p><strong>Error Message:</strong> {display_error}</p>",
            ]
            
            if failure_reason:
                content_sections.append(f"<p><strong>Failure Reason:</strong> {failure_reason}</p>")
            
            content_sections.extend([
                "</div>",
                
                "<div style='background: #d1ecf1; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #17a2b8;'>",
                "<h3>⚡ Quick Actions</h3>",
                "<p><strong>Choose an action to resolve this transaction:</strong></p>",
                action_buttons_html,
                "</div>",
                
                "<div style='background: #f8f9fa; padding: 15px; border-radius: 8px; margin: 20px 0;'>",
                "<h4>🔒 Security Information</h4>",
                "<ul style='margin: 0; padding-left: 20px;'>",
                "<li>Action links are secure and expire in 24 hours</li>",
                "<li>Each link can only be used once</li>",
                "<li>All actions are logged with admin identification</li>",
                "<li>Alternative: Use Telegram bot <code>/admin_failures</code> command</li>",
                "</ul>",
                "</div>",
                
                "<div style='background: #e2e3e5; padding: 15px; border-radius: 8px; margin: 20px 0;'>",
                "<h4>📋 Action Descriptions</h4>",
                "<ul style='margin: 0; padding-left: 20px;'>",
                "<li><strong>🔄 Retry:</strong> Queue transaction for automatic retry processing</li>",
                "<li><strong>💰 Refund:</strong> Return funds to user's available wallet balance</li>",
                "<li><strong>❌ Decline:</strong> Permanently decline transaction (funds remain frozen)</li>",
                "</ul>",
                "</div>"
            ])
            
            content = "".join(content_sections)
            
            # Create email with high urgency
            subject = f"🚨 URGENT: Transaction Failure - {format_amount(amount, currency)} - ID: {cashout_id[:8]}"
            html_content = self._create_email_template(
                "TRANSACTION_FAILURE", subject, content, "critical"
            )
            
            # Send email
            success = email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if success:
                logger.info(f"Failure alert email sent for cashout {cashout_id} (${amount} {currency})")
            else:
                logger.error(f"Failed to send failure alert email for cashout {cashout_id}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error sending failure alert with actions: {e}")
            return False

    def _build_action_buttons_html(self, base_url: str, action_tokens: Dict[str, str]) -> str:
        """Build HTML for secure action buttons"""
        button_styles = {
            'retry': {
                'color': '#007bff',
                'bg_color': '#007bff',
                'hover_color': '#0056b3',
                'icon': '🔄',
                'text': 'Retry Transaction'
            },
            'refund': {
                'color': '#28a745',
                'bg_color': '#28a745', 
                'hover_color': '#1e7e34',
                'icon': '💰',
                'text': 'Refund to Wallet'
            },
            'decline': {
                'color': '#dc3545',
                'bg_color': '#dc3545',
                'hover_color': '#c82333',
                'icon': '❌',
                'text': 'Decline Permanently'
            }
        }
        
        buttons_html = []
        
        for action, token in action_tokens.items():
            if token and action in button_styles:
                style = button_styles[action]
                action_url = f"{base_url}/webhook/admin_action/{action}/{token}"
                
                button_html = f"""
                <div style="margin: 10px 0;">
                    <a href="{action_url}" 
                       style="
                           display: inline-block;
                           padding: 12px 24px;
                           background-color: {style['bg_color']};
                           color: white;
                           text-decoration: none;
                           border-radius: 6px;
                           font-weight: bold;
                           font-size: 16px;
                           text-align: center;
                           min-width: 200px;
                           margin: 5px;
                           box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                       "
                       onmouseover="this.style.backgroundColor='{style['hover_color']}'"
                       onmouseout="this.style.backgroundColor='{style['bg_color']}'">
                        {style['icon']} {style['text']}
                    </a>
                </div>
                """
                buttons_html.append(button_html)
        
        return "".join(buttons_html)

    async def send_bulk_failure_summary(self, session: Session, 
                                       min_failures: int = 5) -> bool:
        """Send summary email when multiple failures accumulate"""
        if not self.enabled:
            return True
            
        try:
            from services.admin_failure_service import admin_failure_service
            
            # Get current failures
            failures_data = admin_failure_service.get_pending_failures(
                session, limit=100, offset=0
            )
            
            failures = failures_data.get('failures', [])
            summary = failures_data.get('summary', {})
            
            total_failures = len(failures)
            
            # Only send if above threshold
            if total_failures < min_failures:
                return True
            
            total_amount = summary.get('total_amount', 0)
            high_priority = summary.get('high_priority_count', 0)
            currency_breakdown = summary.get('currency_breakdown', {})
            
            # Build summary content
            content_sections = [
                "<h2>📊 Multiple Transaction Failures Alert</h2>",
                f"<p><strong>You have {total_failures} transactions requiring admin intervention.</strong></p>",
                
                "<div style='background: #fff3cd; padding: 20px; border-radius: 8px; margin: 20px 0;'>",
                "<h3>📈 Summary Statistics</h3>",
                f"<p><strong>📋 Total Pending:</strong> {total_failures} transactions</p>",
                f"<p><strong>🔥 High Priority:</strong> {high_priority} transactions</p>",
                f"<p><strong>💰 Total Value:</strong> ${total_amount:,.2f}</p>",
                "</div>",
                
                "<div style='background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0;'>",
                "<h3>💱 Currency Breakdown</h3>",
            ]
            
            for currency, data in currency_breakdown.items():
                count = data['count']
                amount = data['total_amount']
                content_sections.append(
                    f"<p><strong>{currency}:</strong> {count} transactions (${amount:,.2f})</p>"
                )
            
            content_sections.extend([
                "</div>",
                
                "<div style='background: #d1ecf1; padding: 20px; border-radius: 8px; margin: 20px 0;'>",
                "<h3>🔧 Recommended Actions</h3>",
                "<p><strong>Use the Telegram bot to manage failures efficiently:</strong></p>",
                "<p>• Send <code>/admin_failures</code> to view and manage all pending failures</p>",
                "<p>• Review high priority transactions first</p>",
                "<p>• Check for patterns in error messages to identify system issues</p>",
                "</div>"
            ])
            
            content = "".join(content_sections)
            
            # Send summary email
            subject = f"📊 {total_failures} Failed Transactions Requiring Admin Review"
            html_content = self._create_email_template(
                "BULK_FAILURE_SUMMARY", subject, content, "high"
            )
            
            success = email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if success:
                logger.info(f"Bulk failure summary sent: {total_failures} pending failures")
            
            return success
            
        except Exception as e:
            logger.error(f"Error sending bulk failure summary: {e}")
            return False


# Initialize global instances
admin_email_service = AdminEmailAlertService()
admin_email_alerts = admin_email_service  # Alias for backward compatibility
