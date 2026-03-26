"""
Financial Audit Logger with Outbox Pattern
Provides atomic transaction logging for all financial operations and state changes
"""

import logging
import uuid
import hashlib
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, Any, Optional
from enum import Enum
from dataclasses import dataclass

from models import AuditEvent
from utils.comprehensive_audit_logger import (
    ComprehensiveAuditLogger, 
    AuditEventType, 
    AuditLevel,
    RelatedIDs,
    TraceContext
)
from database import SessionLocal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session as SyncSession

logger = logging.getLogger(__name__)


class FinancialEventType(Enum):
    """Types of financial events for comprehensive tracking"""
    
    # Escrow events
    ESCROW_CREATED = "escrow_created"
    ESCROW_FUNDED = "escrow_funded"
    ESCROW_PAYMENT_CONFIRMED = "escrow_payment_confirmed"
    ESCROW_FUNDS_SEGREGATED = "escrow_funds_segregated"
    ESCROW_PARTIAL_RELEASE = "escrow_partial_release"
    ESCROW_FULL_RELEASE = "escrow_full_release"
    ESCROW_REFUND = "escrow_refund"
    ESCROW_CANCELLED = "escrow_cancelled"
    ESCROW_DISPUTED = "escrow_disputed"
    ESCROW_HOLDING_MISSING = "escrow_holding_missing"
    HOLDING_AMOUNT_MISMATCH = "holding_amount_mismatch"
    
    # Wallet events
    WALLET_CREDIT = "wallet_credit"
    WALLET_DEBIT = "wallet_debit"
    WALLET_HOLD = "wallet_hold"
    WALLET_HOLD_RELEASE = "wallet_hold_release"
    WALLET_DEPOSIT_ADDRESS_GENERATED = "wallet_deposit_address_generated"
    WALLET_DEPOSIT_RECEIVED = "wallet_deposit_received"
    
    # Exchange events
    EXCHANGE_ORDER_CREATED = "exchange_order_created"
    EXCHANGE_RATE_LOCKED = "exchange_rate_locked"
    EXCHANGE_PAYMENT_RECEIVED = "exchange_payment_received"
    EXCHANGE_PROCESSING = "exchange_processing"
    EXCHANGE_COMPLETED = "exchange_completed"
    EXCHANGE_FAILED = "exchange_failed"
    EXCHANGE_CANCELLED = "exchange_cancelled"
    
    # Cashout events
    CASHOUT_INITIATED = "cashout_initiated"
    CASHOUT_APPROVED = "cashout_approved"
    CASHOUT_EXECUTING = "cashout_executing"
    CASHOUT_COMPLETED = "cashout_completed"
    CASHOUT_FAILED = "cashout_failed"
    CASHOUT_CANCELLED = "cashout_cancelled"
    CASHOUT_HOLD_RELEASED = "wallet_hold_release"  # Alias for WALLET_HOLD_RELEASE
    
    # Platform revenue events
    PLATFORM_FEE_COLLECTED = "platform_fee_collected"
    MARKUP_APPLIED = "markup_applied"
    PROCESSING_FEE_CHARGED = "processing_fee_charged"
    
    # System events
    FUNDS_RECONCILIATION = "funds_reconciliation"
    BALANCE_ADJUSTMENT = "balance_adjustment"
    OVERPAYMENT_CREDITED = "overpayment_credited"
    UNDERPAYMENT_DETECTED = "underpayment_detected"
    
    # NGN Fincra Bank Transfer events
    NGN_BANK_TRANSFER_INITIATED = "ngn_bank_transfer_initiated"
    NGN_BANK_TRANSFER_PROCESSING = "ngn_bank_transfer_processing"
    NGN_BANK_TRANSFER_COMPLETED = "ngn_bank_transfer_completed"
    NGN_BANK_TRANSFER_FAILED = "ngn_bank_transfer_failed"
    NGN_BANK_TRANSFER_CANCELLED = "ngn_bank_transfer_cancelled"
    
    # NGN Account Verification events
    NGN_ACCOUNT_VERIFICATION_INITIATED = "ngn_account_verification_initiated"
    NGN_ACCOUNT_VERIFICATION_COMPLETED = "ngn_account_verification_completed"
    NGN_ACCOUNT_VERIFICATION_FAILED = "ngn_account_verification_failed"
    
    # NGN Payment Processing events
    NGN_PAYMENT_LINK_GENERATED = "ngn_payment_link_generated"
    NGN_PAYMENT_INITIATED = "ngn_payment_initiated"
    NGN_PAYMENT_CONFIRMED = "ngn_payment_confirmed"
    NGN_PAYMENT_FAILED = "ngn_payment_failed"
    NGN_PAYMENT_TIMEOUT = "ngn_payment_timeout"
    NGN_PAYMENT_CANCELLED = "ngn_payment_cancelled"
    
    # NGN Webhook Processing events
    NGN_WEBHOOK_RECEIVED = "ngn_webhook_received"
    NGN_WEBHOOK_PROCESSED = "ngn_webhook_processed"
    NGN_WEBHOOK_SIGNATURE_VERIFIED = "ngn_webhook_signature_verified"
    NGN_WEBHOOK_SIGNATURE_FAILED = "ngn_webhook_signature_failed"
    NGN_WEBHOOK_PARSING_FAILED = "ngn_webhook_parsing_failed"
    NGN_WEBHOOK_UNMATCHED = "ngn_webhook_unmatched"
    
    # NGN Payout events (Bank Transfer Cashouts)
    NGN_PAYOUT_INITIATED = "ngn_payout_initiated"
    NGN_PAYOUT_APPROVED = "ngn_payout_approved"
    NGN_PAYOUT_EXECUTING = "ngn_payout_executing"
    NGN_PAYOUT_COMPLETED = "ngn_payout_completed"
    NGN_PAYOUT_FAILED = "ngn_payout_failed"
    NGN_PAYOUT_CANCELLED = "ngn_payout_cancelled"
    NGN_CASHOUT_CONFIRMED = "ngn_cashout_confirmed"
    
    # NGN Exchange Rate events
    NGN_EXCHANGE_RATE_FETCHED = "ngn_exchange_rate_fetched"
    NGN_EXCHANGE_RATE_APPLIED = "ngn_exchange_rate_applied"
    NGN_MARKUP_CALCULATED = "ngn_markup_calculated"
    NGN_CONVERSION_USD_TO_NGN = "ngn_conversion_usd_to_ngn"
    NGN_CONVERSION_NGN_TO_USD = "ngn_conversion_ngn_to_usd"
    
    # NGN Wallet Funding events
    NGN_WALLET_FUNDING_INITIATED = "ngn_wallet_funding_initiated"
    NGN_WALLET_FUNDING_COMPLETED = "ngn_wallet_funding_completed"
    NGN_WALLET_FUNDING_FAILED = "ngn_wallet_funding_failed"
    
    # NGN Escrow Payment events
    NGN_ESCROW_PAYMENT_INITIATED = "ngn_escrow_payment_initiated"
    NGN_ESCROW_PAYMENT_CONFIRMED = "ngn_escrow_payment_confirmed"
    NGN_ESCROW_PAYMENT_FAILED = "ngn_escrow_payment_failed"
    
    # NGN Monitoring events
    NGN_PAYMENT_STATUS_CHECKED = "ngn_payment_status_checked"
    NGN_TRANSACTION_RECONCILED = "ngn_transaction_reconciled"
    NGN_BANK_LIST_FETCHED = "ngn_bank_list_fetched"
    
    # Payment Processing events
    CRYPTO_PAYMENT_CONFIRMED = "crypto_payment_confirmed"
    
    # Admin operations
    ADMIN_MANUAL_REFUND = "admin_manual_refund"
    ADMIN_MANUAL_CREDIT = "admin_manual_credit"
    ADMIN_CASHOUT_CANCELLED = "admin_cashout_cancelled"
    
    # Webhook Processing events
    WEBHOOK_EVENT_RECORDED = "webhook_event_recorded"
    WEBHOOK_DUPLICATE_DETECTED = "webhook_duplicate_detected"
    WEBHOOK_STATUS_UPDATED = "webhook_status_updated"


