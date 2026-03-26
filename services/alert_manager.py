"""
Alert Manager
Centralized alert coordination and dispatch system
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List
from config import Config


class MonitoringConfig:
    """Configuration for alert management"""

    SERVICE_ALERT_COOLDOWN_MINUTES = int(
        getattr(Config, "SERVICE_ALERT_COOLDOWN_MINUTES", 15)
    )
    SYSTEM_ALERT_COOLDOWN_MINUTES = int(
        getattr(Config, "SYSTEM_ALERT_COOLDOWN_MINUTES", 30)
    )

    @classmethod
    def get_service_alert_cooldown(cls):
        """Get service alert cooldown period"""

        return timedelta(minutes=cls.SERVICE_ALERT_COOLDOWN_MINUTES)

    @classmethod
    def get_system_alert_cooldown(cls):
        """Get system alert cooldown period"""

        return timedelta(minutes=cls.SYSTEM_ALERT_COOLDOWN_MINUTES)


from services.consolidated_notification_service import (
    consolidated_notification_service as notification_hub,
)
from config import Config
from telegram import Bot

logger = logging.getLogger(__name__)


class AlertManager:
    """Centralized alert management and coordination"""

    def __init__(self):
        self.alert_history = {}
        self.last_alert_times = {}

    def _should_send_alert(self, alert_type: str, key: str = None) -> bool:
        """Check if alert should be sent based on cooldown"""
        alert_key = f"{alert_type}:{key}" if key else alert_type
        last_alert = self.last_alert_times.get(alert_key)

        if not last_alert:
            return True

        # Use different cooldowns for different alert types
        if alert_type.startswith("service_"):
            cooldown = MonitoringConfig.get_service_alert_cooldown()
        else:
            cooldown = MonitoringConfig.get_system_alert_cooldown()

        time_since_alert = datetime.utcnow() - last_alert
        return time_since_alert >= cooldown

    def _record_alert(self, alert_type: str, key: str = None):
        """Record that an alert was sent"""
        alert_key = f"{alert_type}:{key}" if key else alert_type
        self.last_alert_times[alert_key] = datetime.utcnow()

    async def send_system_alert(self, alert_data: Dict[str, Any]) -> bool:
        """Send system monitoring alert"""
        try:
            alert_type = f"system_{alert_data.get('check_type', 'unknown')}"

            if not self._should_send_alert(alert_type):
                logger.debug(f"Skipping {alert_type} alert - within cooldown period")
                return False

            # Format alert message
            message = self._format_system_alert(alert_data)

            # Send to admins and notification group
            success = await self._dispatch_alert(
                message, "system", alert_data.get("severity", "medium")
            )

            if success:
                self._record_alert(alert_type)
                logger.info(f"System alert sent: {alert_type}")

            return success

        except Exception as e:
            logger.error(f"Failed to send system alert: {e}")
            return False

    async def send_service_failure_alert(
        self, service_name: str, failures: List[str], severity: str = "high"
    ) -> bool:
        """Send service failure alert"""
        try:
            alert_type = f"service_{service_name}"

            if not self._should_send_alert(alert_type):
                logger.debug(f"Skipping {alert_type} alert - within cooldown period")
                return False

            # Format service failure message
            message = self._format_service_failure_alert(service_name, failures)

            # Send to admins and notification group
            success = await self._dispatch_alert(message, "service", severity)

            if success:
                self._record_alert(alert_type)
                logger.info(f"Service failure alert sent: {service_name}")

            return success

        except Exception as e:
            logger.error(f"Failed to send service failure alert: {e}")
            return False

    async def send_critical_transaction_alert(
        self, transaction_data: Dict[str, Any]
    ) -> bool:
        """Send high-value or suspicious transaction alert"""
        try:
            alert_type = "transaction_critical"
            transaction_id = transaction_data.get("id", "unknown")

            if not self._should_send_alert(alert_type, transaction_id):
                logger.debug("Skipping transaction alert - within cooldown period")
                return False

            # Format transaction alert
            message = self._format_transaction_alert(transaction_data)

            # Send to admins only (sensitive financial data)
            success = await self._send_admin_only_alert(message)

            if success:
                self._record_alert(alert_type, transaction_id)
                logger.info(f"Transaction alert sent: {transaction_id}")

            return success

        except Exception as e:
            logger.error(f"Failed to send transaction alert: {e}")
            return False

    def _format_system_alert(self, alert_data: Dict[str, Any]) -> str:
        """Format system monitoring alert message"""
        alert_data.get("check_type", "System")
        status = alert_data.get("status", "unknown")
        issues = alert_data.get("issues", [])

        severity_emoji = {
            "low": "🔵",
            "medium": "🟡",
            "high": "🔴",
            "critical": "🚨",
        }.get(alert_data.get("severity", "medium"), "⚠️")

        message = f"""{severity_emoji} SYSTEM ALERT: 

