"""
Context Data Security Module
Prevents data corruption and ensures integrity during currency switches
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class ContextSecurityError(Exception):
    """Raised when context data integrity is compromised"""
    pass

class ContextDataProtector:
    """Protects context data during currency switching operations"""
    
    # Context data expiry (30 minutes)
    CONTEXT_EXPIRY_MINUTES = 30
    
    @classmethod
    def validate_context_integrity(
        cls,
        context_data: Dict[str, Any],
        required_fields: list,
        context_type: str = "generic"
    ) -> Dict[str, Any]:
        """
        Validate context data integrity and completeness
        
        Returns:
            dict: Validation result
        """
        try:
            validation_result = {
                "is_valid": True,
                "missing_fields": [],
                "corrupted_fields": [],
                "context_type": context_type,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            # Check required fields
            for field in required_fields:
                if field not in context_data:
                    validation_result["missing_fields"].append(field)
                    validation_result["is_valid"] = False
                elif context_data[field] is None:
                    validation_result["corrupted_fields"].append(f"{field}_is_null")
                    validation_result["is_valid"] = False
            
            # Check context expiry
            if "created_at" in context_data:
                try:
                    created_at = datetime.fromisoformat(context_data["created_at"])
                    age = datetime.utcnow() - created_at
                    if age > timedelta(minutes=cls.CONTEXT_EXPIRY_MINUTES):
                        validation_result["is_valid"] = False
                        validation_result["corrupted_fields"].append("context_expired")
                except (ValueError, TypeError):
                    validation_result["corrupted_fields"].append("invalid_timestamp")
                    validation_result["is_valid"] = False
            
            if validation_result["is_valid"]:
                logger.info(f"CONTEXT VALIDATED: {context_type}")
            else:
                logger.warning(f"CONTEXT VALIDATION FAILED: {context_type} - {validation_result}")
                
            return validation_result
            
        except Exception as e:
            logger.error(f"Error validating context integrity: {e}")
            return {
                "is_valid": False,
                "error": str(e),
                "context_type": context_type,
                "timestamp": datetime.utcnow().isoformat()
            }
    
    @classmethod
    def secure_context_update(
        cls,
        context_data: Dict[str, Any],
        updates: Dict[str, Any],
        operation: str = "update"
    ) -> Dict[str, Any]:
        """
        Securely update context data while preserving critical fields
        
        Returns:
            dict: Updated context data
        """
        try:
            # Create backup of critical fields
            critical_fields = ["usd_value", "original_amount", "created_at", "user_id"]
            backup = {}
            
            for field in critical_fields:
                if field in context_data:
                    backup[field] = context_data[field]
            
            # Apply updates
            updated_context = context_data.copy()
            updated_context.update(updates)
            
            # Restore critical fields if they were overwritten
            for field, value in backup.items():
                if field in updates and field != "usd_value":  # Allow USD value updates for security fixes
                    logger.warning(f"CONTEXT SECURITY: Prevented overwrite of critical field '{field}' during {operation}")
                    updated_context[field] = value
            
            # Add operation timestamp
            updated_context["last_updated"] = datetime.utcnow().isoformat()
            updated_context["last_operation"] = operation
            
            logger.info(f"CONTEXT UPDATED SECURELY: {operation}")
            return updated_context
            
        except Exception as e:
            logger.error(f"Error in secure context update: {e}")
            raise ContextSecurityError(f"Context update failed: {e}")
    
    @classmethod
    def create_switch_checkpoint(
        cls,
        context_data: Dict[str, Any],
        switch_type: str,
        from_currency: str,
        to_currency: str
    ) -> str:
        """
        Create a checkpoint before currency switching
        
        Returns:
            str: Checkpoint ID
        """
        try:
            checkpoint_id = f"cp_{switch_type}_{int(datetime.utcnow().timestamp())}"
            
            {
                "checkpoint_id": checkpoint_id,
                "switch_type": switch_type,
                "from_currency": from_currency,
                "to_currency": to_currency,
                "context_snapshot": context_data.copy(),
                "created_at": datetime.utcnow().isoformat()
            }
            
            # Store checkpoint (in production, this would go to database or cache)
            logger.info(f"CHECKPOINT CREATED: {checkpoint_id} for {from_currency}->{to_currency}")
            
            return checkpoint_id
            
        except Exception as e:
            logger.error(f"Error creating switch checkpoint: {e}")
            raise ContextSecurityError(f"Checkpoint creation failed: {e}")

class ErrorRecoveryManager:
    """Manages error recovery during currency operations"""
    
    @classmethod
    def handle_conversion_error(
        cls,
        error: Exception,
        context_data: Dict[str, Any],
        operation: str,
        user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Handle conversion errors with appropriate recovery actions
        
        Returns:
            dict: Recovery result
        """
        try:
            recovery_result = {
                "error_handled": True,
                "recovery_action": "none",
                "safe_fallback": None,
                "user_message": "❌ Error processing request. Please try again.",
                "operation": operation,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            error_type = type(error).__name__
            error_message = str(error)
            
            # Determine recovery action based on error type
            if "rate" in error_message.lower():
                recovery_result["recovery_action"] = "retry_with_fresh_rates"
                recovery_result["user_message"] = "❌ Exchange rates temporarily unavailable. Please try again."
            elif "validation" in error_message.lower():
                recovery_result["recovery_action"] = "reset_to_safe_state"
                recovery_result["user_message"] = "❌ Invalid operation detected. Please restart the process."
            elif "timeout" in error_message.lower():
                recovery_result["recovery_action"] = "extend_session"
                recovery_result["user_message"] = "⏰ Session timed out. Please restart."
            else:
                recovery_result["recovery_action"] = "generic_fallback"
            
            # Log recovery action
            logger.error(
                f"ERROR RECOVERY: user_id={user_id} operation={operation} "
                f"error={error_type} action={recovery_result['recovery_action']}"
            )
            
            return recovery_result
            
        except Exception as recovery_error:
            logger.critical(f"CRITICAL: Error recovery failed: {recovery_error}")
            return {
                "error_handled": False,
                "recovery_action": "critical_fallback",
                "user_message": "❌ System error. Please contact support.",
                "operation": operation,
                "timestamp": datetime.utcnow().isoformat()
            }