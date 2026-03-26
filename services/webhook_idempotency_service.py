"""
Webhook Idempotency Service
Comprehensive webhook idempotency protection to prevent webhook replays and ensure financial safety
"""

import logging
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, Any, Optional, Tuple, List, cast
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, and_, or_

from models import WebhookEventLedger
from utils.atomic_transactions import atomic_transaction
from utils.data_sanitizer import sanitize_for_log, safe_error_log
from utils.financial_audit_logger import (
    FinancialAuditLogger,
    FinancialEventType,
    EntityType,
    FinancialContext
)


logger = logging.getLogger(__name__)
audit_logger = FinancialAuditLogger()


class WebhookProvider(Enum):
    """Supported webhook providers"""
    DYNOPAY = "dynopay"
    BLOCKBEE = "blockbee"
    FINCRA = "fincra"


class WebhookEventStatus(Enum):
    """Webhook event processing status"""
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DUPLICATE = "duplicate"


@dataclass
class WebhookEventInfo:
    """Information about a webhook event for processing"""
    provider: WebhookProvider
    event_id: str
    event_type: str  # Required field for webhook categorization
    txid: Optional[str] = None
    reference_id: Optional[str] = None
    amount: Optional[Decimal] = None
    currency: Optional[str] = None
    user_id: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
    webhook_payload: Optional[str] = None


@dataclass
class IdempotencyResult:
    """Result of idempotency check"""
    is_duplicate: bool
    webhook_event_id: Optional[int] = None
    previous_status: Optional[str] = None
    previous_result: Optional[str] = None
    error_message: Optional[str] = None


@dataclass
class ProcessingResult:
    """Result of webhook processing"""
    success: bool
    webhook_event_id: int
    processing_duration_ms: Optional[int] = None
    result_data: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None


