"""
PII Protection and Encryption Service
Provides GDPR-compliant handling of personally identifiable information
"""

import logging
import json
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone
from cryptography.fernet import Fernet
import base64
import os
from dataclasses import dataclass
from enum import Enum

from models import SecurityAudit
from database import SessionLocal

logger = logging.getLogger(__name__)


class PIIType(Enum):
    """Types of PII data"""
    EMAIL = "email"
    PHONE = "phone_number"
    FULL_NAME = "full_name"
    ADDRESS = "address"
    CRYPTO_ADDRESS = "crypto_address"
    BANK_DETAILS = "bank_details"
    IP_ADDRESS = "ip_address"
    USER_AGENT = "user_agent"


@dataclass
class PIIAccessEvent:
    """PII access audit event"""
    user_id: Optional[int]
    pii_type: PIIType
    access_type: str  # read, write, delete, decrypt
    accessor_id: int  # who accessed it
    purpose: str  # why it was accessed
    ip_address: Optional[str] = None
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


class PIIEncryption:
    """PII encryption and decryption service"""
    
    def __init__(self):
        self.encryption_key = self._get_or_create_encryption_key()
        self.fernet = Fernet(self.encryption_key)
    
    def _get_or_create_encryption_key(self) -> bytes:
        """Get or create encryption key for PII"""
        # Try to get from environment first
        key_b64 = os.environ.get('PII_ENCRYPTION_KEY')
        
        if key_b64:
            try:
                return base64.urlsafe_b64decode(key_b64)
            except Exception as e:
                logger.error(f"Invalid PII encryption key format: {e}")
        
        # Generate new key if not found
        logger.warning("Generating new PII encryption key - this will invalidate existing encrypted data")
        key = Fernet.generate_key()
        
        # Store in environment for reuse (in production, use secure key management)
        os.environ['PII_ENCRYPTION_KEY'] = base64.urlsafe_b64encode(key).decode()
        
        return key
    
    def encrypt_pii(self, data: str, pii_type: PIIType) -> str:
        """
        Encrypt PII data
        
        Args:
            data: PII data to encrypt
            pii_type: Type of PII
            
        Returns:
            Encrypted data as base64 string
        """
        if not data:
            return ""
        
        try:
            # Add metadata to encrypted data
            metadata = {
                "type": pii_type.value,
                "encrypted_at": datetime.now(timezone.utc).isoformat(),
                "data": data
            }
            
            json_data = json.dumps(metadata).encode('utf-8')
            encrypted_data = self.fernet.encrypt(json_data)
            
            return base64.urlsafe_b64encode(encrypted_data).decode('utf-8')
            
        except Exception as e:
            logger.error(f"PII encryption failed for {pii_type.value}: {e}")
            raise
    
    def decrypt_pii(self, encrypted_data: str, expected_type: PIIType = None) -> str:
        """
        Decrypt PII data
        
        Args:
            encrypted_data: Encrypted data as base64 string
            expected_type: Expected PII type for validation
            
        Returns:
            Decrypted PII data
        """
        if not encrypted_data:
            return ""
        
        try:
            encrypted_bytes = base64.urlsafe_b64decode(encrypted_data.encode('utf-8'))
            decrypted_bytes = self.fernet.decrypt(encrypted_bytes)
            
            metadata = json.loads(decrypted_bytes.decode('utf-8'))
            
            # Validate type if provided
            if expected_type and metadata.get("type") != expected_type.value:
                raise ValueError(f"PII type mismatch: expected {expected_type.value}, got {metadata.get('type')}")
            
            return metadata["data"]
            
        except Exception as e:
            logger.error(f"PII decryption failed: {e}")
            raise
    
    def is_encrypted(self, data: str) -> bool:
        """Check if data appears to be encrypted PII"""
        if not data or len(data) < 50:  # Encrypted data is much longer
            return False
        
        try:
            # Try to decode as base64
            base64.urlsafe_b64decode(data.encode('utf-8'))
            return True
        except Exception:
            return False