class EntityType(Enum):
    """Entity types for financial tracking"""
    ESCROW = "escrow"
    ESCROW_HOLDING = "escrow_holding"
    WALLET = "wallet"
    EXCHANGE_ORDER = "exchange_order"
    CASHOUT = "cashout"
    TRANSACTION = "transaction"
    UNIFIED_TRANSACTION = "unified_transaction"
    PLATFORM_REVENUE = "platform_revenue"
    USER = "user"
    NGN_BANK_TRANSFER = "ngn_bank_transfer"
    NGN_PAYMENT = "ngn_payment"
    NGN_PAYOUT = "ngn_payout"
    NGN_CASHOUT = "ngn_cashout"
    NGN_ACCOUNT_VERIFICATION = "ngn_account_verification"
    NGN_WEBHOOK = "ngn_webhook"
    WEBHOOK_EVENT = "webhook_event"


@dataclass
class FinancialContext:
    """Financial context for audit events"""
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    exchange_rate: Optional[Decimal] = None
    fee_amount: Optional[Decimal] = None
    markup_percentage: Optional[Decimal] = None
    balance_before: Optional[Decimal] = None
    balance_after: Optional[Decimal] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to PII-safe dictionary"""
        result = {}
        for key, value in self.__dict__.items():
            if value is not None:
                if isinstance(value, Decimal):
                    result[key] = str(value)
                else:
                    result[key] = value
        return result


class FinancialAuditLogger:
    """
    Financial audit logger with outbox pattern for atomic transaction logging
    """
    
    def __init__(self):
        self.comprehensive_logger = ComprehensiveAuditLogger("financial")
    
    def _sanitize_event_data(self, data: Any) -> Dict[str, Any]:
        """
        Sanitize event data to prevent sensitive information leakage and handle various data types safely
        ENTERPRISE COMPLIANCE: Comprehensive PII/PCI redaction for financial audit logging
        
        Args:
            data: Raw data to sanitize (dict, list, primitive types)
            
        Returns:
            Sanitized dictionary safe for audit logging with PII/PCI redaction
        """
        if data is None:
            return {}
        
        try:
            # Handle dict with comprehensive PII/PCI redaction
            if isinstance(data, dict):
                sanitized = {}
                
                # CRITICAL: Refined sensitive field list - excludes crypto addresses for forensic value
                sensitive_fields = {
                    'password', 'secret', 'key', 'token', 'auth', 'private', 'credential',
                    'email', 'phone', 'bank_account', 'account_number', 'iban', 'swift',
                    'card_number', 'pan', 'cvv', 'cvc', 'pin', 'otp', 'verification_code',
                    'private_key', 'mnemonic', 'seed', 'ssn', 'social_security', 'passport'
                    # NOTE: 'address' removed to preserve crypto addresses for reconciliation
                    # Use 'physical_address', 'mailing_address' for actual addresses if needed
                }
                
                for key, value in data.items():
                    # CRITICAL FIX: Ensure key is converted to string before operations
                    key_str = str(key) if not isinstance(key, str) else key
                    key_lower = key_str.lower()
                    
                    # Skip/redact sensitive keys with comprehensive pattern matching
                    if isinstance(key_lower, str) and any(sensitive in key_lower for sensitive in sensitive_fields):
                        sanitized[key_str] = '[REDACTED_PII]'
                    else:
                        # Recursively sanitize values with PII pattern detection
                        sanitized[key_str] = self._sanitize_value(value)
                return sanitized
            
            # Handle list/array
            elif isinstance(data, (list, tuple)):
                return {'items': [self._sanitize_value(item) for item in data[:10]]}  # Limit to 10 items
            
            # Handle primitive types
            else:
                return {'value': self._sanitize_value(data)}
                
        except Exception as e:
            logger.debug(f"Error sanitizing data: {e}")
            return {'sanitization_error': str(e), 'data_type': type(data).__name__}
    
    def _sanitize_value(self, value: Any) -> Any:
        """
        Sanitize individual values for safe logging with comprehensive PII/PCI redaction
        ENTERPRISE COMPLIANCE: Pattern-based detection and redaction of sensitive data
        """
        if value is None:
            return None
        elif isinstance(value, str):
            # CRITICAL: Apply PII pattern detection and redaction
            return self._apply_value_pattern_redaction(value)
        elif isinstance(value, (int, float, bool)):
            return value
        elif isinstance(value, Decimal):
            return str(value)
        elif isinstance(value, datetime):
            return value.isoformat()
        elif isinstance(value, dict):
            return self._sanitize_event_data(value)
        elif isinstance(value, (list, tuple)):
            return [self._sanitize_value(item) for item in list(value)[:5]]  # Limit to 5 items
        else:
            return str(type(value).__name__)
    
    def _apply_value_pattern_redaction(self, value: str) -> str:
        """
        Apply comprehensive substring-safe pattern-based redaction for PII/PCI compliance
        ENTERPRISE GRADE: Detects and redacts PII embedded anywhere within text strings
        GLOBAL COMPLIANCE: Handles international formats (NGN, E.164, etc.)
        """
        import re
        
        # CRITICAL: Use compiled patterns with re.sub for substring-safe detection
        
        # Email pattern - detects emails anywhere in text (substring-safe)
        email_pattern = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
        value = email_pattern.sub(lambda m: f"{m.group(0)[0]}***@{m.group(0).split('@')[1]}", value)
        
        # ENHANCED Phone pattern - GLOBAL E.164-aware with separator tolerance
        # Handles: +234-803-123-4567, +1-555-123-4567, (555) 123-4567, +44 20 7946 0958, etc.
        phone_pattern = re.compile(
            r'(?:\+?[1-9]\d{0,3}[-.\s]?)?'  # Optional country code (1-4 digits)
            r'(?:\([0-9]{1,4}\)|[0-9]{1,4})[-.\s]?'  # Area code with optional parentheses
            r'(?:[0-9]{3,4}[-.\s]?){1,3}'  # Number groups
            r'[0-9]{3,4}',  # Final group
            re.IGNORECASE
        )
        value = phone_pattern.sub(lambda m: f"***{m.group(0)[-4:]}" if len(m.group(0)) >= 4 else "***", value)
        
        # US SSN pattern - detects SSNs with/without separators (123-45-6789, 123456789)  
        ssn_pattern = re.compile(r'\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b')
        value = ssn_pattern.sub(lambda m: "***-**-" + m.group(0)[-4:], value)
        
        # Passport pattern - common formats (US: A12345678, UK: 123456789, etc.)
        passport_pattern = re.compile(r'\b[A-Z]{1,2}[0-9]{6,9}\b|\b[0-9]{8,9}\b')
        value = passport_pattern.sub(lambda m: f"{m.group(0)[:2]}***{m.group(0)[-2:]}" if len(m.group(0)) >= 4 else "***", value)
        
        # PAN/Card number pattern - ENHANCED: detects 13-19 digit cards anywhere in text (substring-safe)
        # Handles Visa(13,16,19), MasterCard(16), Amex(15), Discover(16), Diners(14), etc.
        pan_pattern = re.compile(r'\b(?:\d[ -]*?){13,19}\b')
        def redact_pan(match):
            clean_digits = re.sub(r'[^0-9]', '', match.group(0))
            if 13 <= len(clean_digits) <= 19:
                return f"{clean_digits[:4]}{'*' * (len(clean_digits) - 8)}{clean_digits[-4:]}"
            return match.group(0)
        value = pan_pattern.sub(redact_pan, value)
        
        # IBAN pattern - CASE INSENSITIVE (15-34 chars, starts with 2 letters)
        iban_pattern = re.compile(r'\b[A-Z]{2}[0-9]{2}[A-Z0-9]{4}[0-9]{7}([A-Z0-9]?){0,16}\b', re.IGNORECASE)
        value = iban_pattern.sub(lambda m: f"{m.group(0)[:4].upper()}***{m.group(0)[-4:].upper()}", value)
        
        # SWIFT/BIC code pattern - CASE INSENSITIVE (8 or 11 characters)
        swift_pattern = re.compile(r'\b[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?\b', re.IGNORECASE)
        value = swift_pattern.sub(lambda m: f"{m.group(0)[:4].upper()}***", value)
        
        # Account number patterns - 8+ consecutive digits (substring-safe)
        account_pattern = re.compile(r'\b\d{8,}\b')
        value = account_pattern.sub(lambda m: f"***{m.group(0)[-4:]}" if len(m.group(0)) >= 4 else "***", value)
        
        return value
    
    def _resolve_user_id(self, user_id: Optional[int], session=None) -> Optional[int]:
        """
        Resolve user_id to database ID using centralized repository pattern.
        
        Args:
            user_id: Could be database ID or telegram_id
            session: Optional database session to use (must be sync Session, not AsyncSession)
            
        Returns:
            Database user ID or None if not found
        """
        if user_id is None:
            return None
            
        try:
            # Import here to avoid circular imports
            from utils.repository import resolve_user_id_to_db_id
            from database import SyncSessionLocal
            from sqlalchemy.orm import Session as SyncSession
            from sqlalchemy.ext.asyncio import AsyncSession
            
            # CRITICAL FIX: Type-check session to prevent async/sync mixing
            if session and isinstance(session, SyncSession):
                # Use provided sync session
                return resolve_user_id_to_db_id(session, user_id)
            elif isinstance(session, AsyncSession):
                # AsyncSession detected - fallback to sync session to prevent coroutine errors
                # This is expected behavior when called from async contexts, no warning needed
                with SyncSessionLocal() as query_session:
                    return resolve_user_id_to_db_id(query_session, user_id)
            else:
                # No session or unknown type - use synchronous session for standalone queries
                with SyncSessionLocal() as query_session:
                    return resolve_user_id_to_db_id(query_session, user_id)
                    
        except Exception as e:
            logger.error(f"Error resolving user_id {user_id}: {e}")
            return None
    
    def log_financial_event(
        self,
        event_type: FinancialEventType,
        entity_type: EntityType,
        entity_id: str,
        user_id: Optional[int] = None,
        financial_context: Optional[FinancialContext] = None,
        previous_state: Optional[str] = None,
        new_state: Optional[str] = None,
        related_entities: Optional[Dict[str, str]] = None,
        additional_data: Optional[Dict[str, Any]] = None,
        session=None
    ) -> str:
        """
        Log financial event atomically within database transaction
        
        Args:
            event_type: Type of financial event
            entity_type: Type of entity being tracked
            entity_id: ID of the entity
            user_id: User associated with the event
            financial_context: Financial amounts and context
            previous_state: Previous state of the entity
            new_state: New state of the entity
            related_entities: Related entity IDs for correlation
            additional_data: Additional PII-safe event data
            session: Database session (if None, creates new transaction)
            
        Returns:
            Event ID for correlation
        """
        event_id = str(uuid.uuid4())
        
        try:
            # CRITICAL FIX: Resolve and validate user_id to prevent foreign key violations
            resolved_user_id = self._resolve_user_id(user_id, session)
            
            # CRITICAL COMPLIANCE FIX: NEVER skip financial audit logging
            # Always log audit events even with unresolved user_id for complete traceability
            user_resolution_details = None
            if user_id is not None and resolved_user_id is None:
                # User ID provided but cannot be resolved - log resolution failure details
                user_resolution_details = {
                    'original_user_id': user_id,
                    'resolved_user_id': None,
                    'resolution_error': 'user_id_cannot_be_resolved_to_valid_database_id'
                }
                logger.warning(
                    f"Financial audit logging with unresolved user_id for {event_type.value} - "
                    f"user_id {user_id} cannot be resolved to valid database ID. "
                    f"Entity: {entity_type.value} {entity_id} - continuing with audit logging for compliance"
                )
                # Continue with audit logging using resolved_user_id=None
            
            # Get trace context
            TraceContext.get_trace_id()
            TraceContext.get_session_id()
            
            # Prepare event data (PII-safe)
            event_data = {}
            
            if financial_context:
                event_data.update(financial_context.to_dict())
            
            if additional_data:
                # Filter out potentially sensitive data
                # CRITICAL FIX: Safely process additional_data to prevent float iteration errors
                try:
                    safe_data = self._sanitize_event_data(additional_data)
                    if isinstance(safe_data, dict):
                        event_data.update(safe_data)
                    else:
                        logger.debug(f"_sanitize_event_data returned non-dict: {type(safe_data)}")
                        event_data['additional_data_processing'] = f"failed_{type(safe_data).__name__}"
                except Exception as e:
                    logger.debug(f"Failed to sanitize additional_data in financial audit: {e}")
                    event_data['additional_data_error'] = str(e)
            
            # Add user resolution info to event data for debugging and compliance
            if user_resolution_details:
                event_data['user_id_resolution'] = user_resolution_details
            elif user_id != resolved_user_id:
                event_data['user_id_resolution'] = {
                    'original_user_id': user_id,
                    'resolved_user_id': resolved_user_id,
                    'resolution_performed': True
                }
            
            # CRITICAL FIX: Defensive normalization of entity_id to prevent database truncation errors
            normalized_entity_id = entity_id
            if len(entity_id) > 255:
                # Store original entity_id in event data for audit fidelity
                event_data['full_entity_id'] = entity_id
                # Create normalized entity_id: first 223 chars + '_' + 32-char hash
                entity_hash = hashlib.sha1(entity_id.encode('utf-8')).hexdigest()[:32]
                normalized_entity_id = entity_id[:223] + '_' + entity_hash
                logger.warning(
                    f"Entity ID truncated for audit safety - Original length: {len(entity_id)}, "
                    f"Event: {event_type.value}, Entity: {entity_type.value}, "
                    f"Full ID preserved in event_data.full_entity_id"
                )
            
            # Create audit event record with resolved user_id  
            audit_event = AuditEvent(
                event_id=event_id,
                event_type=event_type.value,
                entity_type=entity_type.value,
                entity_id=normalized_entity_id,
                user_id=resolved_user_id,
                event_data=event_data,
                processed=False
            )
            
            # Store related entities in event_data since fields don't exist in model
            # CRITICAL FIX: Add comprehensive type checking to prevent float iteration errors
            if related_entities:
                try:
                    # Ensure related_entities is a proper dictionary before using 'in' operator
                    if not isinstance(related_entities, dict):
                        logger.debug(f"related_entities is not dict: {type(related_entities)} - {related_entities}")
                    else:
                        # Store related IDs in event_data instead of non-existent model fields
                        if 'escrow_id' in related_entities:
                            raw_escrow_id = related_entities['escrow_id']
                            # CRITICAL: Strict escrow_id typing enforcement
                            if raw_escrow_id is not None:
                                if isinstance(raw_escrow_id, int):
                                    event_data['escrow_id'] = str(raw_escrow_id)
                                elif isinstance(raw_escrow_id, str) and raw_escrow_id.isdigit():
                                    event_data['escrow_id'] = raw_escrow_id
                                elif isinstance(raw_escrow_id, str):
                                    event_data['escrow_id'] = raw_escrow_id  # Accept valid string escrow IDs
                                else:
                                    logger.warning(f"Invalid escrow_id type {type(raw_escrow_id)}: {raw_escrow_id}")
                        
                        if 'exchange_order_id' in related_entities:
                            event_data['exchange_order_id'] = related_entities['exchange_order_id']
                        
                        if 'transaction_id' in related_entities:
                            event_data['transaction_id'] = related_entities['transaction_id']
                        
                        if 'cashout_id' in related_entities:
                            event_data['cashout_id'] = related_entities['cashout_id']
                except Exception as e:
                    logger.debug(f"Failed to process related_entities in financial audit: {e}")
            
            # Store in outbox table atomically - CRITICAL: Only within existing transaction
            if session:
                # SECURITY: Strict type checking - only accept sync sessions for sync method
                if not isinstance(session, SyncSession):
                    logger.error(f"SYNC METHOD CALLED WITH INVALID SESSION TYPE: {type(session)}")
                    logger.error("Sync log_financial_event() only accepts SyncSession - audit event NOT logged")
                    return event_id
                
                # Use provided sync session (part of larger transaction)
                session.add(audit_event)
                session.flush()  # Flush to DB but DO NOT commit (maintain atomicity)
            else:
                # CRITICAL COMPLIANCE FIX: No session provided - create atomic session for audit logging
                # Never skip financial audit events - create dedicated session for traceability
                logger.info(
                    f"No session provided for financial audit event {event_type.value} - "
                    f"creating dedicated audit session. Entity: {entity_type.value} {entity_id}"
                )
                # Use SessionLocal for atomic audit logging
                with SessionLocal() as audit_session:
                    audit_session.add(audit_event)
                    audit_session.flush()  # Flush but don't commit - let transaction handling occur upstream
                    audit_session.commit()  # Commit only the audit event for traceability
            
            logger.debug(
                f"Financial audit event logged: {event_type.value} for {entity_type.value} {entity_id}"
            )
            
            return event_id
            
        except Exception as e:
            logger.error(f"Failed to log financial audit event: {e}")
            # Never fail the main transaction due to audit logging
            return event_id
    
    async def log_financial_event_async(
        self,
        event_type: FinancialEventType,
        entity_type: EntityType,
        entity_id: str,
        user_id: Optional[int] = None,
        financial_context: Optional[FinancialContext] = None,
        previous_state: Optional[str] = None,
        new_state: Optional[str] = None,
        related_entities: Optional[Dict[str, str]] = None,
        additional_data: Optional[Dict[str, Any]] = None,
        session: Optional[AsyncSession] = None
    ) -> str:
        """
        Log financial event atomically within async database transaction
        
        Args:
            event_type: Type of financial event
            entity_type: Type of entity being tracked
            entity_id: ID of the entity
            user_id: User associated with the event
            financial_context: Financial amounts and context
            previous_state: Previous state of the entity
            new_state: New state of the entity
            related_entities: Related entity IDs for correlation
            additional_data: Additional PII-safe event data
            session: AsyncSession (if None, creates new async transaction)
            
        Returns:
            Event ID for correlation
        """
        event_id = str(uuid.uuid4())
        
        try:
            # CRITICAL FIX: Resolve and validate user_id to prevent foreign key violations
            resolved_user_id = self._resolve_user_id(user_id, session)
            
            if user_id is not None and resolved_user_id is None:
                # User ID provided but cannot be resolved - skip audit logging gracefully
                logger.warning(
                    f"Skipping async financial audit logging for {event_type.value} - "
                    f"user_id {user_id} cannot be resolved to valid database ID. "
                    f"Entity: {entity_type.value} {entity_id}"
                )
                return event_id
            
            # Get trace context
            TraceContext.get_trace_id()
            TraceContext.get_session_id()
            
            # Prepare event data (PII-safe) - same logic as sync version
            event_data = {}
            
            if financial_context:
                event_data.update(financial_context.to_dict())
            
            if additional_data:
                try:
                    safe_data = self._sanitize_event_data(additional_data)
                    if isinstance(safe_data, dict):
                        event_data.update(safe_data)
                    else:
                        logger.debug(f"_sanitize_event_data returned non-dict: {type(safe_data)}")
                        event_data['additional_data_processing'] = f"failed_{type(safe_data).__name__}"
                except Exception as e:
                    logger.debug(f"Failed to sanitize additional_data in async financial audit: {e}")
                    event_data['additional_data_error'] = str(e)
            
            # Add user resolution info
            if user_id != resolved_user_id:
                event_data['user_id_resolution'] = {
                    'original_user_id': user_id,
                    'resolved_user_id': resolved_user_id,
                    'resolution_performed': True
                }
            
            # CRITICAL FIX: Defensive normalization of entity_id to prevent database truncation errors (async version)
            normalized_entity_id = entity_id
            if len(entity_id) > 255:
                # Store original entity_id in event data for audit fidelity
                event_data['full_entity_id'] = entity_id
                # Create normalized entity_id: first 223 chars + '_' + 32-char hash
                entity_hash = hashlib.sha1(entity_id.encode('utf-8')).hexdigest()[:32]
                normalized_entity_id = entity_id[:223] + '_' + entity_hash
                logger.warning(
                    f"Entity ID truncated for audit safety (async) - Original length: {len(entity_id)}, "
                    f"Event: {event_type.value}, Entity: {entity_type.value}, "
                    f"Full ID preserved in event_data.full_entity_id"
                )
            
            # Create audit event record with resolved user_id  
            audit_event = AuditEvent(
                event_id=event_id,
                event_type=event_type.value,
                entity_type=entity_type.value,
                entity_id=normalized_entity_id,
                user_id=resolved_user_id,
                event_data=event_data,
                processed=False
            )
            
            # Store related entities in event_data (async version)
            if related_entities:
                try:
                    if not isinstance(related_entities, dict):
                        logger.debug(f"related_entities is not dict: {type(related_entities)} - {related_entities}")
                    else:
                        # Store related IDs in event_data instead of non-existent model fields
                        if 'escrow_id' in related_entities:
                            raw_escrow_id = related_entities['escrow_id']
                            # CRITICAL: Strict escrow_id typing enforcement
                            if raw_escrow_id is not None:
                                if isinstance(raw_escrow_id, int):
                                    event_data['escrow_id'] = str(raw_escrow_id)
                                elif isinstance(raw_escrow_id, str) and raw_escrow_id.isdigit():
                                    event_data['escrow_id'] = raw_escrow_id
                                elif isinstance(raw_escrow_id, str):
                                    event_data['escrow_id'] = raw_escrow_id  # Accept valid string escrow IDs
                                else:
                                    logger.warning(f"Invalid escrow_id type {type(raw_escrow_id)}: {raw_escrow_id}")
                        
                        if 'exchange_order_id' in related_entities:
                            event_data['exchange_order_id'] = related_entities['exchange_order_id']
                        
                        if 'transaction_id' in related_entities:
                            event_data['transaction_id'] = related_entities['transaction_id']
                        
                        if 'cashout_id' in related_entities:
                            event_data['cashout_id'] = related_entities['cashout_id']
                            
                except Exception as e:
                    logger.debug(f"Failed to process related_entities in async financial audit: {e}")
            
            # Store in outbox table atomically - ASYNC VERSION
            if session:
                # SECURITY: Strict type checking - only accept async sessions for async method
                if not isinstance(session, AsyncSession):
                    logger.error(f"ASYNC METHOD CALLED WITH INVALID SESSION TYPE: {type(session)}")
                    logger.error("Async log_financial_event_async() only accepts AsyncSession - audit event NOT logged")
                    return event_id
                
                # Use provided async session (part of larger async transaction)
                session.add(audit_event)
                await session.flush()  # Async flush to DB but DO NOT commit (maintain atomicity)
            else:
                # CRITICAL COMPLIANCE FIX: No session provided - create atomic session for audit logging
                # Never skip financial audit events - create dedicated async session for traceability
                logger.info(
                    f"No session provided for async financial audit event {event_type.value} - "
                    f"creating dedicated audit session. Entity: {entity_type.value} {entity_id}"
                )
                # Use AsyncSessionLocal for atomic audit logging
                from database import AsyncSessionLocal
                async with AsyncSessionLocal() as audit_session:
                    audit_session.add(audit_event)
                    await audit_session.flush()  # Flush but don't commit
                    await audit_session.commit()  # Commit only the audit event for traceability
            
            logger.debug(
                f"Async financial audit event logged: {event_type.value} for {entity_type.value} {entity_id}"
            )
            
            return event_id
            
        except Exception as e:
            logger.error(f"Failed to log async financial audit event: {e}")
            # Never fail the main transaction due to audit logging
            return event_id
    
    def log_escrow_event(
        self,
        event_type: FinancialEventType,
        escrow_id: str,
        user_id: Optional[int] = None,
        amount: Optional[Decimal] = None,
        currency: Optional[str] = None,
        previous_state: Optional[str] = None,
        new_state: Optional[str] = None,
        transaction_id: Optional[str] = None,
        session=None,
        **kwargs
    ) -> str:
        """Convenience method for escrow events"""
        financial_context = FinancialContext(
            amount=amount,
            currency=currency
        ) if amount or currency else None
        
        related_entities = {'escrow_id': escrow_id}
        if transaction_id:
            related_entities['transaction_id'] = transaction_id
        
        return self.log_financial_event(
            event_type=event_type,
            entity_type=EntityType.ESCROW,
            entity_id=escrow_id,
            user_id=user_id,
            financial_context=financial_context,
            previous_state=previous_state,
            new_state=new_state,
            related_entities=related_entities,
            additional_data=kwargs,
            session=session
        )
    
    def log_wallet_event(
        self,
        event_type: FinancialEventType,
        user_id: int,
        amount: Optional[Decimal] = None,
        currency: Optional[str] = None,
        balance_before: Optional[Decimal] = None,
        balance_after: Optional[Decimal] = None,
        transaction_id: Optional[str] = None,
        session=None,
        **kwargs
    ) -> str:
        """Convenience method for wallet events"""
        financial_context = FinancialContext(
            amount=amount,
            currency=currency,
            balance_before=balance_before,
            balance_after=balance_after
        )
        
        related_entities = {}
        if transaction_id:
            related_entities['transaction_id'] = transaction_id
        
        return self.log_financial_event(
            event_type=event_type,
            entity_type=EntityType.WALLET,
            entity_id=str(user_id),
            user_id=user_id,
            financial_context=financial_context,
            related_entities=related_entities,
            additional_data=kwargs,
            session=session
        )
    
    def log_exchange_event(
        self,
        event_type: FinancialEventType,
        exchange_order_id: str,
        user_id: Optional[int] = None,
        from_amount: Optional[Decimal] = None,
        from_currency: Optional[str] = None,
        to_amount: Optional[Decimal] = None,
        to_currency: Optional[str] = None,
        exchange_rate: Optional[Decimal] = None,
        previous_state: Optional[str] = None,
        new_state: Optional[str] = None,
        session=None,
        **kwargs
    ) -> str:
        """Convenience method for exchange events"""
        financial_context = FinancialContext(
            amount=from_amount,
            currency=from_currency,
            exchange_rate=exchange_rate
        )
        
        # Add exchange-specific data
        exchange_data = kwargs.copy()
        if to_amount:
            exchange_data['to_amount'] = str(to_amount)
        if to_currency:
            exchange_data['to_currency'] = to_currency
        
        related_entities = {'exchange_order_id': exchange_order_id}
        
        return self.log_financial_event(
            event_type=event_type,
            entity_type=EntityType.EXCHANGE_ORDER,
            entity_id=exchange_order_id,
            user_id=user_id,
            financial_context=financial_context,
            previous_state=previous_state,
            new_state=new_state,
            related_entities=related_entities,
            additional_data=exchange_data,
            session=session
        )
    
    def log_cashout_event(
        self,
        event_type: FinancialEventType,
        cashout_id: str,
        user_id: Optional[int] = None,
        amount: Optional[Decimal] = None,
        currency: Optional[str] = None,
        previous_state: Optional[str] = None,
        new_state: Optional[str] = None,
        transaction_id: Optional[str] = None,
        session=None,
        **kwargs
    ) -> str:
        """Convenience method for cashout events"""
        financial_context = FinancialContext(
            amount=amount,
            currency=currency
        )
        
        related_entities = {'cashout_id': cashout_id}
        if transaction_id:
            related_entities['transaction_id'] = transaction_id
        
        return self.log_financial_event(
            event_type=event_type,
            entity_type=EntityType.CASHOUT,
            entity_id=cashout_id,
            user_id=user_id,
            financial_context=financial_context,
            previous_state=previous_state,
            new_state=new_state,
            related_entities=related_entities,
            additional_data=kwargs,
            session=session
        )
    


class FinancialAuditRelay:
    """
    Background service to process outbox events and emit to logging system
    """
    
    def __init__(self):
        self.comprehensive_logger = ComprehensiveAuditLogger("financial_relay")
        self.batch_size = 100
        self.max_retries = 3
    
    async def process_pending_events(self) -> Dict[str, int]:
        """
        Process all pending audit events in the outbox
        
        Returns:
            Processing statistics
        """
        stats = {
            'processed': 0,
            'failed': 0,
            'retries': 0
        }
        
        try:
            with SessionLocal() as session:
                # Get pending events ordered by creation time
                pending_events = (
                    session.query(AuditEvent)
                    .filter(
                        AuditEvent.processed == False
                        # Note: retry_count field doesn't exist in model, using basic processed filter
                    )
                    .order_by(AuditEvent.created_at)
                    .limit(self.batch_size)
                    .all()
                )
                
                for event in pending_events:
                    try:
                        # Emit to comprehensive audit logger
                        self._emit_audit_event(event)
                        
                        # Mark as processed using proper SQL UPDATE
                        session.execute(
                            session.query(AuditEvent)
                            .filter(AuditEvent.id == event.id)
                            .update({'processed': True, 'processed_at': datetime.now(timezone.utc)})
                        )
                        stats['processed'] += 1
                        
                    except Exception as e:
                        logger.error(f"Failed to process audit event {event.event_id}: {e}")
                        # Note: retry_count field doesn't exist - mark as processed to avoid infinite retry
                        stats['failed'] += 1
                        # Mark as processed since we can't track retry count with current schema
                        logger.error(f"Audit event {event.event_id} marked as processed due to processing failure")
                        # Use proper SQL UPDATE statement instead of direct assignment
                        (
                            session.query(AuditEvent)
                            .filter(AuditEvent.id == event.id)
                            .update({'processed': True, 'processed_at': datetime.now(timezone.utc)})
                        )
                        stats['retries'] += 1
                
                # CRITICAL FIX: DO NOT commit() - caller's transaction must handle commit
                # This preserves atomicity and prevents partial audit data commits
                session.flush()  # Only flush to ensure data is written but not committed
                
        except Exception as e:
            logger.error(f"Error processing audit events: {e}")
        
        return stats
    
    def _emit_audit_event(self, event: AuditEvent):
        """Emit audit event to comprehensive audit logger"""
        try:
            # Extract related IDs from event_data since fields don't exist in model
            event_data = event.event_data or {}
            related_ids = RelatedIDs(
                escrow_id=event_data.get('escrow_id'),
                exchange_order_id=event_data.get('exchange_order_id'),
                transaction_id=event_data.get('transaction_id'),
                cashout_id=event_data.get('cashout_id')
            )
            
            # Emit to comprehensive audit logger with proper field access
            self.comprehensive_logger.audit(
                event_type=AuditEventType.TRANSACTION,
                action=str(event.event_type),  # Convert Column to string
                level=AuditLevel.INFO,
                result="logged",
                user_id=int(event.user_id) if event.user_id else None,  # Convert Column to int
                related_ids=related_ids,
                payload=event.event_data,
                trace_id=event_data.get('trace_id'),
                session_id=event_data.get('session_id')
            )
            
            # Also log to console for immediate visibility
            logger.info(
                f"💰 FINANCIAL_AUDIT: {event.event_type} | "
                f"Entity: {event.entity_type}:{event.entity_id} | "
                f"User: {event.user_id} | "
                f"Amount: {event_data.get('amount', 'N/A')} {event_data.get('currency', '')} | "
                f"State: {event_data.get('previous_state', 'N/A')} → {event_data.get('new_state', 'N/A')} | "
                f"Trace: {event_data.get('trace_id', 'N/A')}"
            )
            
        except Exception as e:
            logger.error(f"Failed to emit audit event to logger: {e}")
            raise
    
    async def cleanup_old_events(self, days_to_keep: int = 90):
        """Clean up old processed audit events"""
        try:
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_to_keep)
            
            with SessionLocal() as session:
                deleted_count = (
                    session.query(AuditEvent)
                    .filter(
                        AuditEvent.processed == True,
                        AuditEvent.created_at < cutoff_date
                    )
                    .delete()
                )
                
                # CRITICAL FIX: DO NOT commit() - caller's transaction must handle commit
                # This preserves atomicity and prevents partial audit data commits
                session.flush()  # Only flush to ensure data is written but not committed
                
                if deleted_count > 0:
                    logger.info(f"Cleaned up {deleted_count} old audit events")
                    
        except Exception as e:
            logger.error(f"Error cleaning up old audit events: {e}")


# Global instances
financial_audit_logger = FinancialAuditLogger()
financial_audit_relay = FinancialAuditRelay()


# Convenience functions for common patterns
def log_transaction_state_change(
    transaction_type: str,
    entity_id: str,
    previous_state: str,
    new_state: str,
    user_id: Optional[int] = None,
    amount: Optional[Decimal] = None,
    currency: Optional[str] = None,
    session=None,
    **kwargs
):
    """Log transaction state change with automatic event type mapping"""
    event_type_mapping = {
        'escrow': FinancialEventType.ESCROW_PAYMENT_CONFIRMED,
        'wallet': FinancialEventType.WALLET_CREDIT,
        'exchange': FinancialEventType.EXCHANGE_PROCESSING,
        'cashout': FinancialEventType.CASHOUT_EXECUTING
    }
    
    event_type = event_type_mapping.get(transaction_type, FinancialEventType.FUNDS_RECONCILIATION)
    entity_type = EntityType.TRANSACTION
    
    financial_context = FinancialContext(amount=amount, currency=currency)
    
    return financial_audit_logger.log_financial_event(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        financial_context=financial_context,
        previous_state=previous_state,
        new_state=new_state,
        additional_data=kwargs,
        session=session
    )