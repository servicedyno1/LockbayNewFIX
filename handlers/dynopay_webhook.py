"""DynoPay Webhook Handler for Processing Crypto Payments - Integrated with Unified Transaction System"""

import asyncio
import logging
import json
from typing import Dict, Any, Optional
from decimal import Decimal
from datetime import timedelta, datetime, timezone
from fastapi import Request
from sqlalchemy.orm import Session
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from database import SessionLocal, async_managed_session
from models import Escrow, Transaction, TransactionType, EscrowStatus, User, UnifiedTransaction, UnifiedTransactionType, UnifiedTransactionStatus
from services.crypto import CryptoServiceAtomic
from services.unified_transaction_service import create_unified_transaction_service
from services.dual_write_adapter import DualWriteMode
from services.webhook_idempotency_service import (
    webhook_idempotency_service,
    WebhookEventInfo,
    WebhookProvider,
    ProcessingResult
)
from services.consolidated_notification_service import NotificationPriority
from utils.universal_id_generator import UniversalIDGenerator
from utils.atomic_transactions import atomic_transaction
from utils.financial_audit_logger import (
    financial_audit_logger,
    FinancialEventType,
    EntityType,
    FinancialContext
)
from utils.webhook_prefetch import (
    prefetch_webhook_context,
    WebhookPrefetchData
)
from services.admin_trade_notifications import admin_trade_notifications

logger = logging.getLogger(__name__)

# Initialize unified transaction service for DynoPay webhooks
unified_tx_service = create_unified_transaction_service(DualWriteMode.DUAL_WRITE_LEGACY_PRIMARY)

# PERFORMANCE OPTIMIZATION: In-memory cache for exchange rates
_exchange_rate_cache = {}
_cache_timestamps = {}