class WebhookIdempotencyService:
    """
    Comprehensive webhook idempotency service to prevent webhook replays
    and ensure financial safety across all payment providers.
    """

    @staticmethod
    def validate_webhook_timestamp(
        webhook_created_at: Optional[datetime] = None,
        max_age_seconds: int = 300,
        max_future_seconds: int = 60
    ) -> Tuple[bool, Optional[str]]:
        """
        Validate webhook timestamp to prevent replay attacks.
        
        Args:
            webhook_created_at: When the webhook was created (timezone-aware)
            max_age_seconds: Maximum age of webhook in seconds (default: 300 = 5 minutes)
            max_future_seconds: Maximum acceptable future timestamp (default: 60 seconds)
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        # CRITICAL SECURITY: Reject webhooks without timestamps to prevent replay attacks
        if webhook_created_at is None:
            error_msg = "Webhook timestamp is required for security"
            logger.error(f"🚨 TIMESTAMP_REQUIRED: {error_msg}")
            return False, error_msg
        
        # Ensure webhook_created_at is timezone-aware
        if webhook_created_at.tzinfo is None:
            webhook_created_at = webhook_created_at.replace(tzinfo=timezone.utc)
        
        current_time = datetime.now(timezone.utc)
        age_seconds = (current_time - webhook_created_at).total_seconds()
        
        # Check if webhook is too old (replay attack)
        if age_seconds > max_age_seconds:
            error_msg = f"Webhook too old: {age_seconds:.1f}s (max: {max_age_seconds}s)"
            logger.error(f"🚨 REPLAY_ATTACK_PREVENTED: {error_msg}")
            return False, error_msg
        
        # Check if webhook is from the future (clock skew attack)
        if age_seconds < -max_future_seconds:
            error_msg = f"Webhook timestamp from future: {age_seconds:.1f}s"
            logger.error(f"🚨 FUTURE_TIMESTAMP_REJECTED: {error_msg}")
            return False, error_msg
        
        logger.info(f"✅ TIMESTAMP_VALID: Webhook age: {age_seconds:.1f}s (within acceptable range)")
        return True, None

    @staticmethod
    def _check_blockbee_txid_idempotency(session: Session, webhook_info: WebhookEventInfo) -> IdempotencyResult:
        """
        BlockBee-specific txid/state-aware idempotency check.
        
        Allows unconfirmed→confirmed transitions for the same txid while preventing true duplicates.
        """
        # Look for existing webhook events for this txid
        existing_events = (
            session.query(WebhookEventLedger)
            .filter(
                and_(
                    WebhookEventLedger.event_provider == WebhookProvider.BLOCKBEE.value,
                    WebhookEventLedger.txid == webhook_info.txid
                )
            )
            .order_by(WebhookEventLedger.processed_at.desc())
            .all()
        )
        
        if not existing_events:
            logger.info(
                f"✅ BLOCKBEE_TXID_IDEMPOTENCY: New txid - "
                f"TxID: {webhook_info.txid}, Event ID: {webhook_info.event_id}"
            )
            return IdempotencyResult(is_duplicate=False)
        
        # Extract confirmation status from metadata to determine state transition
        current_confirmed = WebhookIdempotencyService._extract_confirmation_status(webhook_info.metadata)
        
        for existing_event in existing_events:
            # Check if this is the exact same event (provider + event_id)
            # Cast to str for type-safe comparison
            if str(existing_event.event_id) == webhook_info.event_id:
                logger.info(
                    f"🔍 BLOCKBEE_TXID_IDEMPOTENCY: Exact duplicate detected - "
                    f"TxID: {webhook_info.txid}, Event ID: {webhook_info.event_id}, "
                    f"Previous Status: {existing_event.status}"
                )
                
                # Cast id to int for type safety - use cast() to help type checker
                event_id_val = int(cast(int, existing_event.id)) if existing_event.id is not None else None
                
                return IdempotencyResult(
                    is_duplicate=True,
                    webhook_event_id=event_id_val,
                    previous_status=str(existing_event.status),
                    previous_result=str(existing_event.processing_result) if existing_event.processing_result is not None else None,
                    error_message=str(existing_event.error_message) if existing_event.error_message is not None else None
                )
            
            # Check for state transitions - safely convert metadata from JSONB column
            # Cast to dict for type safety - JSONB returns dict-like object
            metadata_val = cast(Dict[str, Any], existing_event.event_metadata) if existing_event.event_metadata is not None else {}
            existing_confirmed = WebhookIdempotencyService._extract_confirmation_status(metadata_val)
            
            # Allow unconfirmed → confirmed transition
            if not existing_confirmed and current_confirmed:
                logger.info(
                    f"✅ BLOCKBEE_TXID_IDEMPOTENCY: Allowing unconfirmed→confirmed transition - "
                    f"TxID: {webhook_info.txid}, "
                    f"Previous Event: {existing_event.event_id} (unconfirmed), "
                    f"Current Event: {webhook_info.event_id} (confirmed)"
                )
                continue  # Allow this transition
            
            # Prevent confirmed → unconfirmed (invalid backward transition)
            if existing_confirmed and not current_confirmed:
                logger.warning(
                    f"⚠️ BLOCKBEE_TXID_IDEMPOTENCY: Invalid backward transition blocked - "
                    f"TxID: {webhook_info.txid}, "
                    f"Previous Event: {existing_event.event_id} (confirmed), "
                    f"Current Event: {webhook_info.event_id} (unconfirmed)"
                )
                
                # Cast id to int for type safety - use cast() to help type checker
                event_id_val = int(cast(int, existing_event.id)) if existing_event.id is not None else None
                
                return IdempotencyResult(
                    is_duplicate=True,
                    webhook_event_id=event_id_val,
                    previous_status=str(existing_event.status),
                    previous_result="Invalid backward transition: confirmed→unconfirmed",
                    error_message="Backward transition not allowed"
                )
            
            # Prevent confirmed → confirmed (true duplicate) 
            if existing_confirmed and current_confirmed:
                logger.warning(
                    f"⚠️ BLOCKBEE_TXID_IDEMPOTENCY: Confirmed duplicate blocked - "
                    f"TxID: {webhook_info.txid}, "
                    f"Previous Event: {existing_event.event_id} (confirmed), "
                    f"Current Event: {webhook_info.event_id} (confirmed)"
                )
                
                # Cast id to int for type safety - use cast() to help type checker
                event_id_val = int(cast(int, existing_event.id)) if existing_event.id is not None else None
                
                return IdempotencyResult(
                    is_duplicate=True,
                    webhook_event_id=event_id_val,
                    previous_status=str(existing_event.status),
                    previous_result="Duplicate confirmed transaction",
                    error_message="Confirmed transaction already processed"
                )
        
        # No blocking duplicates found, allow processing
        logger.info(
            f"✅ BLOCKBEE_TXID_IDEMPOTENCY: Valid transition allowed - "
            f"TxID: {webhook_info.txid}, Event ID: {webhook_info.event_id}, "
            f"Confirmed: {current_confirmed}"
        )
        
        return IdempotencyResult(is_duplicate=False)

    @staticmethod
    def _extract_confirmation_status(metadata: Optional[Dict[str, Any]]) -> bool:
        """
        Extract confirmation status from webhook metadata.
        
        For BlockBee webhooks, this is typically in the 'confirmed' field.
        """
        if not metadata:
            return False
        
        # BlockBee uses 'confirmed' boolean field
        return bool(metadata.get('confirmed', False))

    @staticmethod
    def check_idempotency(webhook_info: WebhookEventInfo) -> IdempotencyResult:
        """
        Check if webhook event has already been processed, with txid/state-aware logic for BlockBee.
        
        For BlockBee webhooks, allows unconfirmed→confirmed transitions for the same txid
        while preventing true duplicates.
        
        Args:
            webhook_info: Information about the webhook event
            
        Returns:
            IdempotencyResult indicating if this is a duplicate and previous processing info
        """
        try:
            with atomic_transaction() as session:
                # For BlockBee, use txid/state-aware duplicate detection
                if webhook_info.provider == WebhookProvider.BLOCKBEE and webhook_info.txid:
                    return WebhookIdempotencyService._check_blockbee_txid_idempotency(session, webhook_info)
                
                # Default logic for other providers: check by (provider, event_id)
                existing_event = (
                    session.query(WebhookEventLedger)
                    .filter(
                        and_(
                            WebhookEventLedger.event_provider == webhook_info.provider.value,
                            WebhookEventLedger.event_id == webhook_info.event_id
                        )
                    )
                    .first()
                )
                
                if existing_event:
                    # If previous attempt failed, allow retry
                    # Use str() for type-safe comparison with enum value
                    if str(existing_event.status) == WebhookEventStatus.FAILED.value:
                        logger.info(
                            f"🔄 WEBHOOK_RETRY_ALLOWED: Previous attempt failed, allowing retry - "
                            f"Provider: {webhook_info.provider.value}, "
                            f"Event ID: {webhook_info.event_id}, "
                            f"Previous Status: {existing_event.status}"
                        )
                        # Update status to processing for retry - use setattr for type safety
                        setattr(existing_event, 'status', WebhookEventStatus.PROCESSING.value)
                        setattr(existing_event, 'updated_at', datetime.utcnow())
                        retry_count_val = int(cast(int, existing_event.retry_count)) if existing_event.retry_count is not None else 0
                        setattr(existing_event, 'retry_count', retry_count_val + 1)
                        session.commit()
                        
                        # Cast id to int for type safety - use cast() to help type checker
                        event_id_val = int(cast(int, existing_event.id)) if existing_event.id is not None else None
                        
                        return IdempotencyResult(
                            is_duplicate=False,
                            webhook_event_id=event_id_val
                        )
                    
                    logger.info(
                        f"🔍 WEBHOOK_IDEMPOTENCY: Duplicate detected - "
                        f"Provider: {webhook_info.provider.value}, "
                        f"Event ID: {webhook_info.event_id}, "
                        f"Previous Status: {existing_event.status}, "
                        f"Processed At: {existing_event.processed_at}"
                    )
                    
                    # Log audit event for duplicate detection - pass session to maintain atomicity
                    audit_logger.log_financial_event(
                        event_type=FinancialEventType.WEBHOOK_DUPLICATE_DETECTED,
                        entity_type=EntityType.WEBHOOK_EVENT,
                        entity_id=webhook_info.event_id,
                        user_id=webhook_info.user_id,
                        financial_context=FinancialContext(
                            amount=webhook_info.amount or Decimal('0'),
                            currency=webhook_info.currency or 'UNKNOWN'
                        ),
                        previous_state="new",
                        new_state="duplicate_detected",
                        related_entities={
                            "provider": webhook_info.provider.value,
                            "original_processed_at": existing_event.processed_at.isoformat() if existing_event.processed_at is not None else "None",
                            "original_status": str(existing_event.status)
                        },
                        additional_data={
                            "source": "webhook_idempotency_service.check_idempotency",
                            "webhook_event_id": str(existing_event.id),
                            "txid": webhook_info.txid or "None",
                            "reference_id": webhook_info.reference_id or "None"
                        },
                        session=session  # CRITICAL FIX: Pass session for atomic audit logging
                    )
                    
                    # Cast id to int for type safety - use cast() to help type checker
                    event_id_val = int(cast(int, existing_event.id)) if existing_event.id is not None else None
                    
                    return IdempotencyResult(
                        is_duplicate=True,
                        webhook_event_id=event_id_val,
                        previous_status=str(existing_event.status),
                        previous_result=str(existing_event.processing_result) if existing_event.processing_result is not None else None,
                        error_message=str(existing_event.error_message) if existing_event.error_message is not None else None
                    )
                
                logger.info(
                    f"✅ WEBHOOK_IDEMPOTENCY: New event - "
                    f"Provider: {webhook_info.provider.value}, "
                    f"Event ID: {webhook_info.event_id}"
                )
                
                return IdempotencyResult(is_duplicate=False)
                
        except Exception as e:
            safe_error = safe_error_log(e)
            logger.error(f"❌ WEBHOOK_IDEMPOTENCY: Error checking idempotency: {safe_error}")
            
            # On error, assume it's NOT a duplicate to avoid blocking legitimate webhooks
            # Better to risk duplicate processing than to block valid payments
            return IdempotencyResult(
                is_duplicate=False,
                error_message=f"Idempotency check failed: {safe_error}"
            )

    @staticmethod
    def record_webhook_event(webhook_info: WebhookEventInfo) -> Optional[int]:
        """
        Record a new webhook event in the ledger atomically.
        
        Args:
            webhook_info: Information about the webhook event
            
        Returns:
            The ID of the created webhook event record, or None if failed
        """
        try:
            with atomic_transaction() as session:
                # Sanitize webhook payload for storage
                # CRITICAL FIX: Database payload column is NOT NULL, so provide default empty JSON
                sanitized_payload = "{}"  # Default empty JSON for NOT NULL constraint
                if webhook_info.webhook_payload:
                    try:
                        payload_data = json.loads(webhook_info.webhook_payload)
                        sanitized_payload = json.dumps(sanitize_for_log(payload_data), default=str)
                    except Exception:
                        sanitized_payload = str(webhook_info.webhook_payload)[:10000]  # Truncate if needed
                
                # Create webhook event record
                webhook_event = WebhookEventLedger(
                    event_provider=webhook_info.provider.value,
                    event_id=webhook_info.event_id,
                    event_type=webhook_info.event_type,
                    payload=json.loads(sanitized_payload),  # CRITICAL FIX: Populate the NOT NULL payload field
                    txid=webhook_info.txid,
                    reference_id=webhook_info.reference_id,
                    status=WebhookEventStatus.PROCESSING.value,
                    amount=webhook_info.amount,
                    currency=webhook_info.currency,
                    user_id=webhook_info.user_id,
                    webhook_payload=sanitized_payload,
                    event_metadata=webhook_info.metadata,
                    retry_count=0
                )
                
                session.add(webhook_event)
                session.flush()  # Get the ID
                # Cast to int for type safety - after flush(), id is populated
                webhook_event_id = cast(int, webhook_event.id)
                
                logger.info(
                    f"📝 WEBHOOK_LEDGER: Recorded new event - "
                    f"ID: {webhook_event_id}, "
                    f"Provider: {webhook_info.provider.value}, "
                    f"Event ID: {webhook_info.event_id}, "
                    f"TxID: {webhook_info.txid}, "
                    f"Reference: {webhook_info.reference_id}"
                )
                
                # Log audit event for webhook recording - pass session to maintain atomicity
                audit_logger.log_financial_event(
                    event_type=FinancialEventType.WEBHOOK_EVENT_RECORDED,
                    entity_type=EntityType.WEBHOOK_EVENT,
                    entity_id=webhook_info.event_id,
                    user_id=webhook_info.user_id,
                    financial_context=FinancialContext(
                        amount=webhook_info.amount or Decimal('0'),
                        currency=webhook_info.currency or 'UNKNOWN'
                    ),
                    previous_state="new",
                    new_state="recorded",
                    related_entities={
                        "provider": webhook_info.provider.value,
                        "webhook_event_id": str(webhook_event_id)
                    },
                    additional_data={
                        "source": "webhook_idempotency_service.record_webhook_event",
                        "txid": webhook_info.txid,
                        "reference_id": webhook_info.reference_id
                    },
                    session=session  # CRITICAL FIX: Pass session for atomic audit logging
                )
                
                # Return webhook_event_id (already int from flush())
                return webhook_event_id
                
        except IntegrityError as e:
            # This means another process recorded the same event concurrently
            safe_error = safe_error_log(e)
            logger.warning(
                f"⚠️ WEBHOOK_RACE_CONDITION: Concurrent event recording detected - "
                f"Provider: {webhook_info.provider.value}, "
                f"Event ID: {webhook_info.event_id}. "
                f"Error: {safe_error}"
            )
            return None
            
        except Exception as e:
            safe_error = safe_error_log(e)
            logger.error(
                f"❌ WEBHOOK_LEDGER: Failed to record event - "
                f"Provider: {webhook_info.provider.value}, "
                f"Event ID: {webhook_info.event_id}. "
                f"Error: {safe_error}"
            )
            return None

    @staticmethod
    def update_processing_status(
        webhook_event_id: int,
        status: WebhookEventStatus,
        processing_duration_ms: Optional[int] = None,
        result_data: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        retry_count: Optional[int] = None
    ) -> bool:
        """
        Update the processing status of a webhook event.
        
        Args:
            webhook_event_id: ID of the webhook event record
            status: New processing status
            processing_duration_ms: Time taken to process in milliseconds
            result_data: Result data from processing
            error_message: Error message if processing failed
            retry_count: Updated retry count
            
        Returns:
            True if update was successful, False otherwise
        """
        try:
            with atomic_transaction() as session:
                webhook_event = (
                    session.query(WebhookEventLedger)
                    .filter(WebhookEventLedger.id == webhook_event_id)
                    .first()
                )
                
                if not webhook_event:
                    logger.error(f"❌ WEBHOOK_UPDATE: Event not found - ID: {webhook_event_id}")
                    return False
                
                # Store previous status for audit
                previous_status = str(webhook_event.status)
                
                # Update status and metadata
                setattr(webhook_event, 'status', status.value)
                setattr(webhook_event, 'processing_duration_ms', processing_duration_ms)
                setattr(webhook_event, 'error_message', error_message)
                
                if retry_count is not None:
                    setattr(webhook_event, 'retry_count', retry_count)
                
                if result_data:
                    setattr(webhook_event, 'processing_result', json.dumps(result_data, default=str))
                
                if status in [WebhookEventStatus.COMPLETED, WebhookEventStatus.FAILED]:
                    setattr(webhook_event, 'completed_at', func.now())
                
                logger.info(
                    f"📊 WEBHOOK_UPDATE: Status updated - "
                    f"ID: {webhook_event_id}, "
                    f"Provider: {webhook_event.event_provider}, "
                    f"Event ID: {webhook_event.event_id}, "
                    f"Status: {previous_status} → {status.value}, "
                    f"Duration: {processing_duration_ms}ms"
                )
                
                # Log audit event for status update - pass session to maintain atomicity
                # Cast all values to proper types for type safety using cast() to help type checker
                amount_val = Decimal(str(cast(Decimal, webhook_event.amount))) if webhook_event.amount is not None else Decimal('0')
                currency_val = str(cast(str, webhook_event.currency)) if webhook_event.currency is not None else 'UNKNOWN'
                user_id_val = int(cast(int, webhook_event.user_id)) if webhook_event.user_id is not None else None
                
                audit_logger.log_financial_event(
                    event_type=FinancialEventType.WEBHOOK_STATUS_UPDATED,
                    entity_type=EntityType.WEBHOOK_EVENT,
                    entity_id=str(webhook_event.event_id),
                    user_id=user_id_val,
                    financial_context=FinancialContext(
                        amount=amount_val,
                        currency=currency_val
                    ),
                    previous_state=previous_status,
                    new_state=status.value,
                    related_entities={
                        "provider": str(webhook_event.event_provider),
                        "webhook_event_id": str(webhook_event_id),
                        "processing_duration_ms": str(processing_duration_ms) if processing_duration_ms is not None else "None"
                    },
                    additional_data={
                        "source": "webhook_idempotency_service.update_processing_status",
                        "txid": webhook_event.txid,
                        "reference_id": webhook_event.reference_id,
                        "retry_count": webhook_event.retry_count,
                        "has_error": error_message is not None
                    },
                    session=session  # CRITICAL FIX: Pass session for atomic audit logging
                )
                
                return True
                
        except Exception as e:
            safe_error = safe_error_log(e)
            logger.error(
                f"❌ WEBHOOK_UPDATE: Failed to update status - "
                f"ID: {webhook_event_id}, "
                f"Status: {status.value}. "
                f"Error: {safe_error}"
            )
            return False

    @classmethod
    async def process_webhook_with_idempotency(
        cls,
        webhook_info: WebhookEventInfo,
        processing_function,
        *args,
        **kwargs
    ) -> ProcessingResult:
        """
        Process a webhook with comprehensive idempotency protection.
        
        This is the main entry point for webhook processing that:
        1. Validates webhook timestamp (replay attack protection)
        2. Checks for duplicates
        3. Records the event
        4. Executes the processing function
        5. Updates the status based on results
        
        Args:
            webhook_info: Information about the webhook event
            processing_function: Function to execute for processing
            *args, **kwargs: Arguments to pass to the processing function
            
        Returns:
            ProcessingResult with success status and details
        """
        start_time = time.time()
        webhook_event_id = None
        
        try:
            # Step 0: Validate webhook timestamp to prevent replay attacks
            # Extract timestamp from metadata or use created_at from database
            webhook_timestamp = None
            
            if webhook_info.metadata and 'timestamp' in webhook_info.metadata:
                webhook_timestamp = webhook_info.metadata['timestamp']
                
                # Convert to datetime based on type
                if isinstance(webhook_timestamp, str):
                    # Parse ISO format string (e.g., "2025-10-08T12:00:00+00:00")
                    webhook_timestamp = datetime.fromisoformat(webhook_timestamp.replace('Z', '+00:00'))
                elif isinstance(webhook_timestamp, (int, float)):
                    # Convert Unix timestamp
                    webhook_timestamp = datetime.fromtimestamp(webhook_timestamp, tz=timezone.utc)
                
                # Validate timestamp with strict enforcement
                is_valid, error_message = cls.validate_webhook_timestamp(webhook_timestamp)
                if not is_valid:
                    logger.critical(
                        f"🚨 REPLAY_ATTACK_BLOCKED: Webhook rejected - {error_message} - "
                        f"Provider: {webhook_info.provider.value}, Event ID: {webhook_info.event_id}"
                    )
                    return ProcessingResult(
                        success=False,
                        webhook_event_id=0,
                        error_message=f"Webhook timestamp validation failed: {error_message}"
                    )
            else:
                # SECURITY ENFORCEMENT: Timestamp is MANDATORY for replay attack protection
                # Reject webhooks without timestamps to prevent replay attacks
                logger.critical(
                    f"🚨 TIMESTAMP_REQUIRED: Webhook rejected - No timestamp provided - "
                    f"Provider: {webhook_info.provider.value}, Event ID: {webhook_info.event_id}. "
                    f"All webhooks must include a timestamp for replay attack protection."
                )
                return ProcessingResult(
                    success=False,
                    webhook_event_id=0,
                    error_message="Webhook timestamp is required for security. No timestamp found in metadata."
                )
            
            # Step 1: Check for duplicates
            idempotency_result = cls.check_idempotency(webhook_info)
            
            if idempotency_result.is_duplicate:
                logger.info(
                    f"🔄 WEBHOOK_IDEMPOTENT: Returning cached result - "
                    f"Provider: {webhook_info.provider.value}, "
                    f"Event ID: {webhook_info.event_id}, "
                    f"Previous Status: {idempotency_result.previous_status}"
                )
                
                # For completed duplicates, return the previous result
                if idempotency_result.previous_status == WebhookEventStatus.COMPLETED.value:
                    previous_result = {}
                    if idempotency_result.previous_result:
                        try:
                            previous_result = json.loads(idempotency_result.previous_result)
                        except Exception:
                            previous_result = {"status": "success", "message": "Previously processed"}
                    
                    return ProcessingResult(
                        success=True,
                        webhook_event_id=idempotency_result.webhook_event_id or 0,
                        result_data=previous_result
                    )
                else:
                    # For failed or processing duplicates, return appropriate response
                    return ProcessingResult(
                        success=False,
                        webhook_event_id=idempotency_result.webhook_event_id or 0,
                        error_message=f"Duplicate event in status: {idempotency_result.previous_status}"
                    )
            
            # Step 2: Record the new event (or use existing for retries)
            # If webhook_event_id is already set in idempotency_result, it's a retry - use that ID
            if idempotency_result.webhook_event_id:
                webhook_event_id = idempotency_result.webhook_event_id
                logger.info(
                    f"🔄 WEBHOOK_RETRY: Using existing event ID for retry - "
                    f"ID: {webhook_event_id}, "
                    f"Event ID: {webhook_info.event_id}"
                )
            else:
                webhook_event_id = cls.record_webhook_event(webhook_info)
            
            if not webhook_event_id:
                # Concurrent recording detected - this is now a duplicate
                logger.warning(
                    f"⚠️ WEBHOOK_CONCURRENT: Event recorded concurrently - "
                    f"Provider: {webhook_info.provider.value}, "
                    f"Event ID: {webhook_info.event_id}"
                )
                
                # Re-check idempotency after concurrent recording
                idempotency_result = cls.check_idempotency(webhook_info)
                if idempotency_result.is_duplicate:
                    return ProcessingResult(
                        success=True,
                        webhook_event_id=idempotency_result.webhook_event_id or 0,
                        result_data={"status": "success", "message": "Concurrently processed"}
                    )
                else:
                    return ProcessingResult(
                        success=False,
                        webhook_event_id=0,
                        error_message="Failed to record webhook event and no duplicate found"
                    )
            
            # Step 3: Execute the processing function
            logger.info(
                f"🚀 WEBHOOK_PROCESSING: Starting processing - "
                f"ID: {webhook_event_id}, "
                f"Provider: {webhook_info.provider.value}, "
                f"Event ID: {webhook_info.event_id}"
            )
            
            try:
                processing_result = await processing_function(webhook_info, *args, **kwargs)
                processing_duration = int((time.time() - start_time) * 1000)
                
                # Determine success based on result type
                if isinstance(processing_result, dict):
                    success = processing_result.get('status') in ['success', 'already_processed']
                    result_data = processing_result
                    error_msg = processing_result.get('error') or processing_result.get('message') if not success else None
                elif isinstance(processing_result, bool):
                    success = processing_result
                    result_data = {"status": "success" if success else "failed"}
                    error_msg = None if success else "Processing function returned False"
                else:
                    success = bool(processing_result)
                    result_data = {"status": "success" if success else "failed", "result": str(processing_result)}
                    error_msg = None if success else f"Unexpected result type: {type(processing_result)}"
                
                # Step 4: Update status based on processing result
                status = WebhookEventStatus.COMPLETED if success else WebhookEventStatus.FAILED
                
                cls.update_processing_status(
                    webhook_event_id=webhook_event_id,
                    status=status,
                    processing_duration_ms=processing_duration,
                    result_data=result_data,
                    error_message=error_msg
                )
                
                logger.info(
                    f"✅ WEBHOOK_COMPLETE: Processing finished - "
                    f"ID: {webhook_event_id}, "
                    f"Success: {success}, "
                    f"Duration: {processing_duration}ms"
                )
                
                return ProcessingResult(
                    success=success,
                    webhook_event_id=webhook_event_id,
                    processing_duration_ms=processing_duration,
                    result_data=result_data,
                    error_message=error_msg
                )
                
            except Exception as processing_error:
                safe_error = safe_error_log(processing_error)
                processing_duration = int((time.time() - start_time) * 1000)
                
                logger.error(
                    f"❌ WEBHOOK_PROCESSING_ERROR: Processing failed - "
                    f"ID: {webhook_event_id}, "
                    f"Provider: {webhook_info.provider.value}, "
                    f"Event ID: {webhook_info.event_id}. "
                    f"Error: {safe_error}"
                )
                
                # Update status to failed
                cls.update_processing_status(
                    webhook_event_id=webhook_event_id,
                    status=WebhookEventStatus.FAILED,
                    processing_duration_ms=processing_duration,
                    error_message=safe_error
                )
                
                return ProcessingResult(
                    success=False,
                    webhook_event_id=webhook_event_id,
                    processing_duration_ms=processing_duration,
                    error_message=safe_error
                )
                
        except Exception as e:
            safe_error = safe_error_log(e)
            processing_duration = int((time.time() - start_time) * 1000)
            
            logger.error(
                f"❌ WEBHOOK_SYSTEM_ERROR: System error in idempotency processing - "
                f"Provider: {webhook_info.provider.value}, "
                f"Event ID: {webhook_info.event_id}. "
                f"Error: {safe_error}"
            )
            
            # Try to update status if we have webhook_event_id
            if webhook_event_id:
                try:
                    cls.update_processing_status(
                        webhook_event_id=webhook_event_id,
                        status=WebhookEventStatus.FAILED,
                        processing_duration_ms=processing_duration,
                        error_message=safe_error
                    )
                except Exception:
                    pass  # Don't let status update errors mask the original error
            
            return ProcessingResult(
                success=False,
                webhook_event_id=webhook_event_id or 0,
                processing_duration_ms=processing_duration,
                error_message=safe_error
            )

    @staticmethod
    def get_webhook_event_history(
        provider: Optional[WebhookProvider] = None,
        event_id: Optional[str] = None,
        reference_id: Optional[str] = None,
        user_id: Optional[int] = None,
        status: Optional[WebhookEventStatus] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get webhook event history for audit and debugging purposes.
        
        Args:
            provider: Filter by webhook provider
            event_id: Filter by specific event ID
            reference_id: Filter by reference ID
            user_id: Filter by user ID
            status: Filter by processing status
            limit: Maximum number of records to return
            
        Returns:
            List of webhook event records
        """
        try:
            with atomic_transaction() as session:
                query = session.query(WebhookEventLedger)
                
                # Apply filters
                if provider:
                    query = query.filter(WebhookEventLedger.event_provider == provider.value)
                if event_id:
                    query = query.filter(WebhookEventLedger.event_id == event_id)
                if reference_id:
                    query = query.filter(WebhookEventLedger.reference_id == reference_id)
                if user_id:
                    query = query.filter(WebhookEventLedger.user_id == user_id)
                if status:
                    query = query.filter(WebhookEventLedger.status == status.value)
                
                # Order by most recent first and limit results
                events = (
                    query
                    .order_by(WebhookEventLedger.created_at.desc())
                    .limit(limit)
                    .all()
                )
                
                # Convert to dictionaries
                result = []
                for event in events:
                    result.append({
                        'id': event.id,
                        'event_provider': event.event_provider,
                        'event_id': event.event_id,
                        'txid': event.txid,
                        'reference_id': event.reference_id,
                        'status': event.status,
                        'amount': str(event.amount) if event.amount is not None else None,
                        'currency': event.currency,
                        'user_id': event.user_id,
                        'retry_count': event.retry_count,
                        'processing_duration_ms': event.processing_duration_ms,
                        'processed_at': event.processed_at.isoformat() if event.processed_at is not None else None,
                        'completed_at': event.completed_at.isoformat() if event.completed_at is not None else None,
                        'created_at': event.created_at.isoformat() if event.created_at is not None else None,
                        'error_message': event.error_message,
                        'has_result': bool(event.processing_result)
                    })
                
                return result
                
        except Exception as e:
            safe_error = safe_error_log(e)
            logger.error(f"❌ WEBHOOK_HISTORY: Failed to get event history: {safe_error}")
            return []

    @staticmethod
    def cleanup_old_events(days_to_keep: int = 90) -> int:
        """
        Clean up old webhook events to prevent database bloat.
        
        Args:
            days_to_keep: Number of days of events to retain
            
        Returns:
            Number of events cleaned up
        """
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days_to_keep)
            
            with atomic_transaction() as session:
                # Only delete completed or failed events older than cutoff
                deleted_count = (
                    session.query(WebhookEventLedger)
                    .filter(
                        and_(
                            WebhookEventLedger.created_at < cutoff_date,
                            or_(
                                WebhookEventLedger.status == WebhookEventStatus.COMPLETED.value,
                                WebhookEventLedger.status == WebhookEventStatus.FAILED.value
                            )
                        )
                    )
                    .delete()
                )
                
                if deleted_count > 0:
                    logger.info(f"🧹 WEBHOOK_CLEANUP: Cleaned up {deleted_count} old webhook events")
                
                return deleted_count
                
        except Exception as e:
            safe_error = safe_error_log(e)
            logger.error(f"❌ WEBHOOK_CLEANUP: Failed to clean up old events: {safe_error}")
            return 0


# Convenience instance for easy importing
webhook_idempotency_service = WebhookIdempotencyService()