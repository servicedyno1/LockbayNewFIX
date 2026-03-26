"""Enhanced Audit Logger for security events and schema monitoring"""

import logging
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class EnhancedAuditLogger:
    """Enhanced audit logging for security and schema events"""
    
    def __init__(self):
        self.initialized = True
        
    async def log_security_event(
        self,
        event_type: str,
        severity: str = "info",
        details: Optional[Dict[str, Any]] = None,
        user_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        description: Optional[str] = None
    ) -> bool:
        """Log security events with enhanced details"""
        try:
            datetime.utcnow().isoformat()
            
            # Create comprehensive log entry
            
            # Log to standard logger with appropriate level
            log_msg = description or str(details)
            if severity == "critical":
                logger.critical(f"🚨 SECURITY EVENT: {event_type} - {log_msg}")
            elif severity == "warning":
                logger.warning(f"⚠️ SECURITY EVENT: {event_type} - {log_msg}")
            else:
                logger.info(f"📋 SECURITY EVENT: {event_type} - {log_msg}")
                
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to log security event: {e}")
            return False
    
    async def log_schema_alert(
        self,
        alert_type: str,
        table_name: str,
        change_details: Dict[str, Any],
        severity: str = "warning"
    ) -> bool:
        """Log schema-related alerts"""
        return await self.log_security_event(
            event_type="schema_alert",
            severity=severity,
            details={
                'alert_type': alert_type,
                'table_name': table_name,
                'change_details': change_details
            }
        )


# Global instance
enhanced_audit_logger = EnhancedAuditLogger()

# Alias for backward compatibility 
audit_logger = enhanced_audit_logger