class PIIAuditLogger:
    """Audit logger for PII access events"""
    
    @staticmethod
    def log_pii_access(event: PIIAccessEvent) -> None:
        """Log PII access event for compliance"""
        try:
            with SessionLocal() as session:
                audit_record = SecurityAudit(
                    user_id=event.user_id,
                    action=f"pii_{event.access_type}",
                    resource=f"{event.pii_type.value}:{event.user_id}",
                    ip_address=event.ip_address,
                    timestamp=event.timestamp,
                    success=True,
                    details=json.dumps({
                        "pii_type": event.pii_type.value,
                        "access_type": event.access_type,
                        "accessor_id": event.accessor_id,
                        "purpose": event.purpose
                    })
                )
                session.add(audit_record)
                session.commit()
                
        except Exception as e:
            logger.error(f"Failed to log PII access event: {e}")
    
    @staticmethod
    def get_pii_access_history(user_id: int, days: int = 30) -> List[Dict[str, Any]]:
        """Get PII access history for a user"""
        try:
            with SessionLocal() as session:
                since_date = datetime.now(timezone.utc) - timedelta(days=days)
                
                records = (
                    session.query(SecurityAudit)
                    .filter(
                        SecurityAudit.user_id == user_id,
                        SecurityAudit.action.like("pii_%"),
                        SecurityAudit.timestamp >= since_date
                    )
                    .order_by(SecurityAudit.timestamp.desc())
                    .all()
                )
                
                return [
                    {
                        "timestamp": record.timestamp.isoformat(),
                        "action": record.action,
                        "resource": record.resource,
                        "ip_address": record.ip_address,
                        "details": json.loads(record.details) if record.details else {}
                    }
                    for record in records
                ]
                
        except Exception as e:
            logger.error(f"Failed to get PII access history: {e}")
            return []


class PIIDataManager:
    """Comprehensive PII data management with encryption and audit trails"""
    
    def __init__(self):
        self.encryption_service = PIIEncryption()
        self.audit_logger = PIIAuditLogger()
    
    def store_pii(self, data: str, pii_type: PIIType, user_id: int, 
                  accessor_id: int, purpose: str, ip_address: str = None) -> str:
        """
        Securely store PII data with encryption and audit trail
        
        Args:
            data: PII data to store
            pii_type: Type of PII
            user_id: User the PII belongs to
            accessor_id: ID of person storing the data
            purpose: Purpose for storing the data
            ip_address: IP address of accessor
            
        Returns:
            Encrypted PII data for database storage
        """
        # Encrypt the data
        encrypted_data = self.encryption_service.encrypt_pii(data, pii_type)
        
        # Log the access
        self.audit_logger.log_pii_access(PIIAccessEvent(
            user_id=user_id,
            pii_type=pii_type,
            access_type="write",
            accessor_id=accessor_id,
            purpose=purpose,
            ip_address=ip_address
        ))
        
        return encrypted_data
    
    def retrieve_pii(self, encrypted_data: str, pii_type: PIIType, user_id: int,
                     accessor_id: int, purpose: str, ip_address: str = None) -> str:
        """
        Securely retrieve PII data with decryption and audit trail
        
        Args:
            encrypted_data: Encrypted PII data from database
            pii_type: Expected type of PII
            user_id: User the PII belongs to
            accessor_id: ID of person accessing the data
            purpose: Purpose for accessing the data
            ip_address: IP address of accessor
            
        Returns:
            Decrypted PII data
        """
        # Decrypt the data
        decrypted_data = self.encryption_service.decrypt_pii(encrypted_data, pii_type)
        
        # Log the access
        self.audit_logger.log_pii_access(PIIAccessEvent(
            user_id=user_id,
            pii_type=pii_type,
            access_type="read",
            accessor_id=accessor_id,
            purpose=purpose,
            ip_address=ip_address
        ))
        
        return decrypted_data
    
    def delete_pii(self, user_id: int, pii_types: List[PIIType],
                   accessor_id: int, purpose: str, ip_address: str = None) -> None:
        """
        Log PII deletion for audit trail
        
        Args:
            user_id: User whose PII is being deleted
            pii_types: Types of PII being deleted
            accessor_id: ID of person deleting the data
            purpose: Purpose for deletion (GDPR request, etc.)
            ip_address: IP address of accessor
        """
        for pii_type in pii_types:
            self.audit_logger.log_pii_access(PIIAccessEvent(
                user_id=user_id,
                pii_type=pii_type,
                access_type="delete",
                accessor_id=accessor_id,
                purpose=purpose,
                ip_address=ip_address
            ))
    
    def mask_pii_for_logs(self, data: str, pii_type: PIIType) -> str:
        """
        Mask PII data for safe logging
        
        Args:
            data: PII data to mask
            pii_type: Type of PII
            
        Returns:
            Masked PII data safe for logs
        """
        if not data:
            return "[EMPTY]"
        
        if pii_type == PIIType.EMAIL:
            if '@' in data:
                local, domain = data.rsplit('@', 1)
                return f"{local[:2]}***@{domain}"
            return f"{data[:2]}***"
        
        elif pii_type == PIIType.PHONE:
            return f"{data[:3]}***{data[-2:]}" if len(data) > 5 else "***"
        
        elif pii_type == PIIType.CRYPTO_ADDRESS:
            return f"{data[:6]}...{data[-4:]}" if len(data) > 10 else "***"
        
        elif pii_type in [PIIType.FULL_NAME, PIIType.ADDRESS]:
            return f"{data[:3]}***" if len(data) > 3 else "***"
        
        else:
            return f"{data[:2]}***" if len(data) > 2 else "***"