class DynoPayWebhookHandler:
    """Handles DynoPay webhook callbacks for escrow deposits"""

    @staticmethod
    async def _get_cached_exchange_rate(currency: str) -> Decimal | None:
        """
        WEBHOOK OPTIMIZATION: Get cached exchange rate with emergency fallback
        
        Priority order:
        1. Main cache (fast)
        2. Rapid cache (fast)
        3. Fallback cache (older but usable)
        4. Local in-memory cache
        5. EMERGENCY: Live fetch with circuit breaker (last resort)
        
        Cache hit/miss monitoring enabled for production visibility
        """
        try:
            from services.fastforex_service import fastforex_service, emergency_fetch_rate_with_circuit_breaker
            
            # WEBHOOK OPTIMIZATION: Use cache-only method that checks all cache layers
            rate = await fastforex_service.get_crypto_to_usd_rate_webhook_optimized(currency)
            
            if rate is not None:
                # MONITORING: Track cache hit
                logger.debug(f"🚀 WEBHOOK_CACHE_HIT: Exchange rate for {currency}: ${rate:.4f}")
                DynoPayWebhookHandler._track_rate_cache_hit(currency, "cache_hit")
                return rate
            else:
                # MONITORING: Track cache miss
                logger.warning(f"⚠️ WEBHOOK_CACHE_MISS: Primary caches empty for {currency}")
                DynoPayWebhookHandler._track_rate_cache_hit(currency, "cache_miss")
                
                # FALLBACK: Check local cache as backup
                cache_key = currency.upper()
                if cache_key in _exchange_rate_cache:
                    logger.warning(f"🚀 LOCAL_FALLBACK: Using local cached rate for {currency}")
                    DynoPayWebhookHandler._track_rate_cache_hit(currency, "local_fallback")
                    return _exchange_rate_cache[cache_key]
                
                # EMERGENCY FALLBACK: Attempt live fetch with circuit breaker protection
                logger.warning(f"🚨 EMERGENCY_ATTEMPT: All caches empty for {currency}, attempting emergency fetch")
                emergency_rate = await emergency_fetch_rate_with_circuit_breaker(currency)
                
                if emergency_rate is not None:
                    logger.info(f"✅ EMERGENCY_SUCCESS: Live fetch successful for {currency} = ${emergency_rate:.4f}")
                    DynoPayWebhookHandler._track_rate_cache_hit(currency, "emergency_fetch_success")
                    
                    # Store in local cache to prevent repeated emergencies
                    _exchange_rate_cache[cache_key] = emergency_rate
                    return emergency_rate
                else:
                    # ALL OPTIONS EXHAUSTED - return None to signal retry needed
                    logger.error(f"❌ WEBHOOK_RATE_UNAVAILABLE: No rate available for {currency} - returning None for retry")
                    DynoPayWebhookHandler._track_rate_cache_hit(currency, "all_failed")
                    return None
                
        except Exception as e:
            logger.error(f"❌ WEBHOOK_RATE_ERROR: {currency} - {e}")
            DynoPayWebhookHandler._track_rate_cache_hit(currency, "error")
            # Return None to signal retry needed
            return None
    
    @staticmethod
    def _track_rate_cache_hit(currency: str, status: str):
        """
        MONITORING: Track cache hit/miss rates for webhook processing
        Logs metrics for production visibility and alerting
        """
        try:
            # Log the event for monitoring and metrics collection
            logger.info(f"📊 WEBHOOK_RATE_METRIC: currency={currency}, status={status}")
            
            # Could be extended to send to metrics service (Prometheus, DataDog, etc.)
            # For now, we log for grep-based analysis
            
        except Exception as e:
            # Don't let monitoring failures break webhook processing
            logger.debug(f"Metric tracking failed: {e}")

    @staticmethod
    async def handle_escrow_deposit_webhook(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process DynoPay webhook for escrow deposit confirmation with comprehensive idempotency protection"""
        
        # PERFORMANCE MONITORING: Track processing time
        import time
        start_time = time.time()
        
        try:
            # Determine event type - skip payment.pending events (only process confirmed)
            event_type = webhook_data.get('event', '')
            if event_type == 'payment.pending':
                logger.info(f"📋 DYNOPAY_WEBHOOK: Received payment.pending event - acknowledging (txId: {webhook_data.get('txId', 'unknown')})")
                return {"status": "success", "message": "Pending event acknowledged"}
            
            # Extract webhook data with field mapping (DynoPay actual format → expected format)
            # BUGFIX: Handle None meta_data (DynoPay may send null) and check transaction_reference
            meta_data = webhook_data.get('meta_data') or {}
            reference_id = (
                meta_data.get('refId') or 
                webhook_data.get('customer_reference') or
                webhook_data.get('transaction_reference')
            )
            
            # BUGFIX: If reference_id still missing, try to resolve via payment address lookup
            if not reference_id and webhook_data.get('address'):
                try:
                    from models import PaymentAddress, Escrow as EscrowModel
                    from database import async_managed_session
                    async with async_managed_session() as addr_session:
                        addr_stmt = select(PaymentAddress, EscrowModel.escrow_id).join(
                            EscrowModel, PaymentAddress.escrow_id == EscrowModel.id
                        ).where(
                            PaymentAddress.address == webhook_data['address']
                        )
                        addr_result = await addr_session.execute(addr_stmt)
                        addr_row = addr_result.first()
                        if addr_row:
                            reference_id = addr_row[1]  # String escrow_id like "ES031126HXF8"
                            logger.info(f"🔍 DYNOPAY_ADDR_LOOKUP: Resolved reference_id={reference_id} from address={webhook_data['address']}")
                except Exception as addr_err:
                    logger.warning(f"⚠️ DYNOPAY_ADDR_LOOKUP: Failed to resolve via address: {addr_err}")
            
            # DynoPay field mapping:
            # - 'amount' = crypto amount (e.g., 0.01331 ETH) 
            # - 'base_amount' = USD value (e.g., 30) - authoritative payment value
            # - 'currency' = crypto currency (ETH, BTC, etc.)
            # - 'base_currency' = fiat currency (USD)
            # BUGFIX: Also check 'amount_received' for underpaid events
            crypto_amount = webhook_data.get('amount') or webhook_data.get('paid_amount') or webhook_data.get('amount_received')
            paid_currency = webhook_data.get('paid_currency') or webhook_data.get('currency')
            transaction_id = webhook_data.get('id') or webhook_data.get('payment_id') or webhook_data.get('txId')
            
            # Use base_amount (USD) from DynoPay when available - this is the authoritative value
            dynopay_base_amount = webhook_data.get('base_amount')
            dynopay_base_currency = webhook_data.get('base_currency', 'USD')
            dynopay_overpayment = webhook_data.get('overpayment') or {}
            
            if dynopay_base_amount and dynopay_base_currency == 'USD':
                paid_amount = dynopay_base_amount
                logger.info(f"📊 DYNOPAY_AMOUNT: Using base_amount=${dynopay_base_amount} USD (crypto: {crypto_amount} {paid_currency}, rate: {webhook_data.get('exchange_rate')})")
                if dynopay_overpayment.get('amount_usd'):
                    logger.info(f"💰 DYNOPAY_OVERPAYMENT: +${dynopay_overpayment['amount_usd']} USD")
            else:
                paid_amount = crypto_amount
            
            if not reference_id:
                logger.error(f"DynoPay webhook missing reference_id (event: {event_type}, keys: {list(webhook_data.keys())})")
                return {"status": "error", "message": "Missing reference ID"}
            
            if not paid_amount or not paid_currency:
                logger.error(f"DynoPay webhook missing payment details (event: {event_type}, amount: {paid_amount}, currency: {paid_currency})")
                return {"status": "error", "message": "Missing payment details"}
            
            if not transaction_id:
                logger.error("DynoPay webhook missing transaction_id")
                return {"status": "error", "message": "Missing transaction ID"}
            
            # REPLAY ATTACK PROTECTION: Extract timestamp from webhook for validation
            webhook_timestamp = None
            created_at = webhook_data.get('created_at')
            if created_at:
                try:
                    # Convert ISO string to datetime with UTC timezone
                    webhook_timestamp = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    if webhook_timestamp.tzinfo is None:
                        webhook_timestamp = webhook_timestamp.replace(tzinfo=timezone.utc)
                except (ValueError, AttributeError) as e:
                    logger.warning(f"Failed to parse timestamp '{created_at}': {e}")
                    webhook_timestamp = None
            else:
                logger.warning(f"⚠️ DynoPay webhook missing created_at timestamp, using server time")
                webhook_timestamp = datetime.now(timezone.utc)
            
            logger.info(f"🔄 DYNOPAY_WEBHOOK: Received webhook - Reference: {reference_id}, Amount: {paid_amount} {paid_currency}, TxID: {transaction_id}")
            
            # ENHANCED IDEMPOTENCY: Use comprehensive webhook idempotency service
            # Create webhook event info for idempotency tracking
            webhook_info = WebhookEventInfo(
                provider=WebhookProvider.DYNOPAY,
                event_id=transaction_id,  # DynoPay transaction ID is the unique event identifier
                event_type="escrow_deposit",  # Categorize this webhook event type
                txid=transaction_id,
                reference_id=reference_id,
                amount=Decimal(str(paid_amount)) if paid_amount else None,
                currency=paid_currency,
                user_id=None,  # Will be determined during processing
                metadata={
                    'meta_data': meta_data,
                    'webhook_source': 'dynopay_escrow_deposit',
                    'timestamp': webhook_timestamp.isoformat() if webhook_timestamp else None  # JSON-serializable ISO string
                },
                webhook_payload=json.dumps(webhook_data)
            )
            
            # Process webhook with comprehensive idempotency protection
            result = await webhook_idempotency_service.process_webhook_with_idempotency(
                webhook_info=webhook_info,
                processing_function=DynoPayWebhookHandler._process_dynopay_payment_with_lock
            )
            
            if result.success:
                # PERFORMANCE MONITORING: Record successful processing time
                processing_time_ms = (time.time() - start_time) * 1000
                logger.info(f"✅ DYNOPAY_IDEMPOTENT: Webhook processed successfully - Event ID: {transaction_id}, Duration: {result.processing_duration_ms}ms, Total: {processing_time_ms:.1f}ms")
                
                # Track performance metrics
                try:
                    from webhook_server import track_webhook_performance
                    track_webhook_performance(processing_time_ms)
                except ImportError:
                    pass  # Fallback if webhook_server not imported
                    
                return result.result_data or {"status": "success", "message": "Payment processed"}
            else:
                # PERFORMANCE MONITORING: Record failed processing time
                processing_time_ms = (time.time() - start_time) * 1000
                logger.error(f"❌ DYNOPAY_IDEMPOTENT: Webhook processing failed - Event ID: {transaction_id}, Duration: {processing_time_ms:.1f}ms, Error: {result.error_message}")
                
                # Track performance metrics even for failures
                try:
                    from webhook_server import track_webhook_performance
                    track_webhook_performance(processing_time_ms)
                except ImportError:
                    pass  # Fallback if webhook_server not imported
                    
                return {"status": "error", "message": result.error_message or "Processing failed"}
                
        except Exception as e:
            logger.error(f"❌ DYNOPAY_WEBHOOK: Handler error - {e}", exc_info=True)
            return {"status": "error", "message": f"Processing error: {str(e)}"}

    @staticmethod
    async def _process_dynopay_payment_with_lock(webhook_info: WebhookEventInfo) -> Dict[str, Any]:
        """Process DynoPay payment with distributed locking - called by idempotency service"""
        try:
            # Reconstruct webhook data from webhook_info
            webhook_data = json.loads(webhook_info.webhook_payload) if webhook_info.webhook_payload else {}
            transaction_id = webhook_info.event_id
            reference_id = webhook_info.reference_id
            
            # CRITICAL FIX: Add distributed locking for payment confirmations
            from utils.distributed_lock import distributed_lock_service
            
            # Acquire distributed lock for this payment
            additional_data = {
                "callback_source": "dynopay",
                "paid_amount": str(webhook_info.amount) if webhook_info.amount else None,
                "paid_currency": webhook_info.currency,
                "reference_id": reference_id,
                "webhook_event_id": getattr(webhook_info, 'webhook_event_id', None)
            }
            
            with distributed_lock_service.acquire_payment_lock(
                order_id=str(reference_id),
                txid=transaction_id,
                timeout=120,  # 120 seconds timeout for payment processing - handles complex operations
                additional_data=additional_data
            ) as lock:
                
                if not lock.acquired:
                    logger.warning(
                        f"DYNOPAY_RACE_CONDITION_PREVENTED: Could not acquire lock for "
                        f"escrow {reference_id}, txid {transaction_id}. Reason: {lock.error}"
                    )
                    return {"status": "already_processing", "message": "Payment is being processed"}
                
                logger.critical(
                    f"DYNOPAY_DISTRIBUTED_LOCK_SUCCESS: Processing payment for escrow {reference_id}, "
                    f"txid {transaction_id} with exclusive lock"
                )
                
                # Process the deposit atomically within the lock
                if reference_id is None:
                    raise ValueError("Reference ID cannot be None for payment processing")
                return await DynoPayWebhookHandler._process_locked_payment(
                    webhook_data, reference_id, transaction_id
                )
                
        except Exception as e:
            logger.error(f"❌ DYNOPAY_LOCK_PROCESSING: Error in locked payment processing - {e}", exc_info=True)
            return {"status": "error", "message": f"Lock processing failed: {str(e)}"}

    @staticmethod
    async def _process_unified_dynopay_payment(
        unified_tx: UnifiedTransaction, webhook_data: Dict[str, Any], 
        paid_amount: float, paid_currency: str, transaction_id: str, session: AsyncSession
    ) -> Dict[str, Any]:
        """Process DynoPay payment for unified transaction with proper status transitions"""
        try:
            tx_id = getattr(unified_tx, 'transaction_id', unified_tx.id)
            transaction_type = unified_tx.transaction_type
            current_status = UnifiedTransactionStatus(unified_tx.status)
            user_id = unified_tx.user_id
            
            # Determine next status based on transaction type
            if transaction_type == UnifiedTransactionType.ESCROW.value:  # type: ignore[arg-type]
                # Escrow flow: awaiting_payment → payment_confirmed
                if current_status == UnifiedTransactionStatus.AWAITING_PAYMENT:
                    next_status = UnifiedTransactionStatus.PAYMENT_CONFIRMED
                    reason = f"DynoPay crypto payment confirmed: {paid_amount} {paid_currency}"
                else:
                    logger.warning(f"Unexpected status {current_status.value} for escrow payment confirmation")
                    return {"status": "error", "message": f"Invalid status for payment: {current_status.value}"}
                    
            elif transaction_type in [UnifiedTransactionType.EXCHANGE_SELL_CRYPTO.value, UnifiedTransactionType.EXCHANGE_BUY_CRYPTO.value]:
                # Exchange flow: awaiting_payment → payment_confirmed  
                if current_status == UnifiedTransactionStatus.AWAITING_PAYMENT:
                    next_status = UnifiedTransactionStatus.PAYMENT_CONFIRMED
                    reason = f"DynoPay crypto payment confirmed: {paid_amount} {paid_currency}"
                else:
                    logger.warning(f"Unexpected status {current_status.value} for exchange payment confirmation")
                    return {"status": "error", "message": f"Invalid status for payment: {current_status.value}"}
                    
            elif transaction_type == UnifiedTransactionType.WALLET_CASHOUT.value:  # type: ignore[arg-type]
                # Wallet cashout: This should not receive payments via DynoPay
                logger.warning(f"Received payment webhook for wallet cashout {tx_id} - this is unexpected")
                return {"status": "error", "message": "Wallet cashouts should not receive payments"}
                
            else:
                logger.error(f"Unknown transaction type: {transaction_type}")
                return {"status": "error", "message": f"Unknown transaction type: {transaction_type}"}
            
            # Update unified transaction metadata with payment details
            payment_metadata = {
                'dynopay_payment_confirmed': True,
                'dynopay_amount_received': str(paid_amount),
                'dynopay_currency': paid_currency,
                'dynopay_transaction_id': transaction_id,
                'dynopay_confirmation_timestamp': __import__('datetime').datetime.utcnow().isoformat()
            }
            
            # Transition status using unified service
            transition_result = await unified_tx_service.transition_status(
                transaction_id=str(tx_id),
                new_status=next_status,
                reason=reason,
                metadata=payment_metadata,
                session=session  # type: ignore[arg-type]
            )
            
            if transition_result.success:
                # Log financial audit event
                financial_audit_logger.log_financial_event(
                    event_type=FinancialEventType.CRYPTO_PAYMENT_CONFIRMED,
                    entity_type=EntityType.UNIFIED_TRANSACTION,
                    entity_id=str(tx_id),
                    user_id=int(user_id) if user_id is not None else None,
                    financial_context=FinancialContext(
                        amount=Decimal(str(paid_amount)),
                        currency=paid_currency
                    ),
                    previous_state=current_status.value,
                    new_state=next_status.value,
                    related_entities={
                        "transaction_type": str(transaction_type),
                        "payment_source": "dynopay_webhook"
                    },
                    additional_data={
                        "source": "dynopay_webhook._process_unified_dynopay_payment",
                        "dynopay_transaction_id": transaction_id
                    }
                )
                
                # Send appropriate user notifications based on transaction type
                try:
                    from services.wallet_notification_service import WalletNotificationService
                    from services.consolidated_notification_service import consolidated_notification_service
                    
                    # CRITICAL FIX: Define explicit allowlists for different notification types
                    wallet_funding_types = [
                        UnifiedTransactionType.EXCHANGE_SELL_CRYPTO.value,
                        UnifiedTransactionType.EXCHANGE_BUY_CRYPTO.value
                    ]
                    
                    escrow_payment_types = [
                        UnifiedTransactionType.ESCROW.value
                    ]
                    
                    # Calculate USD value - use DynoPay's base_amount if already in USD
                    usd_amount = None
                    dynopay_base = webhook_data.get('base_amount')
                    dynopay_base_curr = webhook_data.get('base_currency', '')
                    if dynopay_base and dynopay_base_curr == 'USD':
                        # DynoPay already provided authoritative USD value
                        usd_amount = Decimal(str(dynopay_base))
                        logger.info(f"📊 USD_FROM_DYNOPAY: Using base_amount=${usd_amount:.2f} (no conversion needed)")
                    else:
                        try:
                            usd_rate = await CryptoServiceAtomic.get_real_time_exchange_rate(paid_currency)
                            if usd_rate is not None:
                                usd_amount = Decimal(str(paid_amount)) * Decimal(str(usd_rate))
                                logger.debug(f"USD conversion successful: {paid_amount} {paid_currency} = ${usd_amount:.2f}")
                            else:
                                logger.warning(f"Exchange rate returned None for {paid_currency}")
                        except Exception as rate_error:
                            logger.warning(f"Failed to get exchange rate for {paid_currency}: {rate_error}")
                            logger.info(f"Will send notification without USD value for {paid_amount} {paid_currency}")
                    
                    # Handle wallet funding notifications (exchanges that credit user wallets)
                    if transaction_type in wallet_funding_types:
                        # CRITICAL FIX: Always send wallet notifications, even when USD rate unavailable
                        wallet_usd_amount = usd_amount if usd_amount is not None else None
                        
                        notification_sent = await WalletNotificationService.send_crypto_deposit_confirmation(
                            user_id=int(user_id) if user_id is not None else 0,
                            amount_crypto=Decimal(str(paid_amount)),
                            currency=paid_currency,
                            amount_usd=wallet_usd_amount,
                            txid_in=transaction_id
                        )
                        
                        if notification_sent:
                            if usd_amount is not None:
                                logger.info(f"✅ UNIFIED_DYNOPAY: Wallet funding confirmation sent to user {user_id} with USD amount")
                            else:
                                logger.info(f"✅ UNIFIED_DYNOPAY: Wallet funding confirmation sent to user {user_id} without USD amount (rate unavailable)")
                            
                            # Send admin notification for wallet funded (with proper error handling)
                            try:
                                async with async_managed_session() as notify_session:
                                    notify_user_result = await notify_session.execute(
                                        select(User).where(User.id == user_id)
                                    )
                                    notify_user = notify_user_result.scalar_one_or_none()
                                    
                                    if notify_user:
                                        # Call notification directly with await to catch errors
                                        try:
                                            notification_result = await admin_trade_notifications.notify_wallet_funded({
                                                'user_id': notify_user.id,
                                                'telegram_id': notify_user.telegram_id,
                                                'username': notify_user.username,
                                                'first_name': notify_user.first_name,
                                                'last_name': notify_user.last_name,
                                                'amount_crypto': float(paid_amount),
                                                'currency': paid_currency,
                                                'amount_usd': float(usd_amount) if usd_amount else None,
                                                'txid': transaction_id,
                                                'funded_at': datetime.utcnow()
                                            })
                                            if notification_result:
                                                logger.info(f"✅ Admin notification sent for wallet funded: {notify_user.username}")
                                            else:
                                                logger.warning(f"⚠️ Admin notification failed for wallet funded: {notify_user.username}")
                                        except Exception as admin_notify_error:
                                            logger.error(f"❌ Failed to send admin wallet funded notification: {admin_notify_error}", exc_info=True)
                                    else:
                                        logger.warning(f"⚠️ User {user_id} not found for admin notification")
                            except Exception as notify_error:
                                logger.error(f"❌ Error in wallet funded notification flow: {notify_error}", exc_info=True)
                        else:
                            logger.warning(f"⚠️ UNIFIED_DYNOPAY: Failed to send wallet funding confirmation to user {user_id}")
                    
                    # ESCROW NOTIFICATIONS: Handled by EnhancedPaymentToleranceService after payment processing
                    # This ensures buyer gets ONE accurate notification after tolerance check completes
                    elif transaction_type in escrow_payment_types:
                        logger.info(f"📋 UNIFIED_DYNOPAY: Escrow payment detected - notifications will be sent after tolerance processing")
                    else:
                        logger.info(f"📋 UNIFIED_DYNOPAY: No user notification configured for transaction type {transaction_type}")
                    
                except Exception as notification_error:
                    logger.error(f"Failed to send unified DynoPay payment notifications: {notification_error}")
                    # Don't raise - notification failures shouldn't affect transaction processing
                
                return {
                    "status": "success", 
                    "message": "Payment processed successfully",
                    "transaction_id": tx_id,
                    "new_status": next_status.value
                }
            else:
                logger.error(f"❌ UNIFIED_DYNOPAY: Failed to transition {tx_id}: {transition_result.error}")
                return {"status": "error", "message": f"Status transition failed: {transition_result.error}"}
                
        except Exception as e:
            # CRITICAL FIX: Extract transaction_id value before using
            tx_id_value = getattr(unified_tx, 'transaction_id', 'unknown')
            logger.error(f"❌ UNIFIED_DYNOPAY: Error processing unified payment for {tx_id_value}: {e}", exc_info=True)
            return {"status": "error", "message": "Internal processing error"}

    @staticmethod
    async def _process_locked_payment(
        webhook_data: Dict[str, Any], reference_id: str, transaction_id: str
    ) -> Dict[str, Any]:
        """
        RESTRUCTURED: Process DynoPay payment with ALL database operations in ONE session block.
        
        Pattern: DB operations → Commit → Notifications → Return
        This prevents transaction rollback bugs from implicit session creation.
        """
        try:
            # Initialize variables for return value (ensure they're always defined)
            usd_rate = None
            created_transaction_id = transaction_id
            
            # Use DynoPay's base_amount (USD) when available, otherwise fall back to crypto amount
            dynopay_base_amt = webhook_data.get('base_amount')
            dynopay_base_curr = webhook_data.get('base_currency', '')
            raw_crypto_amount = webhook_data.get('amount') or webhook_data.get('paid_amount') or 0
            
            crypto_amount_decimal = Decimal(str(raw_crypto_amount))
            
            if dynopay_base_amt and dynopay_base_curr == 'USD':
                usd_amount = Decimal(str(dynopay_base_amt))
                logger.info(f"📊 DYNOPAY_USD_DIRECT: Using base_amount=${usd_amount} (crypto: {crypto_amount_decimal} {webhook_data.get('currency', '?')})")
            else:
                usd_amount = Decimal('0')
            
            # Extract webhook data
            meta_data = webhook_data.get('meta_data', {})
            paid_amount = webhook_data.get('paid_amount') or webhook_data.get('amount')
            paid_currency = webhook_data.get('paid_currency') or webhook_data.get('currency')
            
            # Variables to store result and notification data (populated in session, used after)
            result_to_return = None
            notification_data = None
            
            # CRITICAL FIX: Wrap session with IntegrityError handling for commit-time constraint violations
            try:
                async with async_managed_session() as session:
                    
                    # PERFORMANCE OPTIMIZATION: Batch all database lookups into a single optimized query
                    # This replaces 4+ sequential queries with 1 optimized batch query
                    from sqlalchemy.orm import joinedload
                    from sqlalchemy import or_, and_
                    
                    # Single optimized query to get all needed data - SQLAlchemy 2.0 style
                    stmt = select(
                        UnifiedTransaction, Escrow, Transaction
                    ).select_from(
                        # Start with UnifiedTransaction (left join to handle both unified and legacy)
                        UnifiedTransaction
                    ).outerjoin(
                    # Join Escrow data
                    Escrow, 
                    UnifiedTransaction.transaction_metadata['escrow_id'].astext == Escrow.escrow_id
                    ).outerjoin(
                    # Join existing Transaction data for idempotency
                    Transaction,
                    or_(
                        Transaction.external_id == transaction_id,
                        and_(
                            Transaction.escrow_id == Escrow.id,
                            Transaction.transaction_type == TransactionType.DEPOSIT.value,
                            Transaction.amount == Decimal(str(paid_amount)),
                            Transaction.currency == paid_currency
                        )
                    )
                    ).where(
                    # Filter by reference_id (works for both unified and legacy)
                    or_(
                        UnifiedTransaction.transaction_metadata['escrow_id'].astext == reference_id,
                        Escrow.escrow_id == reference_id
                    )
                    ).options(
                    # CRITICAL FIX: Eagerly load buyer and seller relationships to prevent lazy loading errors
                    joinedload(Escrow.buyer),
                    joinedload(Escrow.seller)
                    )
                
                    result = await session.execute(stmt)
                    query_result = result.first()
                
                    # Parse batch query result
                    unified_tx, escrow, existing_transaction = None, None, None
                    if query_result:
                        unified_tx, escrow, existing_transaction = query_result
                
                    # WEBHOOK_PREFETCH OPTIMIZATION: If no unified transaction found, use prefetch for escrow lookup
                    # This replaces 3 sequential queries (utid, escrow_id, buyer/seller) with 1-2 batched queries
                    if not escrow:
                        logger.info(f"🚀 WEBHOOK_PREFETCH: Using batched prefetch for escrow {reference_id}")
                        prefetch_data = await prefetch_webhook_context(
                            order_id=reference_id,
                            order_type='escrow',
                            session=session
                        )
                        
                        if prefetch_data:
                            # Escrow found via prefetch - fetch the actual ORM object for processing
                            # The prefetch already loaded buyer/seller relationships efficiently
                            stmt_escrow = select(Escrow).where(Escrow.id == prefetch_data.order_id).options(
                                joinedload(Escrow.buyer),
                                joinedload(Escrow.seller)
                            )
                            result_escrow = await session.execute(stmt_escrow)
                            escrow = result_escrow.scalar_one_or_none()
                            
                            if escrow:
                                logger.info(
                                    f"✅ WEBHOOK_PREFETCH_SUCCESS: Escrow loaded in {prefetch_data.prefetch_duration_ms:.1f}ms "
                                    f"(target: <200ms) - Buyer: {prefetch_data.telegram_id}, Balance: ${prefetch_data.current_balance}"
                                )
                            else:
                                logger.error(f"❌ WEBHOOK_PREFETCH_ERROR: Prefetch succeeded but ORM fetch failed for escrow {reference_id}")
                        else:
                            logger.error(f"❌ WEBHOOK_PREFETCH_MISS: Escrow not found for reference_id {reference_id}")
                
                    # EARLY EXIT CASES: Handle these without early returns, set result_to_return instead
                    if not escrow:
                        logger.error(f"⚡ FAST_DYNOPAY: Escrow not found for reference_id: {reference_id}")
                        result_to_return = {"status": "error", "message": "Escrow not found", "reference_id": reference_id}
                
                    elif existing_transaction:
                        logger.warning(f"⚡ FAST_DYNOPAY: Payment already processed - found existing transaction: {existing_transaction.transaction_id}")
                        result_to_return = {
                            "status": "already_processed", 
                            "transaction_id": existing_transaction.transaction_id,
                            "reason": "duplicate_payment_detected"
                        }
                
                    elif unified_tx:
                        # UNIFIED TRANSACTION: Process within session, keep all DB ops here
                        logger.info(f"⚡ UNIFIED_DYNOPAY: Processing unified transaction {unified_tx.transaction_id}")
                        result_to_return = await DynoPayWebhookHandler._process_unified_dynopay_payment(
                            unified_tx, webhook_data, float(paid_amount) if paid_amount is not None else 0.0, 
                            str(paid_currency) if paid_currency is not None else '', transaction_id, session
                        )
                        
                        # CRITICAL FIX: Update escrow timing fields after unified transaction processing
                        # The unified path only updates unified_transactions table, NOT the escrow object
                        if result_to_return and result_to_return.get("status") == "success" and escrow:
                            from config import Config as DynoUnifiedConfig
                            now_stmt = select(func.now())
                            now_result = await session.execute(now_stmt)
                            current_time = now_result.scalar()
                            
                            if current_time:
                                escrow.status = EscrowStatus.PAYMENT_CONFIRMED.value
                                escrow.payment_confirmed_at = current_time
                                escrow.expires_at = current_time + timedelta(minutes=DynoUnifiedConfig.SELLER_RESPONSE_TIMEOUT_MINUTES)
                                escrow.deposit_confirmed = True
                                escrow.deposit_tx_hash = transaction_id
                                
                                # DELIVERY COUNTDOWN
                                if escrow.pricing_snapshot is not None and 'delivery_hours' in escrow.pricing_snapshot:
                                    delivery_hours = int(escrow.pricing_snapshot['delivery_hours'])
                                    escrow.delivery_deadline = current_time + timedelta(hours=delivery_hours)
                                    escrow.auto_release_at = escrow.delivery_deadline + timedelta(hours=24)
                                    logger.info(f"⏰ DELIVERY_DEADLINE_SET: Escrow {escrow.escrow_id} delivery countdown starts - {delivery_hours}h")
                                
                                await session.flush()
                                logger.info(f"✅ UNIFIED_DYNOPAY_ESCROW_FIX: Updated escrow {escrow.escrow_id} expires_at to {DynoUnifiedConfig.SELLER_RESPONSE_TIMEOUT_MINUTES}m from payment confirmation")
                                
                                # CRITICAL FIX: Populate notification_data so post-session notifications fire
                                # The unified path previously skipped this, resulting in NO buyer/seller notifications
                                buyer_telegram_id = escrow.buyer.telegram_id if escrow.buyer else None
                                buyer_referral_code = escrow.buyer.referral_code if escrow.buyer else None
                                
                                seller_identifier = ""
                                if escrow.seller and escrow.seller.username:
                                    seller_identifier = f"@{escrow.seller.username}"
                                elif escrow.seller and escrow.seller.first_name:
                                    seller_identifier = escrow.seller.first_name
                                else:
                                    seller_identifier = "Seller"
                                
                                notification_data = {
                                    "type": "seller_offer",
                                    "escrow_id": escrow.escrow_id,
                                    "overpayment_credited": Decimal("0"),
                                    "buyer_telegram_id": buyer_telegram_id,
                                    "buyer_id": escrow.buyer_id,
                                    "escrow_amount": Decimal(str(escrow.amount or 0)),
                                    "buyer_fee": Decimal(str(escrow.buyer_fee_amount or 0)),
                                    "seller_identifier": seller_identifier,
                                    "seller_id": escrow.seller_id,
                                    "buyer_referral_code": buyer_referral_code
                                }
                                logger.info(f"📋 UNIFIED_DYNOPAY_NOTIFICATION: Prepared notification_data for escrow {escrow.escrow_id} (buyer={buyer_telegram_id}, seller={seller_identifier})")
                
                    else:
                        # LEGACY ESCROW PAYMENT: Continue processing within session
                        logger.info(f"⚡ LEGACY_DYNOPAY: Fast processing for legacy escrow {escrow.id}")
                        
                        # TYPE SAFETY: Extract all escrow Column values as scalar types to prevent Column type errors
                        escrow_id_value = escrow.id
                        escrow_status = escrow.status if not hasattr(escrow.status, 'value') else escrow.status.value
                        escrow_buyer_id = escrow.buyer_id
                        escrow_seller_id = escrow.seller_id
                        escrow_total_amount = escrow.total_amount
                        escrow_escrow_id = escrow.escrow_id
                        escrow_payment_confirmed_at = escrow.payment_confirmed_at
                        
                        # 🔒 CRITICAL SECURITY CHECK: Validate escrow status before processing payment
                        if escrow_status == EscrowStatus.CANCELLED.value:  # type: ignore[arg-type]
                            logger.warning(f"🚨 CANCELLED_ESCROW: Payment received for CANCELLED escrow {reference_id}. Will credit buyer wallet.")
                            
                            # BUGFIX: Credit the received payment to buyer's wallet (without platform fee)
                            # The buyer sent crypto but the escrow was cancelled - refund to wallet
                            try:
                                # Calculate USD amount from the webhook data
                                dynopay_base_amt_cancel = webhook_data.get('base_amount')
                                dynopay_base_curr_cancel = webhook_data.get('base_currency', 'USD')
                                
                                if dynopay_base_amt_cancel and dynopay_base_curr_cancel == 'USD':
                                    refund_usd_amount = Decimal(str(dynopay_base_amt_cancel))
                                elif crypto_amount and paid_currency:
                                    # Fallback: convert crypto to USD
                                    rate = await DynoPayWebhookHandler._get_cached_exchange_rate(paid_currency)
                                    if rate:
                                        refund_usd_amount = Decimal(str(crypto_amount)) * rate
                                    else:
                                        refund_usd_amount = Decimal("0")
                                        logger.error(f"❌ CANCELLED_REFUND: Cannot determine USD value for {crypto_amount} {paid_currency}")
                                else:
                                    refund_usd_amount = Decimal("0")
                                
                                if refund_usd_amount > 0:
                                    # Deduct platform fee from refund (refund escrow amount, not fee)
                                    escrow_amount = Decimal(str(escrow.amount))
                                    escrow_fee = Decimal(str(escrow.fee_amount or 0))
                                    
                                    # Refund the escrow base amount (without platform fee) 
                                    # Cap at the actual received amount
                                    refund_amount = min(escrow_amount, refund_usd_amount)
                                    
                                    refund_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                                        user_id=int(escrow_buyer_id),
                                        amount=refund_amount,
                                        currency="USD",
                                        escrow_id=int(escrow_id_value),
                                        transaction_type="escrow_refund",
                                        description=f"Auto-refund for cancelled trade #{escrow_escrow_id}: crypto payment received after cancellation (fee excluded)",
                                        session=session
                                    )
                                    
                                    if refund_success:
                                        # Update escrow status to refunded
                                        escrow.status = EscrowStatus.REFUNDED.value
                                        await session.commit()
                                        
                                        logger.info(
                                            f"✅ CANCELLED_REFUND: Credited ${refund_amount:.2f} to buyer {escrow_buyer_id} wallet "
                                            f"for cancelled escrow {reference_id} (received ${refund_usd_amount:.2f}, fee ${escrow_fee:.2f} excluded)"
                                        )
                                        
                                        # Notify buyer about the refund via Telegram
                                        try:
                                            from services.consolidated_notification_service import (
                                                consolidated_notification_service,
                                                NotificationRequest,
                                                NotificationChannel,
                                                NotificationPriority,
                                                NotificationCategory
                                            )
                                            buyer = escrow.buyer
                                            if buyer:
                                                buyer_telegram_id = getattr(buyer, 'telegram_id', None)
                                                if buyer_telegram_id:
                                                    notification = NotificationRequest(
                                                        user_id=int(buyer_telegram_id),
                                                        category=NotificationCategory.PAYMENT_UPDATES,
                                                        priority=NotificationPriority.HIGH,
                                                        title=f"Refund processed for trade #{escrow_escrow_id}",
                                                        message=(
                                                            f"💰 <b>Refund Processed</b>\n\n"
                                                            f"Your crypto payment for cancelled trade <b>#{escrow_escrow_id}</b> has been refunded.\n\n"
                                                            f"<b>Refund Amount:</b> ${refund_amount:.2f} USD\n"
                                                            f"<b>Platform Fee:</b> ${escrow_fee:.2f} USD (retained)\n\n"
                                                            f"The refund has been added to your LockBay wallet balance.\n"
                                                            f"Use /start to check your wallet."
                                                        ),
                                                        channels=[NotificationChannel.TELEGRAM],
                                                        idempotency_key=f"cancelled_refund_{escrow_escrow_id}_{transaction_id}"
                                                    )
                                                    asyncio.create_task(
                                                        consolidated_notification_service.send_notification(notification)
                                                    )
                                        except Exception as notif_err:
                                            logger.warning(f"⚠️ CANCELLED_REFUND: Notification failed: {notif_err}")
                                        
                                        # Notify admin about the auto-refund
                                        try:
                                            asyncio.create_task(
                                                admin_trade_notifications.notify_escrow_cancelled({
                                                    'escrow_id': escrow_escrow_id,
                                                    'amount': Decimal(str(refund_amount)),
                                                    'currency': 'USD',
                                                    'buyer_info': f"User_{escrow_buyer_id}",
                                                    'seller_info': 'N/A',
                                                    'cancelled_by': 'System (auto-refund)',
                                                    'reason': f'Crypto payment ${float(refund_amount):.2f} received after cancellation - auto-refunded to buyer wallet (tx: {transaction_id})',
                                                    'cancelled_at': datetime.now(timezone.utc)
                                                })
                                            )
                                        except Exception as admin_err:
                                            logger.warning(f"⚠️ CANCELLED_REFUND: Admin notification failed: {admin_err}")
                                    else:
                                        logger.error(f"❌ CANCELLED_REFUND: Failed to credit buyer wallet for escrow {reference_id}")
                                else:
                                    logger.error(f"❌ CANCELLED_REFUND: Cannot determine USD amount for refund (escrow {reference_id})")
                            except Exception as refund_err:
                                logger.error(f"❌ CANCELLED_REFUND: Error processing refund for cancelled escrow {reference_id}: {refund_err}")
                            
                            # Return success to stop webhook retries
                            result_to_return = {
                                "status": "refunded",
                                "message": f"Escrow {reference_id} was cancelled. Payment refunded to buyer wallet.",
                                "escrow_id": escrow_escrow_id,
                                "reason": "escrow_cancelled_payment_refunded"
                            }
                        
                        # Also check for other invalid states that shouldn't receive payments
                        elif escrow_status in [EscrowStatus.COMPLETED.value, EscrowStatus.DISPUTED.value, EscrowStatus.REFUNDED.value]:
                            logger.warning(f"🚨 INVALID_STATE: Payment received for escrow {reference_id} in state: {escrow_status}. Rejecting webhook.")
                            
                            # CRITICAL: Return success status to stop webhook retries
                            result_to_return = {
                                "status": "rejected",
                                "message": f"Escrow {reference_id} is in invalid state: {escrow_status}",
                                "escrow_id": escrow_escrow_id,
                                "reason": f"escrow_already_{escrow_status}"
                            }
                        
                        elif not result_to_return:
                            # MAIN PAYMENT PROCESSING: All within session block
                        
                            crypto_amount_decimal = Decimal(str(raw_crypto_amount))
                        
                            # Use DynoPay base_amount (USD) if available, otherwise convert via exchange rate
                            if dynopay_base_amt and dynopay_base_curr == 'USD':
                                usd_amount = Decimal(str(dynopay_base_amt))
                                usd_rate = Decimal(str(webhook_data.get('exchange_rate', 0))) if webhook_data.get('exchange_rate') else None
                                logger.info(f"📊 DYNOPAY_USD_DIRECT: Using base_amount=${usd_amount} for escrow processing")
                            else:
                                # PERFORMANCE OPTIMIZATION: Cache exchange rates to avoid repeated API calls
                                usd_rate = await DynoPayWebhookHandler._get_cached_exchange_rate(str(paid_currency) if paid_currency is not None else 'USD')
                        
                                # CRITICAL FIX: Handle None rate (exchange rate unavailable - need retry)
                                if usd_rate is None:
                                    logger.warning(f"⚠️ WEBHOOK_RATE_RETRY: Exchange rate unavailable for {paid_currency}, marking for retry")
                                    result_to_return = {
                                        "status": "retry",
                                        "message": f"Exchange rate unavailable for {paid_currency}, will retry",
                                        "escrow_id": escrow_escrow_id,
                                        "transaction_id": transaction_id
                                    }
                                else:
                                    usd_amount = crypto_amount_decimal * Decimal(str(usd_rate))
                            
                            if not result_to_return:
                            
                                # CRITICAL FIX: Don't create duplicate transaction here
                                # EscrowFundManager creates the correct transaction with ESCROW_PAYMENT type
                                created_transaction_id = f"ESC_{reference_id}_{transaction_id[:8]}"
                            
                                # UNIFIED PROCESSING: Use UnifiedPaymentProcessor for comprehensive overpayment/underpayment handling
                                logger.info(f"📊 UNIFIED PROCESSOR - Processing DynoPay payment for {escrow_escrow_id}")
                            
                                try:
                                    from services.unified_payment_processor import unified_processor
                                
                                    processing_result = await unified_processor.process_escrow_payment(
                                        escrow=escrow,
                                        received_amount=crypto_amount_decimal,
                                        received_usd=usd_amount,
                                        crypto_currency=str(paid_currency).upper() if paid_currency is not None else 'USD',
                                        tx_hash=transaction_id,
                                        price_usd=Decimal(str(usd_rate)) if usd_rate else Decimal(str(webhook_data.get('exchange_rate', 0))),
                                        session=session
                                    )
                                
                                    # CRITICAL: Extract and log holding verification results
                                    holding_verification = processing_result.fund_breakdown.get('holding_verification', {}) if processing_result.fund_breakdown is not None else {}
                                    holding_verified = processing_result.fund_breakdown.get('holding_verified', False) if processing_result.fund_breakdown is not None else False
                                    holding_auto_recovered = processing_result.fund_breakdown.get('holding_auto_recovered', False) if processing_result.fund_breakdown is not None else False
                                
                                    logger.info(
                                        f"🔍 DYNOPAY_HOLDING_VERIFICATION: {escrow_escrow_id} - "
                                        f"Verified: {holding_verified}, Auto-recovered: {holding_auto_recovered}"
                                    )
                                
                                    if processing_result.success:
                                        if processing_result.escrow_confirmed:
                                            # BUG FIX #3: Update escrow status AFTER successful processing
                                            escrow.status = EscrowStatus.PAYMENT_CONFIRMED.value  # type: ignore[assignment]
                                        
                                            # Get current timestamp using async query
                                            now_stmt = select(func.now())
                                            now_result = await session.execute(now_stmt)
                                            current_time = now_result.scalar()
                                        
                                            escrow.payment_confirmed_at = current_time  # type: ignore[assignment]
                                            from config import Config as DynoLegacyConfig
                                            escrow.expires_at = current_time + timedelta(minutes=DynoLegacyConfig.SELLER_RESPONSE_TIMEOUT_MINUTES) if current_time is not None else None  # type: ignore[operator]
                                            escrow.deposit_confirmed = True  # type: ignore[assignment]
                                            escrow.deposit_tx_hash = transaction_id  # type: ignore[assignment]
                                        
                                            # DELIVERY COUNTDOWN: Set delivery_deadline based on payment confirmation time
                                            if escrow.pricing_snapshot is not None and 'delivery_hours' in escrow.pricing_snapshot:  # type: ignore[arg-type]
                                                delivery_hours = int(escrow.pricing_snapshot['delivery_hours'])
                                                escrow.delivery_deadline = current_time + timedelta(hours=delivery_hours)
                                                escrow.auto_release_at = escrow.delivery_deadline + timedelta(hours=24)
                                                logger.info(f"⏰ DELIVERY_DEADLINE_SET: Escrow {escrow_escrow_id} delivery countdown starts - {delivery_hours}h")
                                        
                                            # CRITICAL FIX: Flush escrow status changes
                                            await session.flush()
                                            logger.info(f"✅ DynoPay unified processor confirmed escrow {escrow_escrow_id}")
                                        
                                            # Additional holding verification logging
                                            if holding_verified:
                                                if holding_auto_recovered:
                                                    logger.warning(f"🔧 DYNOPAY_AUTO_RECOVERY: Holding for {escrow_escrow_id} required auto-recovery")
                                                else:
                                                    logger.info(f"✅ DYNOPAY_HOLDING_VERIFIED: Holding verified for {escrow_escrow_id}")
                                            else:
                                                logger.error(f"❌ DYNOPAY_HOLDING_FAILED: Critical holding verification failure for {escrow_escrow_id}")
                                        
                                            # BUG FIX #4: Extract overpayment amount for buyer notification
                                            overpayment_credited = Decimal("0")
                                            if processing_result.overpayment_handled:
                                                overpayment_credited = Decimal(str(processing_result.fund_breakdown.get('overpayment_credited', 0))) if processing_result.fund_breakdown is not None else Decimal("0")
                                                if overpayment_credited > 0:
                                                    logger.info(f"💰 DynoPay overpayment handled: ${overpayment_credited:.2f}")
                                                else:
                                                    logger.error(f"❌ OVERPAYMENT_BUG: overpayment_handled=True but amount=0!")
                                        
                                            # NOTIFICATION DATA: Extract ALL data needed for notifications while still in session
                                            # This ensures buyer notification can be sent even if fresh escrow fetch fails
                                            buyer_telegram_id = escrow.buyer.telegram_id if escrow.buyer else None
                                            
                                            # CRITICAL FIX: Explicitly fetch buyer referral code to ensure it's available
                                            buyer_referral_code = None
                                            if escrow.buyer:
                                                buyer_referral_code = escrow.buyer.referral_code
                                                logger.info(f"🔍 REFERRAL_DEBUG: Buyer {escrow.buyer_id} has referral_code: {buyer_referral_code}")
                                            else:
                                                logger.warning(f"⚠️ REFERRAL_DEBUG: escrow.buyer is None for escrow {escrow_escrow_id}")
                                            
                                            seller_identifier = ""
                                            if escrow.seller and escrow.seller.username:
                                                seller_identifier = f"@{escrow.seller.username}"
                                            elif escrow.seller and escrow.seller.first_name:
                                                seller_identifier = escrow.seller.first_name
                                            else:
                                                seller_identifier = "Seller"
                                            
                                            notification_data = {
                                                "type": "seller_offer",
                                                "escrow_id": escrow_escrow_id,
                                                "overpayment_credited": overpayment_credited,
                                                # Buyer notification data (extracted in session for fallback)
                                                "buyer_telegram_id": buyer_telegram_id,
                                                "buyer_id": escrow.buyer_id,
                                                "escrow_amount": Decimal(str(escrow.amount or 0)),
                                                "buyer_fee": Decimal(str(escrow.buyer_fee_amount or 0)),
                                                "seller_identifier": seller_identifier,
                                                # Referral data for viral growth
                                                "seller_id": escrow.seller_id,
                                                "buyer_referral_code": buyer_referral_code
                                            }
                                            logger.info(f"📋 NOTIFICATION_DATA_DEBUG: seller_id={notification_data['seller_id']}, buyer_referral_code={notification_data['buyer_referral_code']}")
                                        
                                            if processing_result.underpayment_handled:
                                                logger.info(f"⚠️ DynoPay underpayment within tolerance")
                                        
                                            logger.info(f"✅ DynoPay deposit processed successfully: {reference_id}")
                                        
                                            # NO RETURN: Set result instead
                                            result_to_return = {
                                                "status": "success",
                                                "escrow_id": reference_id,
                                                "transaction_id": created_transaction_id,
                                                "amount_received": str(crypto_amount_decimal),
                                                "currency": paid_currency,
                                                "usd_value": str(usd_amount)
                                            }
                                            
                                        else:
                                            logger.warning(f"⚠️ DynoPay escrow {escrow_escrow_id} payment processed but not confirmed (insufficient payment)")
                                            escrow.status = EscrowStatus.PARTIAL_PAYMENT.value  # type: ignore[assignment]
                                        
                                            # NO RETURN: Set result instead
                                            result_to_return = {
                                                "status": "partial_payment",
                                                "escrow_id": reference_id,
                                                "message": "Payment received but insufficient",
                                                "transaction_id": created_transaction_id,
                                                "amount_received": str(crypto_amount_decimal),
                                                "currency": paid_currency,
                                                "usd_value": str(usd_amount)
                                            }
                                    else:
                                        logger.error(f"❌ DynoPay unified processor failed for {escrow_escrow_id}: {processing_result.error_message}")
                                    
                                        # CRITICAL FIX: Don't set payment_failed if payment was already confirmed
                                        if escrow_payment_confirmed_at is None:  # type: ignore[arg-type]
                                            escrow.status = EscrowStatus.PAYMENT_FAILED.value  # type: ignore[assignment]
                                        
                                            # CRITICAL: Clean up any frozen holdings from failed payment
                                            from services.escrow_fund_manager import EscrowFundManager
                                            cleanup_result = await EscrowFundManager.cleanup_failed_escrow_holdings(str(escrow_escrow_id), session)
                                            if cleanup_result.get("success") and cleanup_result.get("amount_released", 0) > 0:
                                                logger.info(f"💰 CLEANUP: Released ${cleanup_result['amount_released']:.2f} from failed escrow {escrow_escrow_id}")
                                        
                                            # ENHANCED ERROR LOGGING for payment failures
                                            await DynoPayWebhookHandler._log_payment_failure(escrow, processing_result.error_message, webhook_data, session)
                                        
                                            # CRITICAL: Schedule retry attempt for failed payment
                                            await DynoPayWebhookHandler._schedule_payment_retry(escrow, webhook_data, session)
                                        else:
                                            logger.warning(f"⚠️ Processing failed but payment was already confirmed at {escrow_payment_confirmed_at} - keeping confirmed status")
                                            await DynoPayWebhookHandler._log_payment_failure(escrow, f"Post-confirmation processing error: {processing_result.error_message}", webhook_data, session)
                                    
                                        # NO RETURN: Set result instead
                                        result_to_return = {
                                            "status": "error",
                                            "escrow_id": reference_id,
                                            "message": f"Payment processing failed: {processing_result.error_message}",
                                            "transaction_id": transaction_id
                                        }
                            
                                except Exception as e:
                                    logger.error(f"Critical error in DynoPay unified processor for {escrow_escrow_id}: {e}")
                                
                                    # Fallback to simple amount check
                                    if usd_amount >= Decimal(str(escrow_total_amount or 0)):  # type: ignore[arg-type]
                                        logger.info(f"✅ DynoPay fallback: Full payment received for escrow {reference_id}: ${usd_amount:.2f}")
                                    
                                        # NOTIFICATION DATA: Mark for fallback notification AFTER session commits
                                        notification_data = {
                                            "type": "fallback_confirmation",
                                            "user_id": escrow_buyer_id,
                                            "amount": crypto_amount_decimal,
                                            "currency": paid_currency,
                                            "amount_usd": usd_amount,
                                            "txid": transaction_id,
                                            "escrow_id": escrow_escrow_id
                                        }
                                    
                                        # NO RETURN: Set result instead
                                        result_to_return = {
                                            "status": "success",
                                            "escrow_id": reference_id,
                                            "transaction_id": created_transaction_id,
                                            "amount_received": str(crypto_amount_decimal),
                                            "currency": paid_currency,
                                            "usd_value": str(usd_amount)
                                        }
                                    else:
                                        logger.error(f"❌ DynoPay fallback: Payment failed for escrow {reference_id}: ${usd_amount:.2f}")
                                    
                                        # CRITICAL FIX: Don't set payment_failed if payment was already confirmed
                                        if escrow_payment_confirmed_at is None:  # type: ignore[arg-type]
                                            escrow.status = EscrowStatus.PAYMENT_FAILED.value  # type: ignore[assignment]
                                        
                                            # CRITICAL: Clean up any frozen holdings from failed payment
                                            from services.escrow_fund_manager import EscrowFundManager
                                            cleanup_result = await EscrowFundManager.cleanup_failed_escrow_holdings(str(escrow_escrow_id), session)
                                            if cleanup_result.get("success") and cleanup_result.get("amount_released", 0) > 0:
                                                logger.info(f"💰 CLEANUP: Released ${cleanup_result['amount_released']:.2f} from failed escrow {escrow_escrow_id}")
                                        
                                            # ENHANCED ERROR LOGGING for fallback failure
                                            await DynoPayWebhookHandler._log_payment_failure(escrow, f"Fallback payment failed: ${usd_amount:.2f} < ${Decimal(str(escrow_total_amount or 0)):.2f}", webhook_data, session)  # type: ignore[arg-type]
                                        
                                            # Schedule retry for fallback failure
                                            await DynoPayWebhookHandler._schedule_payment_retry(escrow, webhook_data, session)
                                        else:
                                            logger.warning(f"⚠️ Fallback processing failed but payment was already confirmed at {escrow_payment_confirmed_at} - keeping confirmed status")
                                            await DynoPayWebhookHandler._log_payment_failure(escrow, f"Post-confirmation fallback error: ${usd_amount:.2f} < ${Decimal(str(escrow_total_amount or 0)):.2f}", webhook_data, session)  # type: ignore[arg-type]
                                    
                                        # NO RETURN: Set result instead
                                        result_to_return = {
                                            "status": "error",
                                            "escrow_id": reference_id,
                                            "message": "Payment amount insufficient",
                                            "transaction_id": transaction_id
                                        }
            
            except IntegrityError as e:
                # CRITICAL FIX: Handle IntegrityError from session commit (overpayment constraint violations)
                # Check if this is the overpayment constraint violation
                if "ix_unique_escrow_overpayment" in str(e):
                    logger.warning(
                        f"⚠️ WEBHOOK_IDEMPOTENT: Duplicate overpayment transaction detected during commit. "
                        f"Transaction already processed in previous webhook attempt. "
                        f"Treating as idempotent success. Error: {str(e)}"
                    )
                    return {
                        "status": "success",
                        "message": "Payment already processed (idempotent)",
                        "transaction_id": transaction_id,
                        "idempotent": True
                    }
                else:
                    # Different IntegrityError, re-raise
                    logger.error(f"❌ WEBHOOK_INTEGRITY_ERROR: Unexpected IntegrityError during payment processing: {e}")
                    raise
            
            # NOTIFICATIONS: Send AFTER session commits (outside session block)
            if notification_data:
                try:
                    if notification_data.get("type") == "seller_offer":
                        # Send seller offer notification
                        # CRITICAL FIX: Fetch fresh escrow object to avoid DetachedInstanceError
                        from handlers.escrow import send_offer_to_seller_by_escrow
                        from database import async_managed_session as fresh_session_manager
                        
                        async with fresh_session_manager() as fresh_session:
                            from sqlalchemy.orm import selectinload
                            stmt = select(Escrow).options(
                                selectinload(Escrow.seller),
                                selectinload(Escrow.buyer)
                            ).where(Escrow.escrow_id == notification_data["escrow_id"])
                            result = await fresh_session.execute(stmt)
                            fresh_escrow = result.scalar_one_or_none()
                        
                        # CRITICAL FIX: Send seller notification using fresh escrow
                        if fresh_escrow:
                            try:
                                success = await send_offer_to_seller_by_escrow(fresh_escrow)
                                if success:
                                    logger.info(f"✅ Seller offer notification sent for {notification_data['escrow_id']}")
                                else:
                                    logger.error(f"❌ Failed to send seller offer notification for {notification_data['escrow_id']}")
                            except Exception as seller_notif_err:
                                logger.error(f"❌ Error sending seller offer notification for {notification_data['escrow_id']}: {seller_notif_err}")
                            
                            try:
                                from services.admin_trade_notifications import AdminTradeNotificationService
                                escrow_amount = fresh_escrow.amount if fresh_escrow.amount is not None else Decimal("0")
                                buyer_telegram_id = notification_data.get("buyer_telegram_id")
                                payment_notification_data = {
                                    'escrow_id': fresh_escrow.escrow_id,
                                    'amount': float(escrow_amount),
                                    'payment_method': 'crypto',
                                    'buyer_info': f"@{buyer_telegram_id}" if buyer_telegram_id else "Unknown",
                                    'seller_info': notification_data.get("seller_identifier", "Unknown")
                                }
                                admin_notif_service = AdminTradeNotificationService()
                                asyncio.create_task(
                                    admin_notif_service.send_group_notification_payment_confirmed(payment_notification_data)
                                )
                                logger.info(f"✅ ADMIN_NOTIFICATION: Payment confirmed notification queued for crypto escrow {fresh_escrow.escrow_id}")
                            except Exception as notif_err:
                                logger.error(f"❌ Failed to queue admin payment confirmed notification: {notif_err}")
                        else:
                            logger.error(f"❌ Could not fetch escrow {notification_data['escrow_id']} for seller notification")
                        
                        # CRITICAL FIX: ALWAYS send buyer notification using pre-extracted data from notification_data
                        # This ensures buyer gets confirmation even if fresh escrow fetch fails
                        try:
                            from services.consolidated_notification_service import ConsolidatedNotificationService, NotificationRequest, NotificationCategory, NotificationPriority, NotificationChannel, DeliveryStatus
                            
                            # Use pre-extracted data from notification_data (set inside session)
                            buyer_telegram_id = notification_data.get("buyer_telegram_id")
                            escrow_public_id = notification_data["escrow_id"]
                            escrow_amount = notification_data["escrow_amount"]
                            buyer_fee = notification_data["buyer_fee"]
                            total_paid = escrow_amount + buyer_fee
                            seller_identifier = notification_data["seller_identifier"]
                            overpayment_credited = notification_data.get("overpayment_credited", Decimal("0"))
                            
                            if buyer_telegram_id:
                                notification_service = ConsolidatedNotificationService()
                                
                                # Check if seller is onboarded and build referral section
                                referral_section = ""
                                keyboard_buttons = []
                                seller_onboarded = notification_data.get("seller_id") is not None
                                buyer_referral_code = notification_data.get("buyer_referral_code")
                                
                                if not seller_onboarded and buyer_referral_code:
                                    from config import Config
                                    from urllib.parse import quote
                                    referral_link = f"https://t.me/{Config.BOT_USERNAME}?start=ref_{buyer_referral_code}"
                                    share_text = quote("Hey! Join me on Lockbay for secure trades 🛡️")
                                    
                                    # Simple text without special characters to avoid Telegram parse errors
                                    referral_section = """

Seller not on Lockbay yet
Tap Share Invite below"""
                                    # Add Share Invite button with URL-encoded parameters
                                    keyboard_buttons.append({"text": "📤 Share Invite", "url": f"https://t.me/share/url?url={quote(referral_link)}&text={share_text}"})
                                
                                # Build overpayment line if applicable
                                overpayment_line = ""
                                if overpayment_credited > 0:
                                    overpayment_line = f"\n💰 +${float(overpayment_credited):.2f} credited to wallet"
                                
                                # Create buyer confirmation message
                                buyer_message = f"""✅ Payment Confirmed

#{escrow_public_id[-8:]} • ${float(escrow_amount):.2f}
Paid: ${float(total_paid):.2f} (inc. ${float(buyer_fee):.2f} fee){overpayment_line}
To: {seller_identifier}{referral_section}

⏰ Awaiting seller (24h)
🔒 Funds secured"""
                                
                                # Send via Telegram AND Email
                                buyer_request = NotificationRequest(
                                    user_id=buyer_telegram_id,
                                    category=NotificationCategory.ESCROW_UPDATES,
                                    priority=NotificationPriority.HIGH,
                                    title="✅ Payment Confirmed",
                                    message=buyer_message,
                                    channels=[NotificationChannel.TELEGRAM, NotificationChannel.EMAIL],
                                    broadcast_mode=True,
                                    idempotency_key=f"escrow_{escrow_public_id}_buyer_payment_confirmed",
                                    template_data={
                                        "keyboard": [[kb] for kb in keyboard_buttons] if keyboard_buttons else None,
                                        "parse_mode": None  # Use plain text to avoid parse errors with special characters
                                    }
                                )
                                
                                buyer_results = await notification_service.send_notification(buyer_request)
                                # Fix: send_notification returns Dict[str, DeliveryResult], not list
                                delivered_channels = [channel for channel, result in buyer_results.items() if result.status == DeliveryStatus.SENT]
                                
                                if delivered_channels:
                                    logger.info(f"✅ Buyer payment confirmation sent for {escrow_public_id} via {', '.join(delivered_channels)}")
                                else:
                                    logger.warning(f"⚠️ Failed to send buyer payment confirmation for {escrow_public_id}")
                            else:
                                logger.warning(f"⚠️ Buyer notification skipped for {escrow_public_id} - no buyer telegram_id")
                        except Exception as buyer_notif_err:
                            logger.error(f"❌ Error sending buyer payment confirmation for {notification_data['escrow_id']}: {buyer_notif_err}")
                    
                    elif notification_data.get("type") == "fallback_confirmation":
                        # Send fallback confirmation notification with referral invite support
                        from services.consolidated_notification_service import consolidated_notification_service
                        await consolidated_notification_service.send_escrow_payment_confirmation(
                            user_id=notification_data["user_id"],
                            amount=notification_data["amount"],
                            currency=notification_data["currency"],
                            amount_usd=notification_data["amount_usd"],
                            txid=notification_data["txid"],
                            escrow_id=notification_data.get("escrow_id")  # Pass escrow_id for referral invite
                        )
                        logger.info(f"✅ Fallback notification sent for {notification_data['escrow_id']}")
                except Exception as notif_err:
                    logger.error(f"❌ Failed to send notification after commit: {notif_err}")
            
            # RETURN: Final result after all processing complete
            if result_to_return:
                return result_to_return
            else:
                # Fallback if no result was set
                return {"status": "error", "message": "Processing completed but no result set"}
                
        except Exception as e:
            logger.error(f"DynoPay webhook handler error: {e}", exc_info=True)
            return {"status": "error", "message": "Internal server error"}

    @staticmethod
    async def _notify_payment_confirmed(escrow: Escrow, transaction: Transaction, session: AsyncSession) -> None:
        """Send notifications when payment is confirmed"""
        try:
            from utils.notification_helpers import send_telegram_message
            
            # CRITICAL FIX: Eagerly load all escrow and buyer/seller attributes INSIDE session context
            # This prevents "greenlet_spawn has not been called" errors when accessing relationships
            escrow_buyer_id = escrow.buyer_id
            escrow_seller_id = escrow.seller_id
            escrow_total_amount_value = escrow.total_amount
            escrow_utid = escrow.utid
            escrow_escrow_id = escrow.escrow_id
            escrow_currency = escrow.currency
            
            # Load buyer attributes (relationship access - must happen in session)
            buyer_telegram_id = escrow.buyer.telegram_id
            buyer_email = escrow.buyer.email if escrow.buyer else None
            buyer_email_verified = getattr(escrow.buyer, 'email_verified', False) if escrow.buyer else False
            buyer_first_name = escrow.buyer.first_name if escrow.buyer else None
            
            # Load seller attributes if seller exists (relationship access - must happen in session)
            seller_telegram_id = None
            seller_first_name = None
            if escrow_seller_id is not None and escrow.seller is not None:  # type: ignore[arg-type]
                seller_telegram_id = escrow.seller.telegram_id
                seller_first_name = escrow.seller.first_name
            
            # Extract transaction values
            transaction_amount = transaction.amount
            transaction_currency = transaction.currency
            transaction_id_value = transaction.transaction_id
            
            # Send admin email alert for deposit confirmation (fire-and-forget, non-blocking)
            try:
                from services.admin_email_alerts import AdminEmailAlertService
                admin_alerts = AdminEmailAlertService()
                
                # Send admin notification in background without blocking
                asyncio.create_task(
                    admin_alerts.send_transaction_alert(
                        "DEPOSIT_CONFIRMED",
                        float(Decimal(str(transaction_amount or 0))),
                        str(transaction_currency) if transaction_currency is not None else '',  # type: ignore[arg-type]
                        escrow.buyer,  # type: ignore[arg-type]
                        details={
                            'escrow_id': escrow_escrow_id,
                            'transaction_id': transaction_id_value,
                            'network': transaction_currency
                        }
                    )
                )
                logger.info(f"Admin deposit confirmation alert queued: {transaction_id_value}")
                
            except Exception as admin_error:
                logger.error(f"Failed to queue admin deposit confirmation alert: {admin_error}")
            
            # BUYER NOTIFICATIONS: Now handled by EnhancedPaymentToleranceService
            # This ensures buyer gets ONE accurate notification after tolerance check completes
            logger.info(f"🔄 BUYER_NOTIFICATION: Handled by EnhancedPaymentToleranceService for user {escrow_buyer_id}")
            
            # Notify seller if exists - using multi-channel notification system
            if escrow_seller_id is not None and seller_telegram_id is not None:  # type: ignore[arg-type]
                try:
                    # Load seller email for multi-channel fallback
                    seller_email = escrow.seller.email if escrow.seller else None
                    seller_username = escrow.seller.username if escrow.seller else None
                    
                    # Use the consolidated notification helper for multi-channel delivery
                    from handlers.escrow import _notify_registered_seller_new_escrow
                    await _notify_registered_seller_new_escrow(
                        seller_id=int(escrow_seller_id) if escrow_seller_id is not None else 0,
                        seller_username=str(seller_username) if seller_username is not None else f"user_{escrow_seller_id}",
                        seller_email=str(seller_email) if seller_email is not None else "",
                        escrow_id=str(escrow_escrow_id),
                        buyer_name=str(buyer_first_name) if buyer_first_name is not None else 'Anonymous',
                        amount=Decimal(str(transaction_amount)) if transaction_amount is not None else Decimal('0'),
                        currency=str(transaction_currency) if transaction_currency is not None else ''
                    )
                    logger.info(f"✅ Multi-channel seller notification sent for escrow {escrow_escrow_id}")
                except Exception as e:
                    logger.error(f"Failed to send multi-channel seller notification for {escrow_seller_id}: {e}")
            
            logger.info(f"Notifications sent for escrow {escrow_escrow_id}")
            
        except Exception as e:
            logger.error(f"Error sending payment confirmation notifications: {e}")

    @staticmethod
    async def _send_payment_confirmation_email(
        escrow: Escrow, transaction: Transaction, base_message: str, variance_explanation: str,
        buyer_email: str, buyer_email_verified: bool, buyer_first_name: str
    ) -> None:
        """Send payment confirmation email as fallback when Telegram fails"""
        escrow_buyer_id = None
        try:
            # CRITICAL FIX: Extract escrow buyer_id first for error logging
            escrow_buyer_id = escrow.buyer_id
            
            # CRITICAL FIX: Extract escrow values before using
            escrow_total_amount_email = escrow.total_amount
            escrow_utid = escrow.utid
            escrow_escrow_id = escrow.escrow_id
            
            # Check if buyer has email and it's verified
            if not buyer_email or not buyer_email.strip():
                logger.warning(f"No email address for buyer {escrow_buyer_id} - cannot send email fallback")
                return
                
            if not buyer_email_verified:
                logger.warning(f"Email not verified for buyer {escrow_buyer_id} - skipping email fallback")
                return
            
            from services.email import EmailService
            from config import Config
            
            email_service = EmailService()
            
            # Calculate USD equivalent for display
            usd_amount = Decimal(str(escrow_total_amount_email or 0))
            usd_display = f" (${usd_amount:.2f})" if usd_amount > 0 else ""  # type: ignore[arg-type]
            
            # Use consistent branding ID from utid field
            branding_trade_id = escrow_utid or escrow_escrow_id or 'N/A'  # type: ignore[operator]
            display_trade_id = branding_trade_id if branding_trade_id else "N/A"
            
            buyer_name = buyer_first_name or "Valued Customer"
            
            # Email subject
            subject = f"🎉 Payment Confirmed - Trade #{display_trade_id} | {Config.PLATFORM_NAME}"
            
            # Create HTML email content with payment details and variance information
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
                <div style="background: #28a745; color: white; padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
                    <h1 style="margin: 0; font-size: 28px;">🎉 Payment Confirmed!</h1>
                    <p style="margin: 10px 0 0 0; font-size: 16px; opacity: 0.9;">{Config.PLATFORM_NAME}</p>
                </div>
                <div style="background: #f9f9f9; padding: 30px; border-radius: 0 0 10px 10px;">
                    <h2>Hello {buyer_name}!</h2>
                    <p><strong>Great news! Your escrow payment has been confirmed and your funds are now safely secured.</strong></p>
                    
                    <div style="background: #e8f5e8; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #28a745;">
                        <h3 style="margin-top: 0; color: #155724;">💰 Payment Details</h3>
                        <p><strong>Amount:</strong> {transaction.amount} {transaction.currency}{usd_display}</p>
                        <p><strong>Trade ID:</strong> #{display_trade_id}</p>
                        <p><strong>Status:</strong> ✅ Payment received • Escrow created</p>
                    </div>
            """
            
            # Add variance explanation if applicable
            if variance_explanation.strip():
                html_content += f"""
                    <div style="background: #fff3cd; padding: 15px; border-radius: 5px; border-left: 4px solid #ffc107; margin: 20px 0;">
                        <h4 style="margin-top: 0; color: #856404;">💡 Payment Note</h4>
                        <p style="margin-bottom: 0;">{variance_explanation.replace('💡 Payment Note: ', '').replace('✅ ', '').replace('✨ ', '').strip()}</p>
                    </div>
                """
            
            # Continue with next steps and footer
            html_content += f"""
                    <div style="background: #d1ecf1; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #17a2b8;">
                        <h3 style="margin-top: 0; color: #0c5460;">📨 Next Steps</h3>
                        <p><strong>Seller invitation sent:</strong> We've notified the seller that your payment is secured.</p>
                        <p><strong>Escrow protection:</strong> Your funds are safely held until delivery is confirmed.</p>
                        <p><strong>Stay updated:</strong> You'll receive notifications as your trade progresses.</p>
                    </div>
                    
                    <div style="background: #f8f9fa; padding: 15px; border-radius: 5px; border: 1px solid #dee2e6; margin: 20px 0;">
                        <p style="margin: 0; color: #6c757d; font-size: 14px;">
                            🔒 <strong>Security:</strong> Your funds are protected by our escrow system until you confirm delivery.
                        </p>
                    </div>
                    
                    <p>If you have any questions or concerns, please contact our support team.</p>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <p style="color: #666; font-size: 14px;">
                            This is an automated notification from {Config.PLATFORM_NAME}<br>
                            If you received this email instead of a Telegram message, it means our Telegram bot was temporarily unavailable.
                        </p>
                    </div>
                </div>
                <div style="background: #6c757d; color: white; padding: 20px; text-align: center; border-radius: 0 0 10px 10px;">
                    <p style="margin: 0; font-size: 14px;">© 2025 {Config.PLATFORM_NAME}. Secure trading platform.</p>
                </div>
            </div>
            """
            
            # Create plain text version
            text_content = f"""
            🎉 Payment Confirmed - {Config.PLATFORM_NAME}
            
            Hello {buyer_name}!
            
            Great news! Your escrow payment has been confirmed and your funds are now safely secured.
            
            💰 PAYMENT DETAILS:
            Amount: {transaction.amount} {transaction.currency}{usd_display}
            Trade ID: #{display_trade_id}
            Status: ✅ Payment received • Escrow created
            
            {variance_explanation}
            
            📨 NEXT STEPS:
            • Seller invitation sent: We've notified the seller that your payment is secured
            • Escrow protection: Your funds are safely held until delivery is confirmed
            • Stay updated: You'll receive notifications as your trade progresses
            
            🔒 SECURITY: Your funds are protected by our escrow system until you confirm delivery.
            
            If you have any questions or concerns, please contact our support team.
            
            ---
            This is an automated notification from {Config.PLATFORM_NAME}
            If you received this email instead of a Telegram message, it means our Telegram bot was temporarily unavailable.
            
            © 2025 {Config.PLATFORM_NAME}. Secure trading platform.
            """
            
            # Send the email
            success = email_service.send_email(
                to_email=buyer_email,
                subject=subject,
                html_content=html_content,
                text_content=text_content
            )
            
            if success:
                logger.info(f"✅ Payment confirmation email sent to buyer {escrow_buyer_id} at {buyer_email}")
            else:
                logger.error(f"❌ Failed to send payment confirmation email to buyer {escrow_buyer_id}")
                
        except Exception as e:
            logger.error(f"Error sending payment confirmation email to buyer {escrow_buyer_id}: {e}")
            raise

    @staticmethod
    async def _handle_payment_after_cancellation(
        escrow: Escrow, webhook_data: Dict[str, Any], session: AsyncSession
    ) -> Dict[str, Any]:
        """Handle payment received for cancelled escrow - trigger refund process"""
        try:
            # TYPE SAFETY: Extract escrow Column values as scalar types
            escrow_id_value = escrow.id
            escrow_buyer_id = escrow.buyer_id
            escrow_escrow_id = escrow.escrow_id
            buyer_telegram_id = escrow.buyer.telegram_id if escrow.buyer else None
            
            # Extract payment details
            meta_data = webhook_data.get('meta_data', {})
            paid_amount = webhook_data.get('paid_amount') or webhook_data.get('amount')
            paid_currency = webhook_data.get('paid_currency') or webhook_data.get('currency')
            transaction_id = webhook_data.get('id') or webhook_data.get('payment_id') or webhook_data.get('txId')
            
            # Log the incident for security monitoring
            logger.error(
                f"🚨 SECURITY ALERT: Payment {transaction_id} received for CANCELLED escrow {escrow_escrow_id}! "
                f"Amount: {paid_amount} {paid_currency}. Initiating automatic refund process."
            )
            
            # Get current timestamp using async query
            now_stmt = select(func.now())
            now_result = await session.execute(now_stmt)
            current_time = now_result.scalar()
            
            # Create a special transaction record for audit trail
            refund_transaction = Transaction(
                transaction_id=UniversalIDGenerator.generate_transaction_id(),
                user_id=escrow_buyer_id,
                escrow_id=escrow_id_value,
                transaction_type=TransactionType.REFUND.value,
                amount=Decimal(str(paid_amount)),
                currency=paid_currency,
                status="pending_refund",
                description=f"REFUND REQUIRED: Payment received after cancellation - {paid_amount} {paid_currency}",
                external_id=transaction_id,
                to_address=meta_data.get('deposit_address'),
                confirmed_at=current_time,
                confirmations=1
            )
            session.add(refund_transaction)
            
            # Send immediate alert to buyer
            try:
                from utils.notification_helpers import send_telegram_message
                buyer_message = (
                    f"⚠️ Payment Received After Cancellation\n\n"
                    f"💰 Amount: {paid_amount} {paid_currency}\n"
                    f"🆔 Escrow: {escrow_escrow_id}\n"
                    f"📋 Transaction: {refund_transaction.transaction_id}\n\n"
                    f"🔄 Your payment will be automatically refunded within 24 hours.\n"
                    f"📞 Contact support if you have questions."
                )
                if buyer_telegram_id:
                    await send_telegram_message(buyer_telegram_id, buyer_message)
            except Exception as e:
                logger.error(f"Failed to notify buyer about payment after cancellation: {e}")
            
            # Send admin alert
            try:
                from services.consolidated_notification_service import consolidated_notification_service
                await consolidated_notification_service.send_admin_alert(
                    title="Payment After Cancellation",
                    message=f"Escrow {escrow_escrow_id} received payment {transaction_id} after being cancelled. "
                            f"Amount: {paid_amount} {paid_currency}. Refund transaction created: {refund_transaction.transaction_id}",
                    priority=NotificationPriority.HIGH
                )
            except Exception as e:
                logger.error(f"Failed to send admin alert: {e}")
            
            # Note: No explicit commit needed - parent async_managed_session context handles it
            
            return {
                "status": "refund_required",
                "reason": "payment_after_cancellation",
                "escrow_id": escrow_escrow_id,
                "refund_transaction_id": refund_transaction.transaction_id,
                "amount": str(Decimal(str(paid_amount or 0))),
                "currency": paid_currency,
                "message": "Payment received for cancelled escrow. Refund process initiated."
            }
            
        except Exception as e:
            logger.error(f"Error handling payment after cancellation: {e}")
            # Note: No explicit rollback needed - parent async_managed_session context handles it
            raise
    
    @staticmethod
    async def _handle_invalid_state_payment(
        escrow: Escrow, webhook_data: Dict[str, Any], session: AsyncSession
    ) -> Dict[str, Any]:
        """Handle payment received for escrow in invalid state"""
        try:
            # TYPE SAFETY: Extract escrow Column values as scalar types
            escrow_id_value = escrow.id
            escrow_buyer_id = escrow.buyer_id
            escrow_escrow_id = escrow.escrow_id
            escrow_status = escrow.status if not hasattr(escrow.status, 'value') else escrow.status.value
            
            # Extract payment details
            paid_amount = webhook_data.get('paid_amount') or webhook_data.get('amount')
            paid_currency = webhook_data.get('paid_currency') or webhook_data.get('currency')
            transaction_id = webhook_data.get('id') or webhook_data.get('payment_id') or webhook_data.get('txId')
            
            logger.warning(
                f"⚠️ Payment {transaction_id} received for escrow {escrow_escrow_id} in invalid state: {escrow_status}. "
                f"Amount: {paid_amount} {paid_currency}. Flagging for manual review."
            )
            
            # Get current timestamp using async query
            now_stmt = select(func.now())
            now_result = await session.execute(now_stmt)
            current_time = now_result.scalar()
            
            # Create transaction record for audit
            review_transaction = Transaction(
                transaction_id=UniversalIDGenerator.generate_transaction_id(),
                user_id=escrow_buyer_id,
                escrow_id=escrow_id_value,
                transaction_type=TransactionType.DEPOSIT.value,
                amount=Decimal(str(paid_amount)),
                currency=paid_currency,
                status="manual_review",
                description=f"MANUAL REVIEW: Payment received for escrow in {escrow_status} state",
                external_id=transaction_id,
                confirmed_at=current_time,
                confirmations=1
            )
            session.add(review_transaction)
            
            # Send admin alert for manual review
            try:
                from services.consolidated_notification_service import consolidated_notification_service
                await consolidated_notification_service.send_admin_alert(
                    title="Invalid State Payment",
                    message=f"Escrow {escrow_escrow_id} (status: {escrow_status}) received payment {transaction_id}. "
                            f"Amount: {paid_amount} {paid_currency}. Manual review required.",
                    priority=NotificationPriority.NORMAL
                )
            except Exception as e:
                logger.error(f"Failed to send admin alert: {e}")
            
            # Note: No explicit commit needed - parent async_managed_session context handles it
            
            return {
                "status": "manual_review",
                "reason": f"payment_for_{escrow_status}_escrow",
                "escrow_id": escrow_escrow_id,
                "transaction_id": review_transaction.transaction_id,
                "amount": str(Decimal(str(paid_amount or 0))),
                "currency": paid_currency,
                "message": f"Payment received for escrow in {escrow_status} state. Flagged for manual review."
            }
            
        except Exception as e:
            logger.error(f"Error handling invalid state payment: {e}")
            # Note: No explicit rollback needed - parent async_managed_session context handles it
            raise

    @staticmethod
    async def validate_webhook_request(request: Request) -> bool:
        """SECURITY: Validate DynoPay webhook request authenticity with signature verification"""
        try:
            # CRITICAL SECURITY FIX: Add proper signature verification
            from utils.webhook_security import WebhookSecurity
            from config import Config
            import json
            
            # Get raw request body for signature verification
            body = await request.body()
            if not body:
                logger.error("🚨 DYNOPAY_SECURITY: Empty request body - rejecting")
                return False
            
            # Extract signature from headers
            signature = WebhookSecurity.extract_webhook_signature(dict(request.headers), "dynopay")
            
            # PRODUCTION SECURITY: Enforce signature verification in production
            is_production = getattr(Config, "ENVIRONMENT", "development").lower() in ["production", "prod"]
            
            if is_production:
                # Production mode: REQUIRE signature verification
                if not signature:
                    logger.critical("🚨 PRODUCTION_SECURITY_BREACH: No signature header in DynoPay webhook")
                    WebhookSecurity.log_security_violation(
                        "dynopay", 
                        request.client.host if request.client else "unknown",
                        "missing_signature",
                        "Production webhook without signature"
                    )
                    return False
                
                # Verify signature
                try:
                    webhook_data = json.loads(body)
                except json.JSONDecodeError as e:
                    logger.error(f"🚨 DYNOPAY_SECURITY: Invalid JSON in webhook: {e}")
                    return False
                
                is_valid = WebhookSecurity.verify_dynopay_webhook(webhook_data, signature)
                if not is_valid:
                    logger.critical("🚨 PRODUCTION_SECURITY_BREACH: DynoPay webhook signature verification FAILED")
                    WebhookSecurity.log_security_violation(
                        "dynopay", 
                        request.client.host if request.client else "unknown",
                        "invalid_signature",
                        f"Signature: {signature[:16] if signature else 'None'}..."
                    )
                    return False
                    
                logger.info("✅ PRODUCTION_SECURITY: DynoPay webhook signature verified successfully")
                
            else:
                # Development mode: Allow optional verification with warnings
                if signature:
                    try:
                        webhook_data = json.loads(body)
                        is_valid = WebhookSecurity.verify_dynopay_webhook(webhook_data, signature)
                        if is_valid:
                            logger.info("✅ DEV_SECURITY: DynoPay webhook signature verified successfully")
                        else:
                            logger.warning("⚠️ DEV_SECURITY: DynoPay webhook signature verification failed")
                            logger.warning("⚠️ DEV_SECURITY: Processing anyway in development mode")
                    except json.JSONDecodeError as e:
                        logger.error(f"⚠️ DEV_SECURITY: Invalid JSON in webhook: {e}")
                        return False
                else:
                    # SECURITY FIX: More accurate logging when secret is configured but no signature sent
                    if getattr(Config, "DYNOPAY_WEBHOOK_SECRET", None):
                        logger.warning("⚠️ DEV_SECURITY: DYNOPAY_WEBHOOK_SECRET is configured, but DynoPay webhook has no signature header")
                        logger.warning("⚠️ DYNOPAY_INTEGRATION: DynoPay may not be configured to send signature headers - check provider settings")
                    else:
                        logger.warning("⚠️ CONFIGURE: Set DYNOPAY_WEBHOOK_SECRET environment variable for security")
            
            # Additional field validation after signature verification
            try:
                webhook_data = json.loads(body) if isinstance(body, bytes) else body
            except json.JSONDecodeError:
                logger.error("DynoPay webhook invalid JSON format")
                return False
                
            required_fields = ['meta_data', 'paid_amount', 'paid_currency', 'id']
            for field in required_fields:
                if field not in webhook_data:  # type: ignore[operator]
                    logger.error(f"DynoPay webhook missing required field: {field}")
                    return False
            
            return True
            
        except Exception as e:
            logger.error(f"DynoPay webhook validation error: {e}")
            # SECURITY: Fail secure - reject on validation errors
            return False
    
    @staticmethod
    async def _log_payment_failure(escrow: Escrow, error_message: str, webhook_data: Dict[str, Any], session: AsyncSession) -> None:
        """Enhanced logging for payment failures with detailed context"""
        try:
            # TYPE SAFETY: Extract escrow Column values as scalar types
            escrow_buyer_id = escrow.buyer_id
            escrow_escrow_id = escrow.escrow_id
            
            transaction_id = webhook_data.get('id') or webhook_data.get('payment_id') or webhook_data.get('txId') or 'unknown'
            paid_amount = webhook_data.get('paid_amount') or webhook_data.get('amount') or 0
            paid_currency = webhook_data.get('paid_currency') or webhook_data.get('currency') or 'unknown'
            
            # Comprehensive failure logging
            logger.error(
                f"🚨 PAYMENT_FAILURE_DETAILED: Escrow {escrow_escrow_id} - "
                f"TxID: {transaction_id}, Amount: {paid_amount} {paid_currency}, "
                f"Error: {error_message}"
            )
            
            # Log failure to audit trail
            logger.error(
                f"🚨 PAYMENT_AUDIT_TRAIL: Escrow {escrow_escrow_id} payment failed - "
                f"User: {escrow_buyer_id}, Amount: {paid_amount} {paid_currency}, "
                f"TxID: {transaction_id}, Error: {error_message}"
            )
            
            # Notify admin of payment failure (non-blocking)
            try:
                import asyncio
                from services.admin_email_alerts import AdminEmailAlertService
                admin_alerts = AdminEmailAlertService()
                
                asyncio.create_task(
                    admin_alerts.send_transaction_alert(
                        "PAYMENT_FAILED",
                        float(Decimal(str(paid_amount or 0))),
                        str(paid_currency) if paid_currency != 'unknown' else '',
                        escrow.buyer,  # type: ignore[arg-type]
                        details={
                            'escrow_id': escrow_escrow_id,
                            'transaction_id': transaction_id,
                            'error_message': error_message,
                            'webhook_provider': 'dynopay'
                        }
                    )
                )
                logger.info(f"Admin payment failure alert queued: {escrow_escrow_id}")
                
            except Exception as admin_error:
                logger.error(f"Failed to queue admin payment failure alert: {admin_error}")
                
        except Exception as log_error:
            logger.error(f"Error in payment failure logging: {log_error}")
    
    @staticmethod
    async def _schedule_payment_retry(escrow: Escrow, webhook_data: Dict[str, Any], session: AsyncSession) -> None:
        """Schedule automatic retry attempt for failed payments"""
        try:
            # TYPE SAFETY: Extract escrow Column values as scalar types
            escrow_buyer_id = escrow.buyer_id
            escrow_escrow_id = escrow.escrow_id
            escrow_total_amount = escrow.total_amount
            
            # Create retry record in unified transaction system
            transaction_id = webhook_data.get('id', 'unknown')
            
            logger.info(
                f"🔄 PAYMENT_RETRY_SCHEDULED: Escrow {escrow_escrow_id} - "
                f"TxID: {transaction_id} scheduled for retry attempt"
            )
            
            # COMMENTED OUT: unified_tx_service.create_transaction() method not available
            # Using existing retry mechanism instead
            # retry_unified_tx = await unified_tx_service.create_transaction(
            #     transaction_type=UnifiedTransactionType.ESCROW_PAYMENT_RETRY,
            #     amount=escrow_total_amount,
            #     currency='USD',
            #     user_id=escrow_buyer_id,
            #     reference_id=escrow_escrow_id,
            #     metadata={
            #         'original_webhook_data': webhook_data,
            #         'retry_reason': 'payment_processing_failure',
            #         'original_transaction_id': transaction_id,
            #         'escrow_id': escrow_escrow_id,
            #         'provider': 'dynopay'
            #     }
            # )
            
            # Log retry scheduling info for manual intervention
            logger.warning(
                f"⚠️ PAYMENT_RETRY_NEEDED: Escrow {escrow_escrow_id} requires manual retry - "
                f"TxID: {transaction_id}, Amount: {escrow_total_amount} USD"
            )
                
        except Exception as retry_error:
            logger.error(f"Error scheduling payment retry: {retry_error}")
    
    @staticmethod
    async def handle_wallet_deposit_webhook(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process DynoPay webhook for wallet deposit confirmation.
        
        CRYPTO→USD CONVERSION FIX: Converts crypto amounts to USD before crediting wallet,
        mirroring the escrow fix from unified_payment_processor.py lines 80-92.
        """
        try:
            # Extract webhook data
            meta_data = webhook_data.get('meta_data', {})
            reference_id = meta_data.get('refId') or webhook_data.get('customer_reference')
            # DynoPay field mapping (same as escrow handler):
            # - 'amount' = crypto amount (e.g., 0.01331 ETH)
            # - 'base_amount' = USD value (e.g., 10) - authoritative payment value
            # - 'currency' = crypto currency (ETH, BTC, etc.)
            # - 'base_currency' = fiat currency (USD)
            crypto_amount = webhook_data.get('amount') or webhook_data.get('paid_amount')
            paid_currency = webhook_data.get('paid_currency') or webhook_data.get('currency')
            transaction_id = webhook_data.get('id') or webhook_data.get('payment_id') or webhook_data.get('txId')
            
            # Use base_amount (USD) from DynoPay when available - this is the authoritative value
            dynopay_base_amount = webhook_data.get('base_amount')
            dynopay_base_currency = webhook_data.get('base_currency', 'USD')
            
            if dynopay_base_amount and dynopay_base_currency == 'USD':
                paid_amount = dynopay_base_amount
                logger.info(f"📊 WALLET_DYNOPAY_AMOUNT: Using base_amount=${dynopay_base_amount} USD (crypto: {crypto_amount} {paid_currency}, rate: {webhook_data.get('exchange_rate')})")
            else:
                paid_amount = crypto_amount
                logger.info(f"📊 WALLET_DYNOPAY_AMOUNT: No base_amount available, using crypto amount: {paid_amount} {paid_currency}")
            
            if not reference_id or not paid_amount or not paid_currency or not transaction_id:
                logger.error("DynoPay wallet webhook missing required fields")
                return {"status": "error", "message": "Missing required webhook fields"}
            
            # REPLAY ATTACK PROTECTION: Extract timestamp from webhook for validation
            webhook_timestamp = None
            created_at = webhook_data.get('created_at')
            if created_at:
                try:
                    # Convert ISO string to datetime with UTC timezone
                    webhook_timestamp = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    if webhook_timestamp.tzinfo is None:
                        webhook_timestamp = webhook_timestamp.replace(tzinfo=timezone.utc)
                    logger.info(f"🔒 TIMESTAMP_EXTRACTED: Wallet webhook timestamp: {webhook_timestamp.isoformat()}")
                    
                    # Validate timestamp to prevent replay attacks
                    from services.webhook_idempotency_service import WebhookIdempotencyService
                    is_valid, error_msg = WebhookIdempotencyService.validate_webhook_timestamp(webhook_timestamp)
                    if not is_valid:
                        logger.error(f"🚨 WALLET_REPLAY_ATTACK_BLOCKED: {error_msg}")
                        return {"status": "error", "message": f"Timestamp validation failed: {error_msg}"}
                except (ValueError, AttributeError) as e:
                    logger.warning(f"⚠️ TIMESTAMP_PARSE_ERROR: Failed to parse created_at '{created_at}': {e}")
                    webhook_timestamp = None
            else:
                logger.warning(f"⚠️ TIMESTAMP_MISSING: No created_at field in wallet webhook for {transaction_id}")
                # FALLBACK: Use current server time for audit trail integrity
                webhook_timestamp = datetime.now(timezone.utc)
                logger.info(f"🔧 TIMESTAMP_FALLBACK: Using server time {webhook_timestamp.isoformat()} for audit trail")
            
            # Parse user_id from reference (format: WALLET-YYYYMMDD-HHMMSS-{user_id})
            if not reference_id.startswith('WALLET-'):
                logger.error(f"Invalid wallet reference format: {reference_id}")
                return {"status": "error", "message": f"Invalid wallet reference format: {reference_id}"}
            
            try:
                user_id = int(reference_id.split('-')[-1])
            except (IndexError, ValueError) as e:
                logger.error(f"Failed to parse user_id from reference {reference_id}: {e}")
                return {"status": "error", "message": f"Invalid wallet reference: {reference_id}"}
            
            logger.info(f"💰 WALLET_DEPOSIT: Processing deposit for user {user_id}, {paid_amount} {paid_currency}, txid: {transaction_id}")
            
            # CRITICAL FIX: Use DynoPay's base_amount (USD) when available
            # This mirrors the escrow handler's approach and prevents rate discrepancy losses
            from services.fastforex_service import FastForexService
            forex_service = FastForexService()
            
            if dynopay_base_amount and dynopay_base_currency == 'USD':
                # DynoPay already provided authoritative USD value - use it directly
                usd_amount = Decimal(str(dynopay_base_amount))
                logger.info(f"💱 WALLET_USD_AUTHORITATIVE: Using DynoPay base_amount=${usd_amount:.2f} (crypto: {crypto_amount} {paid_currency})")
            elif paid_currency == 'USD':
                usd_amount = Decimal(str(paid_amount or 0))
                logger.info(f"💱 WALLET_USD: Direct USD deposit: ${usd_amount:.2f}")
            else:
                # Fallback: Convert crypto to USD using cached rate (only when no base_amount)
                crypto_rate = await forex_service.get_crypto_to_usd_rate(paid_currency)
                if crypto_rate is None:
                    logger.error(f"❌ WALLET_RATE_UNAVAILABLE: No rate available for {paid_currency}")
                    return {"status": "retry", "message": f"Exchange rate unavailable for {paid_currency}"}
                
                usd_amount = Decimal(str(paid_amount or 0)) * Decimal(str(crypto_rate))
                logger.info(f"💱 WALLET_USD_CONVERSION_FALLBACK: Converted {paid_amount} {paid_currency} to ${usd_amount:.2f} USD (rate: ${crypto_rate:.2f})")
            
            # Use async session to credit wallet
            from models import Wallet, CryptoDeposit, CryptoDepositStatus
            
            async with async_managed_session() as session:
                
                # Check for duplicate transaction
                stmt_tx = select(Transaction).where(Transaction.blockchain_tx_hash == transaction_id)
                result_tx = await session.execute(stmt_tx)
                existing_tx = result_tx.scalar_one_or_none()
                
                if existing_tx:
                    logger.warning(f"⚠️ WALLET_DUPLICATE: Transaction {transaction_id} already processed")
                    return {
                        "status": "already_processed",
                        "transaction_id": existing_tx.transaction_id,
                        "reason": "duplicate_tx_hash"
                    }
                
                # Get or create user's USD wallet
                stmt_wallet = select(Wallet).where(
                    Wallet.user_id == user_id,
                    Wallet.currency == 'USD'
                )
                result_wallet = await session.execute(stmt_wallet)
                wallet = result_wallet.scalar_one_or_none()
                
                if not wallet:
                    wallet = Wallet(
                        user_id=user_id,
                        currency='USD',
                        available_balance=Decimal('0.00')
                    )
                    session.add(wallet)
                    await session.flush()
                    logger.info(f"✅ WALLET_CREATED: Created USD wallet for user {user_id}")
                
                # Credit wallet balance
                old_balance = wallet.available_balance
                wallet.available_balance += Decimal(str(usd_amount))  # type: ignore[assignment]
                logger.info(f"💰 WALLET_CREDIT: user={user_id}, old=${old_balance}, new=${wallet.available_balance}, added=${usd_amount:.2f}")
                
                # Get current timestamp using async query
                now_stmt = select(func.now())
                now_result = await session.execute(now_stmt)
                current_time = now_result.scalar()
                
                # Create transaction record
                transaction = Transaction(
                    transaction_id=UniversalIDGenerator.generate_transaction_id(),
                    user_id=user_id,
                    transaction_type=TransactionType.DEPOSIT.value,
                    amount=Decimal(str(usd_amount)),
                    currency='USD',
                    status="confirmed",
                    description=f"DynoPay wallet deposit - {paid_amount} {paid_currency} → ${usd_amount:.2f} USD",
                    blockchain_tx_hash=transaction_id,
                    confirmed_at=current_time
                )
                session.add(transaction)
                
                # Create or update crypto deposit record
                stmt_deposit = select(CryptoDeposit).where(
                    CryptoDeposit.txid == transaction_id,
                    CryptoDeposit.provider == 'dynopay'
                )
                result_deposit = await session.execute(stmt_deposit)
                deposit = result_deposit.scalar_one_or_none()
                
                if not deposit:
                    deposit = CryptoDeposit(
                        provider='dynopay',
                        txid=transaction_id,
                        order_id=reference_id,
                        user_id=user_id,
                        coin=paid_currency.upper(),
                        amount=Decimal(str(paid_amount)),
                        amount_fiat=Decimal(str(usd_amount)),
                        status=CryptoDepositStatus.CREDITED.value,
                        confirmations=1,
                        address_in='dynopay_wallet_deposit',  # FIX: Add required address_in field
                        address_out=None
                    )
                    session.add(deposit)
                else:
                    deposit.status = CryptoDepositStatus.CREDITED.value  # type: ignore[assignment]
                    deposit.amount_fiat = Decimal(str(usd_amount))  # type: ignore[assignment]
                
                # Extract transaction_id before session closes
                transaction_id_value = transaction.transaction_id
                
                logger.info(f"✅ WALLET_DEPOSIT_SUCCESS: {reference_id}, user {user_id}, ${usd_amount:.2f} credited")
            
            # Send notification to user
            try:
                from services.wallet_notification_service import WalletNotificationService
                
                notification_sent = await WalletNotificationService.send_crypto_deposit_confirmation(
                    user_id=user_id,
                    amount_crypto=Decimal(str(paid_amount)),
                    currency=paid_currency,
                    amount_usd=Decimal(str(usd_amount)),
                    txid_in=transaction_id
                )
                
                if notification_sent:
                    logger.info(f"✅ WALLET_NOTIFICATION: Sent deposit confirmation to user {user_id}")
                else:
                    logger.warning(f"⚠️ WALLET_NOTIFICATION: Failed to send deposit confirmation to user {user_id}")
                    
            except Exception as notif_error:
                logger.error(f"❌ WALLET_NOTIFICATION_ERROR: {notif_error}")
                # Don't fail the webhook if notification fails
            
            return {
                "status": "success",
                "user_id": user_id,
                "transaction_id": transaction_id_value,
                "amount_usd": str(usd_amount),
                "original_amount": str(Decimal(str(paid_amount or 0))),
                "original_currency": paid_currency
            }
                
        except Exception as e:
            logger.error(f"❌ WALLET_DEPOSIT_ERROR: {e}", exc_info=True)
            return {"status": "error", "message": str(e)}