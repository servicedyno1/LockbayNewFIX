"""
Unified Admin Alert System
Integrates all alerts with email, priorities, and consolidation
"""

import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
import asyncio
from collections import defaultdict
import json

logger = logging.getLogger(__name__)


class AlertPriority(Enum):
    """Alert priority levels for email frequency management"""
    CRITICAL = "critical"    # Immediate email + escalation
    HIGH = "high"           # Email within 5 minutes
    MEDIUM = "medium"       # Email within 30 minutes (consolidated)
    LOW = "low"            # Daily digest only


class AlertCategory(Enum):
    """Alert categories for consolidation"""
    SECURITY = "security"
    FINANCIAL = "financial"
    SYSTEM = "system"
    FRAUD = "fraud"
    TIMEOUT = "timeout"
    API_FAILURE = "api_failure"


@dataclass
class UnifiedAlert:
    """Unified alert structure"""
    id: str
    category: AlertCategory
    priority: AlertPriority
    title: str
    message: str
    details: Dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    user_id: Optional[int] = None
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    
    def to_email_format(self) -> str:
        """Convert alert to email format"""
        priority_icons = {
            AlertPriority.CRITICAL: "🚨",
            AlertPriority.HIGH: "⚠️",
            AlertPriority.MEDIUM: "🔔",
            AlertPriority.LOW: "ℹ️"
        }
        
        category_icons = {
            AlertCategory.SECURITY: "🛡️",
            AlertCategory.FINANCIAL: "💰",
            AlertCategory.SYSTEM: "⚙️",
            AlertCategory.FRAUD: "🕵️",
            AlertCategory.TIMEOUT: "⏰",
            AlertCategory.API_FAILURE: "🔌"
        }
        
        icon = priority_icons.get(self.priority, "🔔")
        cat_icon = category_icons.get(self.category, "📋")
        
        email_content = f"""
{icon} {cat_icon} {self.title}

Priority: {self.priority.value.upper()}
Category: {self.category.value.upper()}
Time: {self.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}

{self.message}
"""
        
        if self.user_id:
            email_content += f"\nUser ID: {self.user_id}"
        
        if self.entity_type and self.entity_id:
            email_content += f"\nEntity: {self.entity_type} {self.entity_id}"
        
        if self.details:
            email_content += f"\nDetails: {json.dumps(self.details, indent=2)}"
        
        return email_content


class AlertConsolidator:
    """Prevents email flooding by consolidating similar alerts"""
    
    def __init__(self):
        self.recent_alerts: Dict[str, List[UnifiedAlert]] = defaultdict(list)
        self.consolidation_windows = {
            AlertPriority.CRITICAL: timedelta(minutes=1),   # Very short consolidation
            AlertPriority.HIGH: timedelta(minutes=5),       # Short consolidation  
            AlertPriority.MEDIUM: timedelta(minutes=30),    # Medium consolidation
            AlertPriority.LOW: timedelta(hours=24)          # Daily consolidation
        }
    
    def should_send_alert(self, alert: UnifiedAlert) -> bool:
        """Check if alert should be sent or consolidated"""
        consolidation_key = f"{alert.category.value}_{alert.priority.value}"
        window = self.consolidation_windows[alert.priority]
        cutoff_time = datetime.utcnow() - window
        
        # Get recent similar alerts
        recent_similar = [
            a for a in self.recent_alerts[consolidation_key]
            if a.timestamp > cutoff_time
        ]
        
        # Critical alerts always go through (with minimal consolidation)
        if alert.priority == AlertPriority.CRITICAL:
            if len(recent_similar) < 3:  # Max 3 critical alerts per minute
                self.recent_alerts[consolidation_key].append(alert)
                return True
            return False
        
        # High priority: max 2 per 5 minutes
        if alert.priority == AlertPriority.HIGH:
            if len(recent_similar) < 2:
                self.recent_alerts[consolidation_key].append(alert)
                return True
            return False
        
        # Medium/Low: max 1 per window
        if len(recent_similar) == 0:
            self.recent_alerts[consolidation_key].append(alert)
            return True
        
        return False
    
    def cleanup_old_alerts(self):
        """Clean up old alerts to prevent memory leaks"""
        cutoff = datetime.utcnow() - timedelta(hours=48)
        for key in self.recent_alerts:
            self.recent_alerts[key] = [
                a for a in self.recent_alerts[key] 
                if a.timestamp > cutoff
            ]