Status: {status.upper()}
Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}

**Issues Detected:**"""

        for issue in issues[:5]:  # Limit to 5 issues to avoid spam
            message += f"\n• {issue}"

        if len(issues) > 5:
            message += f"\n• ... and {len(issues) - 5} more issues"

        message += f"\n\nPlatform: {Config.PLATFORM_NAME}"

        return message

    def _format_service_failure_alert(
        self, service_name: str, failures: List[str]
    ) -> str:
        """Format service failure alert message"""
        message = f"""🚨 **SERVICE FAILURE ALERT**

**Service:** {service_name.upper()}
Status: FAILING
Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}

**Recent Failures:**"""

        for failure in failures[:5]:  # Limit to 5 most recent
            message += f"\n• {failure}"

        if len(failures) > 5:
            message += f"\n• ... and {len(failures) - 5} more failures"

        # Add specific recommendations
        recommendations = {
            "blockbee": "• Check BlockBee API status and callback URLs\n• Verify API key configuration\n• Review address generation logs",
            "fincra": "• Check Fincra account balance and limits\n• Verify API credentials\n• Review payment processing logs",
            "binance": "• Check Binance API permissions\n• Verify cashout settings\n• Review balance levels",
        }

        if service_name.lower() in recommendations:
            message += (
                f"\n\n**Immediate Actions:**\n{recommendations[service_name.lower()]}"
            )

        message += f"\n\nPlatform: {Config.PLATFORM_NAME}"
        message += "\n**Action Required:** Immediate investigation needed"

        return message

    def _format_transaction_alert(self, transaction_data: Dict[str, Any]) -> str:
        """Format high-value transaction alert"""
        message = f"""🔒 **HIGH-VALUE TRANSACTION ALERT**

**Type:** {transaction_data.get('type', 'Unknown')}
**Amount:** ${transaction_data.get('amount', 0):,.2f} USD
Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}

