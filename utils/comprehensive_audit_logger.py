"""
Comprehensive Audit Logging Framework for Telegram Escrow Bot
Provides JSON-structured logging with PII redaction, trace correlation, and contextual enrichment
"""

import logging
import json
import time
import uuid
import contextvars
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from enum import Enum
from dataclasses import dataclass, asdict
from functools import wraps
from decimal import Decimal

# Import existing PII protection
from utils.pii_protection import PIIDataManager
from config import Config

# Set up module logger for debug messages
logger = logging.getLogger(__name__)

# Context variables for trace correlation
trace_id_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar('trace_id', default=None)
session_id_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar('session_id', default=None)
user_id_context: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar('user_id', default=None)
conversation_id_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar('conversation_id', default=None)
chat_id_context: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar('chat_id', default=None)


class AuditEventType(Enum):
    """Types of audit events for categorization"""
    USER_INTERACTION = "user_interaction"  # User clicks, messages, commands
    CONVERSATION = "conversation"          # Conversation state changes
    COMMUNICATION = "communication"        # Message hub, dispute chat, support messaging
    TRANSACTION = "transaction"            # Financial operations
    ADMIN = "admin"                        # Administrative actions
    SYSTEM = "system"                      # Internal system operations


class AuditLevel(Enum):
    """Audit severity levels"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass
class RelatedIDs:
    """Container for related entity IDs"""
    escrow_id: Optional[str] = None
    exchange_order_id: Optional[str] = None
    dispute_id: Optional[str] = None
    cashout_id: Optional[str] = None
    user_rating_id: Optional[str] = None
    referral_id: Optional[str] = None
    transaction_id: Optional[str] = None
    
    # Communication-specific IDs
    message_id: Optional[str] = None          # Individual message ID
    support_ticket_id: Optional[str] = None   # Support ticket ID
    conversation_id: Optional[str] = None     # Conversation thread ID
    counterpart_user_id: Optional[str] = None # Other participant in conversation
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, filtering out None values"""
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class PayloadMetadata:
    """Sanitized payload metadata (never contains actual sensitive data)"""
    has_email: bool = False
    has_phone: bool = False
    has_crypto_address: bool = False
    has_bank_details: bool = False
    has_attachments: bool = False
    message_length: Optional[int] = None
    command: Optional[str] = None
    callback_data_type: Optional[str] = None
    button_count: Optional[int] = None
    field_count: Optional[int] = None
    
    # Communication-specific metadata
    communication_type: Optional[str] = None        # "escrow", "dispute", "support", "hub_navigation"
    message_thread_id: Optional[str] = None        # Thread/conversation identifier
    participant_count: Optional[int] = None        # Number of participants
    message_sequence_number: Optional[int] = None  # Position in conversation
    is_admin_message: bool = False                  # Message from admin/system
    has_quick_replies: bool = False                 # Message contains quick reply buttons
    chat_duration_seconds: Optional[float] = None  # Time spent in chat interface
    navigation_depth: Optional[int] = None          # UI navigation depth in message hub
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, filtering out None and False values"""
        result = {}
        for k, v in asdict(self).items():
            if v is not None and v is not False:
                result[k] = v
        return result


@dataclass
class AuditRecord:
    """Complete audit record structure"""
    timestamp: str
    environment: str
    level: str
    event_type: str
    user_id: Optional[int]
    is_admin: bool
    chat_id: Optional[int]
    message_id: Optional[int]
    session_id: Optional[str]
    conversation_id: Optional[str]
    trace_id: Optional[str]
    related_ids: Dict[str, Any]
    action: str
    result: str
    latency_ms: Optional[float]
    payload_metadata: Dict[str, Any]
    service: str = "telegram-escrow-bot"
    version: str = "1.0.0"
    
    def to_json(self) -> str:
        """Convert audit record to concise JSON string for better readability"""
        # Create a more concise structure for better log readability
        concise_record = {
            "ts": self._safe_extract_timestamp(),
            "user": self.user_id if self.user_id else "anon",
            "action": self.action,
            "result": self.result
        }
        
        # Add essential context only
        if self.latency_ms and self.latency_ms > 100:  # Only log significant latency
            concise_record["latency"] = f"{self.latency_ms:.0f}ms"
        
        if self.chat_id:
            concise_record["chat"] = self.chat_id
            
        if self.trace_id:
            concise_record["trace"] = self.trace_id[:8]  # Shortened trace ID
            
        # Add metadata only if present and relevant
        if self.payload_metadata:
            relevant_metadata = {}
            # Ensure payload_metadata is a dictionary before iterating
            try:
                # Type validation: ensure payload_metadata is a dictionary
                metadata_dict = None  # Initialize to None for safety
                
                if isinstance(self.payload_metadata, dict):
                    metadata_dict = self.payload_metadata
                elif hasattr(self.payload_metadata, 'to_dict') and callable(self.payload_metadata.to_dict):
                    # Handle PayloadMetadata objects that might not have been converted
                    metadata_dict = self.payload_metadata.to_dict()
                elif isinstance(self.payload_metadata, (str, int, float)):
                    # Handle primitive types - create a safe representation
                    metadata_dict = {"raw_value": str(self.payload_metadata), "type": type(self.payload_metadata).__name__}
                    logger.debug(f"payload_metadata is {type(self.payload_metadata).__name__} instead of dict: {self.payload_metadata}")
                else:
                    # Handle other unknown types
                    metadata_dict = {"raw_value": str(self.payload_metadata), "type": type(self.payload_metadata).__name__}
                    logger.warning(f"Unexpected payload_metadata type: {type(self.payload_metadata)} - {self.payload_metadata}")
                
                # CRITICAL FIX: Ensure metadata_dict is actually a dictionary before iteration
                if not isinstance(metadata_dict, dict):
                    # Emergency fallback if conversion failed
                    metadata_dict = {"raw_value": str(self.payload_metadata), "type": type(self.payload_metadata).__name__, "conversion_failed": True}
                    logger.warning(f"metadata_dict conversion failed, emergency fallback applied: {type(metadata_dict)}")
                
                # Now safely iterate over the validated dictionary
                for key, value in metadata_dict.items():
                    # CRITICAL FIX: Comprehensive type checking to prevent all float iteration errors
                    try:
                        # CRITICAL FIX: Convert key to string BEFORE any operations
                        str_key = str(key) if not isinstance(key, str) else key
                        
                        # Define allowed keys as a set for safer membership checking
                        allowed_keys = {'command', 'message_length', 'has_email', 'raw_value', 'type', 'conversion_failed', 'has_phone', 'has_crypto_address', 'has_bank_details'}
                        
                        # Safe membership check with guaranteed string key and verified set
                        # CRITICAL FIX: Triple-check all components before membership test
                        try:
                            if (isinstance(allowed_keys, (set, list, tuple, frozenset)) and
                                isinstance(str_key, str) and
                                str_key in allowed_keys):
                                # CRITICAL FIX: Enhanced truthiness check for all value types
                                if value is not None and value != '' and value != 0 and value is not False:
                                    relevant_metadata[str_key] = value
                        except (TypeError, AttributeError) as membership_error:
                            logger.debug(f"Membership test failed for key {str_key}: {membership_error}")
                            
                    except (TypeError, AttributeError) as e:
                        # Log but don't fail on individual metadata processing
                        logger.debug(f"Metadata key processing error: {e}")
                        
            except (TypeError, AttributeError) as e:
                # Safe fallback if anything goes wrong with metadata processing
                relevant_metadata = {"error": f"metadata_processing_failed: {str(e)}", "type": type(self.payload_metadata).__name__}
                logger.warning(f"Failed to process payload_metadata safely: {e}")
            
            if relevant_metadata:
                concise_record["meta"] = relevant_metadata
        
        # Handle Decimal serialization
        def json_serializer(obj):
            if isinstance(obj, Decimal):
                return str(obj)
            raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
        
        return json.dumps(concise_record, ensure_ascii=False, separators=(',', ':'), default=json_serializer)
    
    def _safe_extract_timestamp(self) -> str:
        """Safely extract timestamp portion, handling non-string types"""
        try:
            # Ensure timestamp is a string before any string operations
            timestamp_str = str(self.timestamp) if not isinstance(self.timestamp, str) else self.timestamp
            
            # CRITICAL FIX: Ensure timestamp_str is string before using 'in' operator
            if isinstance(timestamp_str, str) and 'T' in timestamp_str:
                time_part = timestamp_str.split('T')[1][:8] if len(timestamp_str.split('T')) > 1 else timestamp_str[:8]
            else:
                time_part = timestamp_str[:8] if isinstance(timestamp_str, str) else "unknown"
            
            return time_part
        except (TypeError, AttributeError, IndexError) as e:
            logger.debug(f"Timestamp extraction failed: {e}")
            return "unknown"


class TraceContext:
    """Manage trace context for correlation across handlers and services"""
    
    @staticmethod
    def set_trace_id(trace_id: str) -> None:
        """Set trace ID for current context"""
        trace_id_context.set(trace_id)
    
    @staticmethod
    def get_trace_id() -> Optional[str]:
        """Get current trace ID"""
        return trace_id_context.get()
    
    @staticmethod
    def generate_trace_id() -> str:
        """Generate new trace ID"""
        return str(uuid.uuid4())
    
    @staticmethod
    def set_session_id(session_id: str) -> None:
        """Set session ID for current context"""
        session_id_context.set(session_id)
    
    @staticmethod
    def get_session_id() -> Optional[str]:
        """Get current session ID"""
        return session_id_context.get()
    
    @staticmethod
    def set_user_context(user_id: int, chat_id: Optional[int] = None, 
                        conversation_id: Optional[str] = None) -> None:
        """Set user context information"""
        user_id_context.set(user_id)
        if chat_id:
            chat_id_context.set(chat_id)
        if conversation_id:
            conversation_id_context.set(conversation_id)
    
    @staticmethod
    def get_user_id() -> Optional[int]:
        """Get current user ID"""
        return user_id_context.get()
    
    @staticmethod
    def get_chat_id() -> Optional[int]:
        """Get current chat ID"""
        return chat_id_context.get()
    
    @staticmethod
    def get_conversation_id() -> Optional[str]:
        """Get current conversation ID"""
        return conversation_id_context.get()
    
    @staticmethod
    def clear_context() -> None:
        """Clear all context variables"""
        trace_id_context.set(None)
        session_id_context.set(None)
        user_id_context.set(None)
        conversation_id_context.set(None)
        chat_id_context.set(None)


class PIISafeDataExtractor:
    """Extract safe metadata from potentially sensitive data"""
    
    def __init__(self):
        self.pii_manager = PIIDataManager()
    
    def extract_safe_payload_metadata(self, data: Any) -> PayloadMetadata:
        """
        Extract safe metadata from payload without exposing PII
        
        Args:
            data: Raw payload data (message, update, etc.)
            
        Returns:
            PayloadMetadata with safe information only
        """
        metadata = PayloadMetadata()
        
        try:
            # Handle Telegram Update objects
            if hasattr(data, 'message'):
                message = data.message
                if message and message.text:
                    metadata.message_length = len(message.text)
                    metadata.command = self._extract_command(message.text)
                    metadata.has_email = self._contains_email(message.text)
                    metadata.has_phone = self._contains_phone(message.text)
                    metadata.has_crypto_address = self._contains_crypto_address(message.text)
                
                if message and hasattr(message, 'document'):
                    metadata.has_attachments = message.document is not None
            
            # Handle Telegram CallbackQuery objects
            if hasattr(data, 'callback_query'):
                callback = data.callback_query
                if callback and callback.data:
                    # CRITICAL FIX: Ensure callback.data is processed safely
                    try:
                        metadata.callback_data_type = self._extract_callback_type(callback.data)
                    except (TypeError, AttributeError) as e:
                        logger.debug(f"Callback data type extraction failed: {e}")
                        metadata.callback_data_type = f"extraction_failed_{type(callback.data).__name__}"
            
            # Handle dictionary payloads
            if isinstance(data, dict):
                metadata.field_count = len(data)
                for key, value in data.items():
                    # CRITICAL FIX: Add comprehensive type checking for both key and value
                    try:
                        if isinstance(value, str):
                            if self._contains_email(value):
                                metadata.has_email = True
                            if self._contains_phone(value):
                                metadata.has_phone = True
                            if self._contains_crypto_address(value):
                                metadata.has_crypto_address = True
                        
                        # CRITICAL FIX: Ensure key is string and value supports operations before using 'in'
                        # Convert key to string for safe operations
                        str_key = str(key) if not isinstance(key, str) else key
                        if isinstance(str_key, str) and isinstance(value, str):
                            # Now safe to do lowercase operations on guaranteed string key
                            lower_key = str_key.lower()
                            # CRITICAL FIX: Additional safety check for 'in' operation
                            # CRITICAL FIX: Triple-check all components before 'in' operation
                            if (isinstance(lower_key, str) and isinstance('bank', str) and isinstance('account', str) and
                                ('bank' in lower_key or 'account' in lower_key)):
                                metadata.has_bank_details = True
                    except (TypeError, AttributeError) as e:
                        # Log but don't fail on individual key/value processing errors
                        logger.debug(f"Payload dictionary processing error for key {type(key)}: {e}")
            
            # Handle keyboard markup
            if not isinstance(data, dict) and hasattr(data, 'reply_markup'):
                markup = data.reply_markup
                if hasattr(markup, 'inline_keyboard'):
                    button_count = sum(len(row) for row in markup.inline_keyboard)
                    metadata.button_count = button_count
        
        except Exception as e:
            # Never fail audit logging due to metadata extraction errors
            logging.getLogger(__name__).warning(f"Safe metadata extraction failed: {e}")
        
        return metadata
    
    def _extract_command(self, text: str) -> Optional[str]:
        """Extract command from message text"""
        if text.startswith('/'):
            return text.split()[0]
        return None
    
    def _contains_email(self, text: str) -> bool:
        """Check if text contains email pattern"""
        try:
            if not isinstance(text, str):
                return False
            # CRITICAL FIX: Additional safety for '@' membership check
            # Ensure text is string before any 'in' operation
            # CRITICAL FIX: Ensure text is string and '@' is string before 'in' operation
            if isinstance(text, str) and isinstance('@', str) and '@' in text:
                parts = text.split('@')
                if len(parts) > 1 and isinstance(parts[-1], str):
                    # Double-check parts[-1] is string before 'in' operation
                    # CRITICAL FIX: Ensure parts[-1] is string and '.' is string before 'in' operation
                    return (isinstance(parts[-1], str) and isinstance('.', str) and '.' in parts[-1])
            return False
        except (TypeError, AttributeError):
            return False
    
    def _contains_phone(self, text: str) -> bool:
        """Check if text contains phone pattern"""
        try:
            if not isinstance(text, str):
                return False
            # CRITICAL FIX: Ensure text is string before string operations
            if isinstance(text, str):
                return text.startswith('+') and any(c.isdigit() for c in text)
            return False
        except (TypeError, AttributeError):
            return False
    
    def _contains_crypto_address(self, text: str) -> bool:
        """Check if text contains crypto address pattern"""
        try:
            if not isinstance(text, str) or len(text) < 20:
                return False
            
            # Bitcoin addresses
            if text.startswith(('1', '3', 'bc1')):
                return True
            
            # Ethereum addresses
            if text.startswith('0x') and len(text) == 42:
                return True
            
            # Other common crypto address patterns
            common_prefixes = ['ltc1', 'D', 'L', 'M']  # Litecoin, Dogecoin
            for prefix in common_prefixes:
                # Additional safety check for startswith operation
                # CRITICAL FIX: Ensure all components are strings before startswith operation
                if (isinstance(text, str) and isinstance(prefix, str) and 
                    text.startswith(prefix) and len(text) > 25):
                    return True
            
            return False
        except (TypeError, AttributeError):
            return False
    
    def _extract_callback_type(self, callback_data: str) -> str:
        """Extract safe callback type from callback data"""
        # CRITICAL FIX: Ensure callback_data is string before using 'in' operator
        # This prevents "argument of type 'float' is not iterable" error
        try:
            if not isinstance(callback_data, str):
                return f"non_string_callback_{type(callback_data).__name__}"
            
            # Extract first part of callback data as type
            # CRITICAL FIX: Additional safety check for ':' membership
            if isinstance(callback_data, str) and isinstance(':', str) and ':' in callback_data:
                return callback_data.split(':')[0]
            return callback_data[:20] if isinstance(callback_data, str) and len(callback_data) > 20 else callback_data
        except (TypeError, AttributeError):
            # Safe fallback for any unexpected callback_data types
            return f"callback_type_extraction_failed_{type(callback_data).__name__}"


class ComprehensiveAuditLogger:
    """Main audit logging class with JSON output and PII protection"""
    
    def __init__(self, logger_name: str = "audit"):
        self.logger = logging.getLogger(f"audit.{logger_name}")
        self.data_extractor = PIISafeDataExtractor()
        
        # Configure JSON formatter if not already configured
        self._setup_json_logging()
    
    def _setup_json_logging(self):
        """Setup JSON logging format"""
        # Check if handler already has JSON formatter
        for handler in self.logger.handlers:
            if isinstance(handler.formatter, AuditJSONFormatter):
                return
        
        # Add JSON formatter to existing handlers or create new one
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            self.logger.addHandler(handler)
        
        for handler in self.logger.handlers:
            if not isinstance(handler.formatter, AuditJSONFormatter):
                handler.setFormatter(AuditJSONFormatter())
    
    def _normalize_metadata(self, metadata: Any) -> Dict[str, Any]:
        """
        Normalize payload_metadata to ensure it's always a dictionary
        
        Args:
            metadata: Input metadata of any type
            
        Returns:
            Normalized dictionary
        """
        if metadata is None:
            return {}
        
        if isinstance(metadata, dict):
            return metadata
        
        if hasattr(metadata, 'to_dict') and callable(metadata.to_dict):
            return metadata.to_dict()
        
        if isinstance(metadata, (str, int, float, bool)):
            return {
                "raw_value": str(metadata),
                "type": type(metadata).__name__
            }
        
        # Fallback for unknown types
        return {
            "raw_value": str(metadata),
            "type": type(metadata).__name__,
            "conversion_fallback": True
        }
    
    def audit(
        self,
        event_type: AuditEventType,
        action: str,
        level: AuditLevel = AuditLevel.INFO,
        result: str = "success",
        user_id: Optional[int] = None,
        is_admin: Optional[bool] = None,
        chat_id: Optional[int] = None,
        message_id: Optional[int] = None,
        related_ids: Optional[RelatedIDs] = None,
        payload: Optional[Any] = None,
        latency_ms: Optional[float] = None,
        **kwargs
    ) -> None:
        """
        Log comprehensive audit event
        
        Args:
            event_type: Type of audit event
            action: Action being performed
            level: Log level
            result: Result of the action
            user_id: User ID (optional, will use context if not provided)
            is_admin: Whether user is admin
            chat_id: Chat ID (optional, will use context if not provided)
            message_id: Message ID
            related_ids: Related entity IDs
            payload: Raw payload data (will be safely processed)
            latency_ms: Operation latency in milliseconds
            **kwargs: Additional fields
        """
        try:
            # Use context values if not provided
            user_id = user_id or TraceContext.get_user_id()
            chat_id = chat_id or TraceContext.get_chat_id()
            session_id = TraceContext.get_session_id()
            conversation_id = TraceContext.get_conversation_id()
            trace_id = TraceContext.get_trace_id()
            
            # Determine if user is admin (with safe type checking)
            if is_admin is None and user_id:
                try:
                    # Safely get admin IDs with proper type validation
                    admin_ids = getattr(Config, 'ADMIN_IDS', [])
                    
                    # CRITICAL FIX: Comprehensive type checking before any 'in' operation
                    # Ensure admin_ids is a proper list/set, not float or other non-iterable
                    if isinstance(admin_ids, (list, set, tuple, frozenset)):
                        # Additional safety: ensure user_id can be compared
                        try:
                            is_admin = user_id in admin_ids
                        except (TypeError, AttributeError) as e:
                            logger.debug(f"Error checking user_id in admin_ids: {e}")
                            is_admin = False
                    elif hasattr(admin_ids, '__iter__') and hasattr(admin_ids, '__contains__') and not isinstance(admin_ids, (str, bytes, int, float, bool, type(None))):
                        # Additional safety check for other iterable types with __contains__ method
                        try:
                            is_admin = user_id in admin_ids
                        except (TypeError, AttributeError) as e:
                            logger.debug(f"Error with iterable admin_ids: {e}")
                            is_admin = False
                    else:
                        # If ADMIN_IDS is float, string, or other non-iterable type, default to False
                        is_admin = False
                        logger.debug(f"ADMIN_IDS is not iterable (type: {type(admin_ids)}), defaulting is_admin to False")
                except (TypeError, AttributeError) as e:
                    is_admin = False
                    logger.debug(f"Error checking admin status: {e}")
            
            # Extract safe payload metadata with robust error handling
            try:
                if payload is not None:
                    payload_metadata = self.data_extractor.extract_safe_payload_metadata(payload)
                else:
                    payload_metadata = PayloadMetadata()
                
                # Ensure payload_metadata is always a safe dictionary
                if hasattr(payload_metadata, 'to_dict') and callable(payload_metadata.to_dict):
                    safe_payload_metadata = payload_metadata.to_dict()
                elif isinstance(payload_metadata, dict):
                    safe_payload_metadata = payload_metadata
                else:
                    safe_payload_metadata = {}
                    
            except Exception as e:
                # Never fail audit logging due to metadata extraction errors
                safe_payload_metadata = {"metadata_extraction_failed": str(e), "type": type(payload).__name__}
                logger.debug(f"Payload metadata extraction failed: {e}")
            
            # Normalize metadata from kwargs to handle float/primitive types
            # CRITICAL FIX: Add type checking to prevent float iteration errors
            normalized_kwargs = {}
            try:
                for key, value in kwargs.items():
                    if key == 'payload_metadata':
                        # Special handling for payload_metadata from kwargs
                        normalized_kwargs[key] = self._normalize_metadata(value)
                    else:
                        # CRITICAL FIX: Comprehensive type checking for all string operations
                        # This prevents "argument of type 'float' is not iterable" errors
                        try:
                            # CRITICAL FIX: Convert key to string first, then do operations
                            str_key = str(key) if not isinstance(key, str) else key
                            
                            # Only perform string operations on guaranteed string values
                            if isinstance(value, str) and len(value) > 0:
                                # Safe to do string operations only on actual non-empty strings
                                try:
                                    lower_value = value.lower()
                                    # CRITICAL FIX: Triple-check everything is string before 'in' operation
                                    if (isinstance(lower_value, str) and 
                                        isinstance('error', str) and isinstance('fail', str) and
                                        ('error' in lower_value or 'fail' in lower_value)):
                                        normalized_kwargs[f"{str_key}_has_error"] = True
                                except (TypeError, AttributeError) as e:
                                    logger.debug(f"Value string operation failed: {e}")
                            
                            # Check key operations separately with guaranteed string conversion
                            if isinstance(str_key, str) and len(str_key) > 0:
                                try:
                                    lower_key = str_key.lower()
                                    # CRITICAL FIX: Triple-check everything is string before 'in' operation
                                    if (isinstance(lower_key, str) and 
                                        isinstance('ip', str) and isinstance('agent', str) and
                                        ('ip' in lower_key or 'agent' in lower_key)):
                                        normalized_kwargs[f"{str_key}_is_network_info"] = True
                                except (TypeError, AttributeError) as e:
                                    logger.debug(f"Key string operation failed: {e}")
                        except (TypeError, AttributeError) as e:
                            # Log but don't fail on string operation errors
                            logger.debug(f"String operation error on key {type(key)}, value {type(value)}: {e}")
                        
                        # Always add the normalized value regardless of string operations
                        normalized_kwargs[key] = value
            except Exception as e:
                # Never fail audit logging due to kwargs processing errors
                normalized_kwargs = {"kwargs_processing_failed": str(e)}
                logger.debug(f"Kwargs processing failed: {e}")
            
            # Create audit record
            record = AuditRecord(
                timestamp=datetime.now(timezone.utc).isoformat(),
                environment=Config.CURRENT_ENVIRONMENT,
                level=level.value if hasattr(level, 'value') else str(level),
                event_type=event_type.value if hasattr(event_type, 'value') else str(event_type),
                user_id=user_id,
                is_admin=bool(is_admin),
                chat_id=chat_id,
                message_id=message_id,
                session_id=session_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
                related_ids=(related_ids.to_dict() if related_ids else {}),
                action=action,
                result=result,
                latency_ms=latency_ms,
                payload_metadata=safe_payload_metadata
            )
            
            # Add any additional fields from normalized kwargs
            # CRITICAL FIX: Add comprehensive type checking to prevent float iteration errors
            try:
                # Ensure normalized_kwargs is actually a dictionary before iteration
                if isinstance(normalized_kwargs, dict):
                    for key, value in normalized_kwargs.items():
                        # CRITICAL FIX: Convert key to string IMMEDIATELY to prevent any iteration errors
                        str_key = str(key) if not isinstance(key, str) else key
                        
                        try:
                            if hasattr(record, str_key) and str_key != 'payload_metadata':
                                setattr(record, str_key, value)
                            elif str_key == 'payload_metadata':
                                # Merge with existing payload_metadata
                                existing_metadata = record.payload_metadata
                                if isinstance(existing_metadata, dict) and isinstance(value, dict):
                                    existing_metadata.update(value)
                                else:
                                    record.payload_metadata = value
                        except (TypeError, AttributeError) as e:
                            logger.debug(f"Setting attribute {str_key} failed: {e}")
                else:
                    logger.debug(f"normalized_kwargs is not dict: {type(normalized_kwargs)}")
            except (TypeError, AttributeError) as e:
                # Never fail audit logging due to kwargs processing errors
                logger.debug(f"Kwargs attribute setting failed: {e}")
            
            # Log the record
            level_str = level.value if hasattr(level, 'value') else str(level)
            log_method = getattr(self.logger, level_str.lower())
            log_method(record.to_json())
            
        except Exception as e:
            # Never fail the main operation due to audit logging issues
            fallback_logger = logging.getLogger(__name__)
            fallback_logger.error(f"Audit logging failed: {e}")
    
    def log(self, 
            level: AuditLevel,
            event_type: AuditEventType,
            action: str,
            result: str = "logged",
            user_id: Optional[int] = None,
            is_admin: Optional[bool] = None,
            chat_id: Optional[int] = None,
            message_id: Optional[int] = None,
            related_ids: Optional[RelatedIDs] = None,
            payload: Optional[Dict[str, Any]] = None,
            latency_ms: Optional[float] = None,
            **kwargs) -> None:
        """
        General log method that wraps log_audit_event for compatibility
        with handler decorators and other components expecting a simple log() method.
        """
        try:
            self.audit(
                event_type=event_type,
                action=action,
                level=level,
                result=result,
                user_id=user_id,
                is_admin=is_admin,
                chat_id=chat_id,
                message_id=message_id,
                related_ids=related_ids,
                payload=payload,
                latency_ms=latency_ms,
                **kwargs
            )
        except Exception as e:
            # Never fail the main operation due to audit logging issues
            fallback_logger = logging.getLogger(__name__)
            fallback_logger.error(f"Audit log() method failed: {e}")


class AuditJSONFormatter(logging.Formatter):
    """Custom JSON formatter for audit logs"""
    
    def format(self, record):
        """Format log record as JSON"""
        try:
            # If the record message is already JSON, use it directly
            if hasattr(record, 'getMessage'):
                message = record.getMessage()
                # Try to parse as JSON first
                try:
                    json.loads(message)
                    return message
                except json.JSONDecodeError:
                    pass
            
            # Create JSON structure for non-JSON messages
            log_record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno
            }
            
            # Add exception info if present
            if record.exc_info:
                log_record["exception"] = self.formatException(record.exc_info)
            
            return json.dumps(log_record, ensure_ascii=False, separators=(',', ':'))
            
        except Exception:
            # Fallback to standard formatting
            return super().format(record)


def audit_decorator(
    event_type: AuditEventType,
    action: Optional[str] = None,
    level: AuditLevel = AuditLevel.INFO,
    track_latency: bool = True
):
    """
    Decorator to automatically audit function calls
    
    Args:
        event_type: Type of audit event
        action: Action name (defaults to function name)
        level: Log level
        track_latency: Whether to track execution time
    """
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time() if track_latency else None
            audit_logger = ComprehensiveAuditLogger(func.__module__)
            action_name = action or f"{func.__name__}"
            
            try:
                result = await func(*args, **kwargs)
                latency = (time.time() - start_time) * 1000 if start_time else None
                
                audit_logger.audit(
                    event_type=event_type,
                    action=action_name,
                    level=level,
                    result="success",
                    latency_ms=latency,
                    payload={"args_count": len(args), "kwargs_count": len(kwargs)}
                )
                return result
            
            except Exception as e:
                latency = (time.time() - start_time) * 1000 if start_time else None
                
                audit_logger.audit(
                    event_type=event_type,
                    action=action_name,
                    level=AuditLevel.ERROR,
                    result="error",
                    latency_ms=latency,
                    payload={"error_type": type(e).__name__, "error_message": str(e)}
                )
                raise
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time() if track_latency else None
            audit_logger = ComprehensiveAuditLogger(func.__module__)
            action_name = action or f"{func.__name__}"
            
            try:
                result = func(*args, **kwargs)
                latency = (time.time() - start_time) * 1000 if start_time else None
                
                audit_logger.audit(
                    event_type=event_type,
                    action=action_name,
                    level=level,
                    result="success",
                    latency_ms=latency,
                    payload={"args_count": len(args), "kwargs_count": len(kwargs)}
                )
                return result
            
            except Exception as e:
                latency = (time.time() - start_time) * 1000 if start_time else None
                
                audit_logger.audit(
                    event_type=event_type,
                    action=action_name,
                    level=AuditLevel.ERROR,
                    result="error",
                    latency_ms=latency,
                    payload={"error_type": type(e).__name__, "error_message": str(e)}
                )
                raise
        
        # Return appropriate wrapper based on whether function is async
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


# Convenience instances
audit_logger = ComprehensiveAuditLogger("main")
user_interaction_logger = ComprehensiveAuditLogger("user_interaction")
transaction_logger = ComprehensiveAuditLogger("transaction")
admin_logger = ComprehensiveAuditLogger("admin")
system_logger = ComprehensiveAuditLogger("system")


# Convenience functions for common audit patterns
def audit_user_interaction(action: str, update: Any = None, result: str = "success", **kwargs):
    """Audit user interaction event"""
    user_interaction_logger.audit(
        event_type=AuditEventType.USER_INTERACTION,
        action=action,
        result=result,
        payload=update,
        **kwargs
    )


def audit_transaction(action: str, amount: Optional[Decimal] = None, currency: Optional[str] = None,
                     related_ids: Optional[RelatedIDs] = None, result: str = "success", **kwargs):
    """Audit transaction event"""
    metadata = {}
    if amount is not None:
        metadata["has_amount"] = True
        metadata["amount_positive"] = amount > 0
    if currency:
        metadata["currency"] = currency
    
    transaction_logger.audit(
        event_type=AuditEventType.TRANSACTION,
        action=action,
        result=result,
        related_ids=related_ids,
        payload=metadata,
        **kwargs
    )


def audit_admin_action(action: str, admin_user_id: int, target_user_id: Optional[int] = None,
                      result: str = "success", **kwargs):
    """Audit admin action event"""
    admin_logger.audit(
        event_type=AuditEventType.ADMIN,
        action=action,
        user_id=admin_user_id,
        is_admin=True,
        result=result,
        payload={"target_user_id": target_user_id} if target_user_id else None,
        **kwargs
    )


def audit_system_event(action: str, level: AuditLevel = AuditLevel.INFO, result: str = "success", **kwargs):
    """Audit system event"""
    system_logger.audit(
        event_type=AuditEventType.SYSTEM,
        action=action,
        level=level,
        result=result,
        **kwargs
    )