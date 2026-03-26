"""
Schema Alert System
Provides comprehensive alerting for database schema issues
"""

import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Any
from dataclasses import dataclass
from enum import Enum
from utils.proactive_schema_monitor import proactive_schema_monitor

logger = logging.getLogger(__name__)

class AlertChannel(Enum):
    """Alert delivery channels"""
    LOG = "log"
    AUDIT = "audit"
    WEBHOOK = "webhook"
    EMAIL = "email"

class AlertSeverity(Enum):
    """Alert severity levels"""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"

@dataclass
class SchemaAlertRule:
    """Schema alert rule configuration"""
    name: str
    condition: str
    severity: AlertSeverity
    channels: List[AlertChannel]
    cooldown_minutes: int
    auto_escalate: bool

class SchemaAlertSystem:
    """Comprehensive schema alerting system"""
    
    def __init__(self):
        self.alert_rules = self._initialize_alert_rules()
        self.alert_history: Dict[str, datetime] = {}
        self.escalation_tracker: Dict[str, int] = {}
        
    def _initialize_alert_rules(self) -> List[SchemaAlertRule]:
        """Initialize default alert rules"""
        return [
            SchemaAlertRule(
                name="critical_column_missing",
                condition="critical_issues > 0 and issue_type == 'missing'",
                severity=AlertSeverity.CRITICAL,
                channels=[AlertChannel.LOG, AlertChannel.AUDIT],
                cooldown_minutes=30,
                auto_escalate=True
            ),
            SchemaAlertRule(
                name="table_missing",
                condition="critical_issues > 0 and issue_type == 'missing_table'",
                severity=AlertSeverity.EMERGENCY,
                channels=[AlertChannel.LOG, AlertChannel.AUDIT],
                cooldown_minutes=15,
                auto_escalate=True
            ),
            SchemaAlertRule(
                name="type_mismatch",
                condition="warnings > 0 and issue_type == 'type_mismatch'",
                severity=AlertSeverity.WARNING,
                channels=[AlertChannel.LOG],
                cooldown_minutes=60,
                auto_escalate=False
            ),
            SchemaAlertRule(
                name="validation_failure",
                condition="validation_failed == True",
                severity=AlertSeverity.EMERGENCY,
                channels=[AlertChannel.LOG, AlertChannel.AUDIT],
                cooldown_minutes=10,
                auto_escalate=True
            ),
            SchemaAlertRule(
                name="multiple_critical_issues",
                condition="critical_issues >= 3",
                severity=AlertSeverity.EMERGENCY,
                channels=[AlertChannel.LOG, AlertChannel.AUDIT],
                cooldown_minutes=20,
                auto_escalate=True
            )
        ]
    
    async def process_schema_status(self):
        """Process current schema status and trigger alerts if needed"""
        try:
            # Get current alerts from monitor
            active_alerts = await proactive_schema_monitor.get_active_alerts()
            health_status = await proactive_schema_monitor.get_health_status()
            
            # Create alert context
            alert_context = {
                "critical_issues": len([a for a in active_alerts if a.severity == "critical"]),
                "warnings": len([a for a in active_alerts if a.severity == "warning"]),
                "total_alerts": len(active_alerts),
                "validation_failed": not health_status.get("monitoring_active", True),
                "active_alerts": active_alerts
            }
            
            # Check each alert rule
            for rule in self.alert_rules:
                if await self._should_trigger_alert(rule, alert_context):
                    await self._trigger_alert(rule, alert_context)
                    
        except Exception as e:
            logger.error(f"Error processing schema alerts: {e}")
    
    async def _should_trigger_alert(self, rule: SchemaAlertRule, context: Dict[str, Any]) -> bool:
        """Check if an alert rule should trigger"""
        try:
            # Check cooldown
            last_alert = self.alert_history.get(rule.name)
            if last_alert:
                cooldown_period = timedelta(minutes=rule.cooldown_minutes)
                if datetime.now() - last_alert < cooldown_period:
                    return False
            
            # Evaluate condition
            return self._evaluate_condition(rule.condition, context)
            
        except Exception as e:
            logger.error(f"Error evaluating alert rule {rule.name}: {e}")
            return False
    
    def _evaluate_condition(self, condition: str, context: Dict[str, Any]) -> bool:
        """Safely evaluate alert condition"""
        try:
            # Extract values from context for evaluation
            critical_issues = context.get("critical_issues", 0)
            warnings = context.get("warnings", 0)
            total_alerts = context.get("total_alerts", 0)
            validation_failed = context.get("validation_failed", False)
            
            # Create safe evaluation namespace
            safe_vars = {
                "critical_issues": critical_issues,
                "warnings": warnings,
                "total_alerts": total_alerts,
                "validation_failed": validation_failed
            }
            
            # Check for specific issue types in active alerts
            active_alerts = context.get("active_alerts", [])
            has_missing = any(alert.alert_type == "missing" for alert in active_alerts)
            has_missing_table = any(alert.alert_type == "missing_table" for alert in active_alerts)
            has_type_mismatch = any(alert.alert_type == "type_mismatch" for alert in active_alerts)
            
            safe_vars.update({
                "issue_type": "missing" if has_missing else ("missing_table" if has_missing_table else ("type_mismatch" if has_type_mismatch else "other"))
            })
            
            # Simple condition evaluation
            if "issue_type == 'missing'" in condition:
                return has_missing and critical_issues > 0
            elif "issue_type == 'missing_table'" in condition:
                return has_missing_table and critical_issues > 0
            elif "issue_type == 'type_mismatch'" in condition:
                return has_type_mismatch and warnings > 0
            elif "validation_failed == True" in condition:
                return validation_failed
            elif "critical_issues >= 3" in condition:
                return critical_issues >= 3
            elif "critical_issues > 0" in condition:
                return critical_issues > 0
            else:
                # Security fix: Removed unsafe eval() fallback
                # If condition is not recognized, log and return False
                logger.warning(f"Unrecognized alert condition: '{condition}' - condition not evaluated")
                return False
                
        except Exception as e:
            logger.error(f"Error evaluating condition '{condition}': {e}")
            return False
    
    async def _trigger_alert(self, rule: SchemaAlertRule, context: Dict[str, Any]):
        """Trigger an alert according to the rule"""
        try:
            # Record alert
            self.alert_history[rule.name] = datetime.now()
            
            # Create alert message
            alert_message = self._create_alert_message(rule, context)
            
            # Send to each configured channel
            for channel in rule.channels:
                await self._send_alert(channel, rule.severity, alert_message, context)
            
            # Handle escalation
            if rule.auto_escalate:
                await self._handle_escalation(rule, context)
                
        except Exception as e:
            logger.error(f"Error triggering alert {rule.name}: {e}")
    
    def _create_alert_message(self, rule: SchemaAlertRule, context: Dict[str, Any]) -> str:
        """Create formatted alert message"""
        critical_count = context.get("critical_issues", 0)
        warning_count = context.get("warnings", 0)
        active_alerts = context.get("active_alerts", [])
        
        severity_emoji = {
            AlertSeverity.INFO: "ℹ️",
            AlertSeverity.WARNING: "⚠️",
            AlertSeverity.CRITICAL: "🚨",
            AlertSeverity.EMERGENCY: "🚨🚨🚨"
        }
        
        emoji = severity_emoji.get(rule.severity, "⚠️")
        
        message = f"{emoji} SCHEMA ALERT: {rule.name}\n"
        message += f"Severity: {rule.severity.value.upper()}\n"
        message += f"Critical Issues: {critical_count}\n"
        message += f"Warnings: {warning_count}\n"
        
        if active_alerts:
            message += "\nActive Issues:\n"
            for alert in active_alerts[:3]:  # Show first 3
                message += f"- {alert.table_name}.{alert.column_name}: {alert.alert_type}\n"
            
            if len(active_alerts) > 3:
                message += f"... and {len(active_alerts) - 3} more\n"
        
        message += f"\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        return message
    
    async def _send_alert(self, channel: AlertChannel, severity: AlertSeverity, message: str, context: Dict[str, Any]):
        """Send alert to specific channel"""
        try:
            if channel == AlertChannel.LOG:
                if severity == AlertSeverity.EMERGENCY:
                    logger.critical(message)
                elif severity == AlertSeverity.CRITICAL:
                    logger.error(message)
                elif severity == AlertSeverity.WARNING:
                    logger.warning(message)
                else:
                    logger.info(message)
            
            elif channel == AlertChannel.AUDIT:
                try:
                    from utils.enhanced_audit_logger import enhanced_audit_logger
                    await enhanced_audit_logger.log_security_event(
                        event_type="schema_alert",
                        description=message,
                        severity=severity.value.upper(),
                        metadata={
                            "alert_channel": channel.value,
                            "critical_issues": context.get("critical_issues", 0),
                            "warnings": context.get("warnings", 0)
                        }
                    )
                except Exception as e:
                    logger.error(f"Failed to send alert to audit system: {e}")
            
            # Additional channels (webhook, email) can be implemented here
            
        except Exception as e:
            logger.error(f"Error sending alert to {channel.value}: {e}")
    
    async def _handle_escalation(self, rule: SchemaAlertRule, context: Dict[str, Any]):
        """Handle alert escalation"""
        try:
            escalation_count = self.escalation_tracker.get(rule.name, 0) + 1
            self.escalation_tracker[rule.name] = escalation_count
            
            if escalation_count >= 3:
                # After 3 escalations, create emergency alert
                emergency_message = "🚨🚨🚨 ESCALATED SCHEMA EMERGENCY\n"
                emergency_message += f"Alert '{rule.name}' has escalated {escalation_count} times\n"
                emergency_message += "This indicates a persistent schema issue requiring immediate attention\n"
                emergency_message += f"Critical Issues: {context.get('critical_issues', 0)}\n"
                emergency_message += f"Original Severity: {rule.severity.value}"
                
                logger.critical(emergency_message)
                
                # Reset escalation counter
                self.escalation_tracker[rule.name] = 0
                
        except Exception as e:
            logger.error(f"Error handling escalation for {rule.name}: {e}")

# Global alert system instance
schema_alert_system = SchemaAlertSystem()

async def start_schema_alerting():
    """Start the schema alerting system"""
    logger.info("🚨 Starting schema alert system...")
    
    # Start monitoring loop
    asyncio.create_task(_alert_monitoring_loop())
    
async def _alert_monitoring_loop():
    """Background loop for schema alerting"""
    while True:
        try:
            await schema_alert_system.process_schema_status()
            await asyncio.sleep(300)  # Check every 5 minutes
        except Exception as e:
            logger.error(f"Error in schema alert monitoring loop: {e}")
            await asyncio.sleep(60)  # Wait 1 minute before retrying