**Details:**
• Transaction ID: {transaction_data.get('id', 'N/A')}
• User ID: {transaction_data.get('user_id', 'N/A')}
• Status: {transaction_data.get('status', 'Unknown')}"""

        if transaction_data.get("suspicious"):
            message += "\n\n⚠️ **SUSPICIOUS ACTIVITY DETECTED**"
            for flag in transaction_data.get("suspicious_flags", []):
                message += f"\n• {flag}"

        message += f"\n\nPlatform: {Config.PLATFORM_NAME}"
        message += "\n**Review Required:** Manual verification recommended"

        return message

    async def _dispatch_alert(
        self, message: str, alert_category: str, severity: str
    ) -> bool:
        """Dispatch alert to appropriate channels"""
        success_count = 0

        try:
            # Always send to admins
            admin_success = await self._send_admin_alert(message)
            if admin_success > 0:
                success_count += 1

            # Send to notification group for non-sensitive alerts
            if alert_category in ["system", "service"] and Config.NOTIFICATION_GROUP_ID:
                group_success = await self._send_group_alert(message)
                if group_success:
                    success_count += 1

            return success_count > 0

        except Exception as e:
            logger.error(f"Error dispatching alert: {e}")
            return False

    async def _send_admin_alert(self, message: str) -> int:
        """Send alert to all admins"""
        try:
            return await notification_hub.notify_all_admins(
                message, parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send admin alert: {e}")
            return 0

    async def _send_admin_only_alert(self, message: str) -> bool:
        """Send sensitive alert to admins only"""
        try:
            success_count = await self._send_admin_alert(message)
            return success_count > 0
        except Exception as e:
            logger.error(f"Failed to send admin-only alert: {e}")
            return False

    async def _send_group_alert(self, message: str) -> bool:
        """Send alert to notification group"""
        try:
            if not Config.NOTIFICATION_GROUP_ID:
                return False

            bot = Bot(Config.BOT_TOKEN)
            return await notification_hub.send_telegram_group_message(
                bot, Config.NOTIFICATION_GROUP_ID, message, parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to send group alert: {e}")
            return False

    async def process_monitoring_results(
        self, system_results: Dict[str, Any], service_results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Process monitoring results and send appropriate alerts"""
        alerts_sent = {"system_alerts": 0, "service_alerts": 0, "transaction_alerts": 0}

        try:
            # Process system monitoring results
            if system_results.get("overall_status") in ["critical", "warning"]:
                for check_name, check_result in system_results.get(
                    "checks", {}
                ).items():
                    if check_result.get("status") in [
                        "unhealthy",
                        "critical",
                        "degraded",
                        "at_risk",
                        "suspicious",
                    ]:
                        alert_data = {
                            "check_type": check_name,
                            "status": check_result["status"],
                            "issues": check_result.get("issues", [])
                            + check_result.get("alerts", [])
                            + check_result.get("warnings", []),
                            "severity": (
                                "high"
                                if check_result["status"] in ["critical", "at_risk"]
                                else "medium"
                            ),
                        }

                        success = await self.send_system_alert(alert_data)
                        if success:
                            alerts_sent["system_alerts"] += 1

            # Process service monitoring results
            if service_results.get("overall_status") in ["critical", "degraded"]:
                for service_name, service_result in service_results.get(
                    "services", {}
                ).items():
                    if service_result.get("status") in [
                        "unhealthy",
                        "error",
                        "degraded",
                    ]:
                        severity = (
                            "high"
                            if service_result["status"] in ["unhealthy", "error"]
                            else "medium"
                        )

                        success = await self.send_service_failure_alert(
                            service_name, service_result.get("errors", []), severity
                        )
                        if success:
                            alerts_sent["service_alerts"] += 1

            # Process high-value transaction alerts
            for check_name, check_result in system_results.get("checks", {}).items():
                if (
                    check_name == "transactions"
                    and check_result.get("status") == "high_activity"
                ):
                    # Send summary alert for high transaction activity
                    transaction_alert = {
                        "type": "High Activity Summary",
                        "amount": 0,  # Summary alert
                        "id": f"summary_{datetime.utcnow().strftime('%Y%m%d')}",
                        "user_id": "multiple",
                        "status": "multiple_high_value",
                        "escrows_count": len(
                            check_result.get("high_value_escrows", [])
                        ),
                        "cashouts_count": len(
                            check_result.get("high_value_cashouts", [])
                        ),
                    }

                    success = await self.send_critical_transaction_alert(
                        transaction_alert
                    )
                    if success:
                        alerts_sent["transaction_alerts"] += 1

            logger.info(
                f"Alert processing completed - System: {alerts_sent['system_alerts']}, Service: {alerts_sent['service_alerts']}, Transaction: {alerts_sent['transaction_alerts']}"
            )

        except Exception as e:
            logger.error(f"Error processing monitoring results: {e}")

        return alerts_sent


# Global instance
alert_manager = AlertManager()
