"""
Notification Provider Interface for UTE

Standardizes notification operations across different providers (Twilio SMS, Brevo Email).
"""

from abc import abstractmethod
from typing import Dict, Any, List
from enum import Enum

from .base import BaseProvider, ProviderResult


class NotificationType(Enum):
    """Types of notifications supported by providers"""
    SMS = "sms"                    # Twilio
    EMAIL = "email"                # Brevo
    PUSH_NOTIFICATION = "push"     # Future: Push notifications
    WEBHOOK = "webhook"            # Future: Webhook notifications


class NotificationPriority(Enum):
    """Priority levels for notifications"""
    LOW = "low"
    NORMAL = "normal" 
    HIGH = "high"
    URGENT = "urgent"


class NotificationProvider(BaseProvider):
    """
    Abstract interface for notification providers
    
    Standardizes notification operations across Twilio (SMS)
    and Brevo (Email) for consistent message delivery.
    """
    
    @abstractmethod
    async def get_supported_notification_types(self) -> ProviderResult:
        """
        Get list of notification types supported by this provider
        
        Returns:
            ProviderResult with data containing List[NotificationType]
        """
        pass
    
    @abstractmethod
    async def send_notification(
        self,
        notification_type: NotificationType,
        recipient: str,
        subject: str,
        message: str,
        priority: NotificationPriority = NotificationPriority.NORMAL,
        metadata: Dict[str, Any] = None,
        template_id: str = None,
        template_variables: Dict[str, Any] = None
    ) -> ProviderResult:
        """
        Send a notification to a recipient
        
        Args:
            notification_type: Type of notification to send
            recipient: Recipient address (phone number, email, etc.)
            subject: Subject/title of the notification
            message: Message content
            priority: Priority level of the notification
            metadata: Additional metadata for the notification
            template_id: Optional template ID for formatted messages
            template_variables: Variables to substitute in template
            
        Returns:
            ProviderResult with delivery details
        """
        pass
    
    @abstractmethod
    async def send_bulk_notification(
        self,
        notification_type: NotificationType,
        recipients: List[str],
        subject: str,
        message: str,
        priority: NotificationPriority = NotificationPriority.NORMAL,
        metadata: Dict[str, Any] = None,
        template_id: str = None,
        template_variables: Dict[str, Any] = None
    ) -> ProviderResult:
        """
        Send the same notification to multiple recipients
        
        Args:
            notification_type: Type of notification to send
            recipients: List of recipient addresses
            subject: Subject/title of the notification
            message: Message content
            priority: Priority level of the notification
            metadata: Additional metadata for the notification
            template_id: Optional template ID for formatted messages
            template_variables: Variables to substitute in template
            
        Returns:
            ProviderResult with bulk delivery details
        """
        pass
    
    @abstractmethod
    async def check_delivery_status(self, external_reference: str) -> ProviderResult:
        """
        Check the delivery status of a sent notification
        
        Args:
            external_reference: Provider's reference for the notification
            
        Returns:
            ProviderResult with current delivery status
        """
        pass
    
    @abstractmethod
    async def validate_recipient(
        self,
        notification_type: NotificationType,
        recipient: str
    ) -> ProviderResult:
        """
        Validate a recipient address for the given notification type
        
        Args:
            notification_type: Type of notification
            recipient: Recipient address to validate
            
        Returns:
            ProviderResult indicating if recipient address is valid
        """
        pass
    
    @abstractmethod
    async def get_delivery_statistics(
        self,
        start_date: str = None,
        end_date: str = None,
        notification_type: NotificationType = None
    ) -> ProviderResult:
        """
        Get delivery statistics for sent notifications
        
        Args:
            start_date: Start date for statistics (ISO format)
            end_date: End date for statistics (ISO format)
            notification_type: Filter by notification type
            
        Returns:
            ProviderResult with delivery statistics
        """
        pass
    
    # Helper methods for common operations
    
    def supports_notification_type(self, notification_type: NotificationType) -> bool:
        """
        Check if this provider supports a specific notification type
        
        Args:
            notification_type: Notification type to check
            
        Returns:
            True if notification type is supported, False otherwise
        """
        # This should be implemented by checking cached supported notification types
        # Default implementation assumes provider doesn't support the notification type
        return False