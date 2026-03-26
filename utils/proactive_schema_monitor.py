"""
Proactive Schema Monitoring System
Monitors for schema-related issues and provides early warnings
"""

import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List
from dataclasses import dataclass
from utils.schema_validator import schema_validator, SchemaValidationResult
from utils.enhanced_audit_logger import enhanced_audit_logger

logger = logging.getLogger(__name__)

@dataclass
class SchemaAlert:
    """Schema monitoring alert"""
    alert_type: str
    severity: str
    table_name: str
    column_name: str
    description: str
    timestamp: datetime
    auto_fixable: bool

class ProactiveSchemaMonitor:
    """Monitors database schema proactively to prevent failures"""
    
    def __init__(self):
        self.last_validation_time = None
        self.validation_interval = timedelta(hours=6)  # Check every 6 hours
        self.active_alerts: List[SchemaAlert] = []
        self.known_issues: Dict[str, datetime] = {}
        
    async def start_monitoring(self):
        """Start the proactive schema monitoring"""
        logger.info("🔍 Starting proactive schema monitoring...")
        
        # Run initial validation
        await self.run_periodic_validation()
        
        # Schedule periodic checks
        asyncio.create_task(self._monitoring_loop())
        
    async def _monitoring_loop(self):
        """Background monitoring loop"""
        while True:
            try:
                await asyncio.sleep(3600)  # Check every hour
                
                # Run validation if interval has passed
                if (self.last_validation_time is None or 
                    datetime.now() - self.last_validation_time >= self.validation_interval):
                    await self.run_periodic_validation()
                    
            except Exception as e:
                logger.error(f"Error in schema monitoring loop: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes before retrying
    
    async def run_periodic_validation(self):
        """Run periodic schema validation"""
        logger.info("🔍 Running periodic schema validation...")
        
        try:
            result = schema_validator.validate_full_schema()
            self.last_validation_time = datetime.now()
            
            # Process results and generate alerts
            await self._process_validation_result(result)
            
            # Log summary
            if result.is_valid:
                logger.info("✅ Periodic schema validation passed")
            else:
                logger.warning(f"⚠️ Periodic schema validation found {len(result.critical_issues)} critical issues")
                
        except Exception as e:
            logger.error(f"Periodic schema validation failed: {e}")
            await self._create_alert(
                alert_type="validation_failure",
                severity="high",
                table_name="system",
                column_name="validation",
                description=f"Schema validation failed: {e}",
                auto_fixable=False
            )
    
    async def _process_validation_result(self, result: SchemaValidationResult):
        """Process validation result and create alerts"""
        # Clear old alerts for resolved issues
        self._clear_resolved_alerts(result)
        
        # Create alerts for critical issues
        for issue in result.critical_issues:
            await self._create_alert(
                alert_type=issue.issue_type,
                severity="critical",
                table_name=issue.table_name,
                column_name=issue.column_name,
                description=f"Critical schema issue: {issue.issue_type} - {issue.model_type} vs {issue.db_type}",
                auto_fixable=issue.issue_type == "missing" and issue.table_name in ['transactions', 'wallets', 'escrows']
            )
        
        # Create alerts for warnings
        for warning in result.warnings:
            if warning.severity == "warning":
                await self._create_alert(
                    alert_type=warning.issue_type,
                    severity="warning",
                    table_name=warning.table_name,
                    column_name=warning.column_name,
                    description=f"Schema warning: {warning.issue_type} - {warning.model_type} vs {warning.db_type}",
                    auto_fixable=False
                )
    
    async def _create_alert(self, alert_type: str, severity: str, table_name: str, 
                          column_name: str, description: str, auto_fixable: bool):
        """Create a schema monitoring alert"""
        alert_key = f"{table_name}.{column_name}.{alert_type}"
        
        # Don't create duplicate alerts
        if alert_key in self.known_issues:
            # Update timestamp for existing issue
            self.known_issues[alert_key] = datetime.now()
            return
        
        alert = SchemaAlert(
            alert_type=alert_type,
            severity=severity,
            table_name=table_name,
            column_name=column_name,
            description=description,
            timestamp=datetime.now(),
            auto_fixable=auto_fixable
        )
        
        self.active_alerts.append(alert)
        self.known_issues[alert_key] = alert.timestamp
        
        # Log alert based on severity
        if severity == "critical":
            logger.critical(f"🚨 CRITICAL SCHEMA ALERT: {description}")
        elif severity == "high":
            logger.error(f"❌ HIGH SCHEMA ALERT: {description}")
        elif severity == "warning":
            logger.warning(f"⚠️ SCHEMA WARNING: {description}")
        
        # Send to audit system
        try:
            await enhanced_audit_logger.log_security_event(
                event_type="schema_monitoring_alert",
                description=description,
                severity=severity.upper(),
                metadata={
                    "alert_type": alert_type,
                    "table_name": table_name,
                    "column_name": column_name,
                    "auto_fixable": auto_fixable
                }
            )
        except Exception as e:
            logger.error(f"Failed to log schema alert to audit system: {e}")
    
    def _clear_resolved_alerts(self, result: SchemaValidationResult):
        """Clear alerts that have been resolved"""
        if result.is_valid:
            # Clear all alerts if validation passes
            resolved_count = len(self.active_alerts)
            self.active_alerts.clear()
            self.known_issues.clear()
            
            if resolved_count > 0:
                logger.info(f"✅ Cleared {resolved_count} resolved schema alerts")
        else:
            # Clear specific resolved issues
            current_issues = set()
            
            for issue in result.critical_issues + result.warnings:
                alert_key = f"{issue.table_name}.{issue.column_name}.{issue.issue_type}"
                current_issues.add(alert_key)
            
            # Remove resolved issues
            resolved_keys = set(self.known_issues.keys()) - current_issues
            for key in resolved_keys:
                del self.known_issues[key]
                
            # Remove from active alerts
            self.active_alerts = [
                alert for alert in self.active_alerts
                if f"{alert.table_name}.{alert.column_name}.{alert.alert_type}" in current_issues
            ]
    
    async def get_active_alerts(self) -> List[SchemaAlert]:
        """Get list of active schema alerts"""
        return self.active_alerts.copy()
    
    async def get_health_status(self) -> Dict[str, any]:
        """Get schema monitoring health status"""
        return {
            "monitoring_active": True,
            "last_validation": self.last_validation_time.isoformat() if self.last_validation_time else None,
            "active_alerts": len(self.active_alerts),
            "critical_alerts": len([a for a in self.active_alerts if a.severity == "critical"]),
            "next_validation": (self.last_validation_time + self.validation_interval).isoformat() 
                             if self.last_validation_time else None
        }

# Global monitor instance
proactive_schema_monitor = ProactiveSchemaMonitor()

async def start_proactive_schema_monitoring():
    """Start proactive schema monitoring"""
    await proactive_schema_monitor.start_monitoring()