class UnifiedAdminAlertSystem:
    """Unified admin alert system with email, priorities, and consolidation"""
    
    def __init__(self):
        self.consolidator = AlertConsolidator()
        self.email_enabled = True
        self.alert_queue: List[UnifiedAlert] = []
        
        # Start background tasks
        asyncio.create_task(self._process_alert_queue())
        asyncio.create_task(self._cleanup_task())
    
    async def send_alert(
        self,
        category: AlertCategory,
        priority: AlertPriority,
        title: str,
        message: str,
        details: Optional[Dict] = None,
        user_id: Optional[int] = None,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None
    ) -> bool:
        """Send unified admin alert with consolidation and priority management"""
        
        alert = UnifiedAlert(
            id=f"{category.value}_{priority.value}_{datetime.utcnow().timestamp()}",
            category=category,
            priority=priority,
            title=title,
            message=message,
            details=details or {},
            user_id=user_id,
            entity_type=entity_type,
            entity_id=entity_id
        )
        
        # Check consolidation
        if not self.consolidator.should_send_alert(alert):
            logger.debug(f"Alert consolidated: {title}")
            return True
        
        # Add to processing queue
        self.alert_queue.append(alert)
        logger.info(f"Alert queued: {priority.value} - {title}")
        return True
    
    async def _process_alert_queue(self):
        """Background task to process alert queue"""
        while True:
            try:
                if self.alert_queue:
                    alerts_to_process = self.alert_queue.copy()
                    self.alert_queue.clear()
                    
                    for alert in alerts_to_process:
                        await self._send_alert_email(alert)
                
                # Process queue every 10 seconds
                await asyncio.sleep(10)
                
            except Exception as e:
                logger.error(f"Error processing alert queue: {e}")
                await asyncio.sleep(30)
    
    async def _send_alert_email(self, alert: UnifiedAlert):
        """Send alert email using consolidated notification service"""
        try:
            from services.consolidated_notification_service import consolidated_notification_service
            
            # Format email subject based on priority
            priority_prefixes = {
                AlertPriority.CRITICAL: "🚨 CRITICAL ALERT",
                AlertPriority.HIGH: "⚠️ HIGH PRIORITY",
                AlertPriority.MEDIUM: "🔔 SYSTEM ALERT",
                AlertPriority.LOW: "ℹ️ INFO"
            }
            
            subject_prefix = priority_prefixes.get(alert.priority, "🔔 ALERT")
            email_message = f"{subject_prefix}: {alert.title}\n\n{alert.to_email_format()}"
            
            # Add timestamp for email tracking
            timestamp = alert.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')
            
            success = await consolidated_notification_service.send_admin_alert(
                email_message,
                timestamp=timestamp,
                priority=alert.priority.value,
                category=alert.category.value
            )
            
            if success:
                logger.info(f"✅ Admin email sent: {alert.priority.value} - {alert.title}")
            else:
                logger.error(f"❌ Failed to send admin email: {alert.title}")
                
        except Exception as e:
            logger.error(f"Error sending alert email: {e}")
    
    async def _cleanup_task(self):
        """Background cleanup task"""
        while True:
            try:
                self.consolidator.cleanup_old_alerts()
                await asyncio.sleep(3600)  # Cleanup every hour
            except Exception as e:
                logger.error(f"Error in cleanup task: {e}")
                await asyncio.sleep(1800)


# Global instance
unified_admin_alerts = UnifiedAdminAlertSystem()


# Convenience functions for easy integration
async def send_security_alert(title: str, message: str, priority: AlertPriority = AlertPriority.HIGH, **kwargs):
    """Send security alert"""
    return await unified_admin_alerts.send_alert(
        AlertCategory.SECURITY, priority, title, message, **kwargs
    )

async def send_financial_alert(title: str, message: str, priority: AlertPriority = AlertPriority.HIGH, **kwargs):
    """Send financial alert"""
    return await unified_admin_alerts.send_alert(
        AlertCategory.FINANCIAL, priority, title, message, **kwargs
    )

async def send_fraud_alert(title: str, message: str, priority: AlertPriority = AlertPriority.CRITICAL, **kwargs):
    """Send fraud alert"""
    return await unified_admin_alerts.send_alert(
        AlertCategory.FRAUD, priority, title, message, **kwargs
    )

async def send_timeout_alert(title: str, message: str, priority: AlertPriority = AlertPriority.HIGH, **kwargs):
    """Send timeout alert"""
    return await unified_admin_alerts.send_alert(
        AlertCategory.TIMEOUT, priority, title, message, **kwargs
    )

async def send_system_alert(title: str, message: str, priority: AlertPriority = AlertPriority.MEDIUM, **kwargs):
    """Send system alert"""
    return await unified_admin_alerts.send_alert(
        AlertCategory.SYSTEM, priority, title, message, **kwargs
    )

async def send_api_failure_alert(title: str, message: str, priority: AlertPriority = AlertPriority.MEDIUM, **kwargs):
    """Send API failure alert"""
    return await unified_admin_alerts.send_alert(
        AlertCategory.API_FAILURE, priority, title, message, **kwargs
    )