class GDPRComplianceManager:
    """GDPR compliance management for PII handling"""
    
    def __init__(self):
        self.pii_manager = PIIDataManager()
    
    def process_data_export_request(self, user_id: int, accessor_id: int) -> Dict[str, Any]:
        """
        Process GDPR data export request
        
        Args:
            user_id: User requesting data export
            accessor_id: ID of person processing the request
            
        Returns:
            User's personal data in portable format
        """
        try:
            with SessionLocal() as session:
                from models import User
                
                user = session.query(User).filter(User.id == user_id).first()
                if not user:
                    return {"error": "User not found"}
                
                # Export user data (non-sensitive parts)
                export_data = {
                    "user_id": user.id,
                    "telegram_id": user.telegram_id,
                    "username": user.username,
                    "created_at": user.created_at.isoformat() if user.created_at else None,
                    "pii_access_history": self.pii_manager.audit_logger.get_pii_access_history(user_id)
                }
                
                # Add encrypted PII (user can decrypt with their consent)
                if hasattr(user, 'email') and user.email:
                    export_data["encrypted_email"] = user.email
                
                if hasattr(user, 'phone_number') and user.phone_number:
                    export_data["encrypted_phone"] = user.phone_number
                
                # Log the export request
                self.pii_manager.audit_logger.log_pii_access(PIIAccessEvent(
                    user_id=user_id,
                    pii_type=PIIType.EMAIL,  # Representative
                    access_type="export",
                    accessor_id=accessor_id,
                    purpose="GDPR data export request"
                ))
                
                return export_data
                
        except Exception as e:
            logger.error(f"Data export request failed: {e}")
            return {"error": "Export failed"}
    
    def process_data_deletion_request(self, user_id: int, accessor_id: int) -> bool:
        """
        Process GDPR data deletion request
        
        Args:
            user_id: User requesting data deletion
            accessor_id: ID of person processing the request
            
        Returns:
            True if deletion successful
        """
        try:
            with SessionLocal() as session:
                from models import User
                
                user = session.query(User).filter(User.id == user_id).first()
                if not user:
                    return False
                
                # Log deletion before removing data
                pii_types = []
                if hasattr(user, 'email') and user.email:
                    pii_types.append(PIIType.EMAIL)
                if hasattr(user, 'phone_number') and user.phone_number:
                    pii_types.append(PIIType.PHONE)
                
                self.pii_manager.delete_pii(
                    user_id=user_id,
                    pii_types=pii_types,
                    accessor_id=accessor_id,
                    purpose="GDPR deletion request"
                )
                
                # Remove PII fields (keep non-PII for business records)
                if hasattr(user, 'email'):
                    user.email = None
                if hasattr(user, 'phone_number'):
                    user.phone_number = None
                
                # Mark account as anonymized
                user.telegram_id = f"DELETED_{user.id}_{int(datetime.now().timestamp())}"
                user.username = None
                
                session.commit()
                return True
                
        except Exception as e:
            logger.error(f"Data deletion request failed: {e}")
            return False


# Global instances
pii_manager = PIIDataManager()
gdpr_manager = GDPRComplianceManager()