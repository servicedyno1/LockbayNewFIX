"""DynoPay Webhook Handler for Exchange Operations - Integrated with Unified Transaction System"""

import logging
from typing import Dict, Any, Optional
from decimal import Decimal
from datetime import datetime, timezone
from fastapi import Request, HTTPException
from sqlalchemy import select
from models import ExchangeOrder, Transaction, TransactionType, ExchangeStatus, User, UnifiedTransaction, UnifiedTransactionType, UnifiedTransactionStatus, WebhookEventLedger, Wallet
from services.unified_transaction_service import create_unified_transaction_service
from services.dual_write_adapter import DualWriteMode
from utils.universal_id_generator import UniversalIDGenerator
from utils.financial_audit_logger import (
    financial_audit_logger,
    FinancialEventType,
    EntityType,
    FinancialContext
)

# SECURITY: Exchange state validation
from utils.exchange_state_validator import ExchangeStateValidator

logger = logging.getLogger(__name__)

# Initialize unified transaction service for DynoPay exchange webhooks
unified_tx_service = create_unified_transaction_service(DualWriteMode.DUAL_WRITE_LEGACY_PRIMARY)


class DynoPayExchangeWebhookHandler:
    """Handles DynoPay webhook callbacks for exchange operations"""

    @staticmethod
    async def handle_exchange_deposit_webhook(webhook_data: Dict[str, Any], headers: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Process DynoPay webhook for exchange deposit confirmation with distributed locking"""
        try:
            # ARCHITECT REQUIREMENT #1: EXPLICIT WEBHOOK AUTHENTICATION AT ENTRY POINT
            from utils.webhook_security import WebhookSecurity
            
            # Extract signature from headers for verification
            signature = None
            if headers:
                signature = headers.get('x-dynopay-signature') or headers.get('X-DynoPay-Signature')
            
            # CRITICAL SECURITY: Explicit signature verification call
            if not WebhookSecurity.verify_dynopay_webhook(webhook_data, signature or ""):
                logger.critical("🚨 DYNOPAY_EXCHANGE_SECURITY_BREACH: Webhook signature verification FAILED")
                raise HTTPException(status_code=401, detail="Webhook authentication failed")
            
            logger.info("✅ DYNOPAY_EXCHANGE_AUTH: Webhook signature verified successfully")
            
            # Extract webhook data
            meta_data = webhook_data.get('meta_data', {})
            reference_id = meta_data.get('refId')
            paid_amount = webhook_data.get('paid_amount')
            paid_currency = webhook_data.get('paid_currency')
            transaction_id = webhook_data.get('id')
            
            if not reference_id:
                logger.error("DynoPay exchange webhook missing reference_id")
                raise HTTPException(status_code=400, detail="Missing reference ID")
            
            if not paid_amount or not paid_currency:
                logger.error("DynoPay exchange webhook missing payment details")
                raise HTTPException(status_code=400, detail="Missing payment details")
                
            if not transaction_id:
                logger.error("DynoPay exchange webhook missing transaction_id")
                raise HTTPException(status_code=400, detail="Missing transaction ID")
            
            # REPLAY ATTACK PROTECTION: Extract timestamp from webhook for validation
            webhook_timestamp = None
            created_at = webhook_data.get('created_at')
            if created_at:
                try:
                    # Convert ISO string to datetime with UTC timezone
                    webhook_timestamp = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    if webhook_timestamp.tzinfo is None:
                        webhook_timestamp = webhook_timestamp.replace(tzinfo=timezone.utc)
                    logger.info(f"🔒 TIMESTAMP_EXTRACTED: Exchange webhook timestamp: {webhook_timestamp.isoformat()}")
                    
                    # Validate timestamp to prevent replay attacks
                    from services.webhook_idempotency_service import WebhookIdempotencyService
                    is_valid, error_msg = WebhookIdempotencyService.validate_webhook_timestamp(webhook_timestamp)
                    if not is_valid:
                        logger.critical(f"🚨 EXCHANGE_REPLAY_ATTACK_BLOCKED: {error_msg}")
                        raise HTTPException(status_code=400, detail=f"Timestamp validation failed: {error_msg}")
                except (ValueError, AttributeError) as e:
                    logger.warning(f"⚠️ TIMESTAMP_PARSE_ERROR: Failed to parse created_at '{created_at}': {e}")
                    webhook_timestamp = None
            else:
                logger.warning(f"⚠️ TIMESTAMP_MISSING: No created_at field in exchange webhook for {transaction_id}")
                # FALLBACK: Use current server time for audit trail integrity
                webhook_timestamp = datetime.now(timezone.utc)
                logger.info(f"🔧 TIMESTAMP_FALLBACK: Using server time {webhook_timestamp.isoformat()} for audit trail")
            
            logger.info(f"DynoPay exchange webhook received: {reference_id}, {paid_amount} {paid_currency}, tx_id: {transaction_id}")
            
            # CRITICAL FIX: Add distributed locking for exchange payment confirmations
            from utils.distributed_lock import distributed_lock_service
            
            # Acquire distributed lock for this payment
            additional_data = {
                "callback_source": "dynopay_exchange",
                "paid_amount": paid_amount,
                "paid_currency": paid_currency,
                "reference_id": reference_id
            }
            
            with distributed_lock_service.acquire_payment_lock(
                order_id=str(reference_id),
                txid=transaction_id,
                timeout=120,  # 2 minutes timeout for payment processing
                additional_data=additional_data
            ) as lock:
                
                if not lock.acquired:
                    logger.warning(
                        f"EXCHANGE_RACE_CONDITION_PREVENTED: Could not acquire lock for "
                        f"exchange {reference_id}, txid {transaction_id}. Reason: {lock.error}"
                    )
                    return {"status": "already_processing", "message": "Exchange payment is being processed"}
                
                logger.critical(
                    f"EXCHANGE_DISTRIBUTED_LOCK_SUCCESS: Processing DynoPay exchange payment for order {reference_id}, "
                    f"txid {transaction_id} with exclusive lock"
                )
                
                # Log webhook event for auditing and idempotency
                await DynoPayExchangeWebhookHandler._log_webhook_event(
                    webhook_data, reference_id, transaction_id
                )
                
                # Process the exchange deposit atomically within the lock
                return await DynoPayExchangeWebhookHandler._process_locked_exchange_payment(
                    webhook_data, reference_id, transaction_id
                )
                
        except HTTPException:
            # Re-raise HTTP exceptions as-is (validation errors, auth failures, etc.)
            raise
        except Exception as e:
            logger.error(f"DynoPay exchange webhook handler error: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")

    @staticmethod
    async def _process_unified_exchange_payment(
        unified_tx: UnifiedTransaction, webhook_data: Dict[str, Any], 
        paid_amount: float, paid_currency: str, transaction_id: str, session
    ) -> Dict[str, Any]:
        """Process DynoPay exchange payment for unified transaction with proper status transitions"""
        try:
            tx_id_val = unified_tx.id
            tx_id = str(tx_id_val if tx_id_val is not None else 0)
            
            # Extract enum values properly without str() which produces wrong format
            transaction_type_col = unified_tx.transaction_type
            if hasattr(transaction_type_col, 'value'):
                transaction_type = transaction_type_col.value
            else:
                transaction_type = transaction_type_col
            
            # Keep status as Enum for proper comparisons
            status_col = unified_tx.status
            if isinstance(status_col, UnifiedTransactionStatus):
                current_status = status_col
            else:
                current_status = UnifiedTransactionStatus(status_col)
            
            current_status_value = str(current_status.value) if hasattr(current_status, 'value') else str(current_status)
            transaction_type_str = str(transaction_type)
            
            logger.info(f"🔄 UNIFIED_EXCHANGE_DYNOPAY: Processing {transaction_type} payment for transaction {tx_id}, "
                       f"current status: {current_status.value}, amount: {paid_amount} {paid_currency}")
            
            # Determine next status based on transaction type
            if transaction_type_str in [UnifiedTransactionType.EXCHANGE_SELL_CRYPTO.value, UnifiedTransactionType.EXCHANGE_BUY_CRYPTO.value]:
                # Exchange flow: awaiting_payment → payment_confirmed  
                if current_status_value == str(UnifiedTransactionStatus.AWAITING_PAYMENT.value):
                    next_status = UnifiedTransactionStatus.PAYMENT_CONFIRMED
                    reason = f"DynoPay exchange crypto payment confirmed: {paid_amount} {paid_currency}"
                else:
                    logger.warning(f"Unexpected status {current_status.value} for exchange payment confirmation")
                    return {"status": "error", "message": f"Invalid status for payment: {current_status.value}"}
                    
            elif transaction_type_str == str(UnifiedTransactionType.ESCROW.value):
                # This should not happen in exchange webhook but handle it gracefully
                logger.warning(f"Received escrow transaction in exchange webhook: {tx_id}")
                if current_status_value == str(UnifiedTransactionStatus.AWAITING_PAYMENT.value):
                    next_status = UnifiedTransactionStatus.PAYMENT_CONFIRMED
                    reason = f"DynoPay crypto payment confirmed: {paid_amount} {paid_currency}"
                else:
                    return {"status": "error", "message": f"Invalid status for payment: {current_status.value}"}
                    
            elif transaction_type_str == str(UnifiedTransactionType.WALLET_CASHOUT.value):
                # Wallet cashout: This should not receive payments via DynoPay
                logger.warning(f"Received payment webhook for wallet cashout {tx_id} - this is unexpected")
                return {"status": "error", "message": "Wallet cashouts should not receive payments"}
                
            else:
                logger.error(f"Unknown transaction type: {transaction_type}")
                return {"status": "error", "message": f"Unknown transaction type: {transaction_type}"}
            
            # Update unified transaction metadata with payment details
            payment_metadata = {
                'dynopay_exchange_payment_confirmed': True,
                'dynopay_amount_received': str(paid_amount),
                'dynopay_currency': paid_currency,
                'dynopay_transaction_id': transaction_id,
                'dynopay_confirmation_timestamp': __import__('datetime').datetime.utcnow().isoformat()
            }
            
            # Transition status using unified service
            transition_result = await unified_tx_service.transition_status(
                transaction_id=tx_id,
                new_status=next_status,
                reason=reason,
                metadata=payment_metadata,
                session=session
            )
            
            if transition_result.success:
                logger.info(f"✅ UNIFIED_EXCHANGE_DYNOPAY: Successfully transitioned {tx_id} from "
                           f"{current_status.value} to {next_status.value}")
                
                # Log financial audit event
                user_id_raw = unified_tx.user_id
                user_id_int: int = int(user_id_raw) if user_id_raw is not None else 0  # type: ignore[arg-type]
                financial_audit_logger.log_financial_event(
                    event_type=FinancialEventType.WALLET_CREDIT,
                    entity_type=EntityType.TRANSACTION,
                    entity_id=tx_id,
                    user_id=user_id_int,
                    financial_context=FinancialContext(
                        amount=Decimal(str(paid_amount)),
                        currency=paid_currency
                    ),
                    previous_state=current_status.value,
                    new_state=next_status.value,
                    related_entities={
                        "transaction_type": str(transaction_type),
                        "payment_source": "dynopay_exchange_webhook"
                    },
                    additional_data={
                        "source": "dynopay_exchange_webhook._process_unified_exchange_payment",
                        "dynopay_transaction_id": transaction_id
                    }
                )
                
                return {
                    "status": "success", 
                    "message": "Exchange payment processed successfully",
                    "transaction_id": tx_id,
                    "new_status": next_status.value
                }
            else:
                logger.error(f"❌ UNIFIED_EXCHANGE_DYNOPAY: Failed to transition {tx_id}: {transition_result.error}")
                return {"status": "error", "message": f"Status transition failed: {transition_result.error}"}
                
        except Exception as e:
            tx_id_val = unified_tx.id
            logger.error(f"❌ UNIFIED_EXCHANGE_DYNOPAY: Error processing unified exchange payment for {tx_id_val if tx_id_val is not None else 0}: {e}", exc_info=True)
            return {"status": "error", "message": "Internal processing error"}

    @staticmethod
    async def _process_locked_exchange_payment(
        webhook_data: Dict[str, Any], reference_id: str, transaction_id: str
    ) -> Dict[str, Any]:
        """Process DynoPay exchange payment within distributed lock context"""
        try:
            # Extract webhook data
            webhook_data.get('meta_data', {})
            paid_amount = webhook_data.get('paid_amount')
            paid_currency = webhook_data.get('paid_currency')
            
            # Process the exchange deposit atomically
            # CRITICAL FIX: Use async_atomic_transaction for atomic payment processing
            from utils.atomic_transactions import async_atomic_transaction
            
            async with async_atomic_transaction() as session:
                
                # Check for unified transaction first (new system)
                stmt = select(UnifiedTransaction).where(
                    UnifiedTransaction.transaction_metadata['exchange_id'].astext == reference_id
                )
                result = await session.execute(stmt)
                unified_tx = result.scalar_one_or_none()
                
                if unified_tx:
                    tx_id_val = unified_tx.id
                    logger.info(f"🔄 UNIFIED_EXCHANGE_DYNOPAY: Processing payment for unified transaction {tx_id_val if tx_id_val is not None else 0}")
                    return await DynoPayExchangeWebhookHandler._process_unified_exchange_payment(
                        unified_tx, webhook_data, float(paid_amount) if paid_amount is not None else 0.0, str(paid_currency) if paid_currency is not None else "", transaction_id, session
                    )
                
                # Fallback to legacy exchange order processing for backward compatibility
                # Handle multiple reference ID formats: EX123, EXC_1758218901410_3122_000000, or direct numeric
                exchange_order = None
                
                try:
                    if reference_id.startswith('EXC_'):
                        # New format: EXC_1758218901410_3122_000000 -> extract the timestamp part (1758218901410)
                        parts = reference_id.split('_')
                        if len(parts) >= 2:
                            try:
                                order_id = int(parts[1])  # Extract 1758218901410 from EXC_1758218901410_3122_000000
                                stmt = select(ExchangeOrder).where(ExchangeOrder.id == order_id)
                                result = await session.execute(stmt)
                                exchange_order = result.scalar_one_or_none()
                            except ValueError:
                                # If parsing fails, try querying by exchange_id field directly
                                logger.info(f"Could not parse numeric ID from {reference_id}, trying exchange_id field lookup")
                                stmt = select(ExchangeOrder).where(ExchangeOrder.exchange_id == reference_id)
                                result = await session.execute(stmt)
                                exchange_order = result.scalar_one_or_none()
                        else:
                            logger.error(f"Invalid EXC_ reference format: {reference_id}")
                            raise HTTPException(status_code=400, detail="Invalid reference ID format")
                    elif reference_id.startswith('EX'):
                        # Legacy format: EX123 -> extract 123
                        try:
                            order_id = int(reference_id[2:])
                            stmt = select(ExchangeOrder).where(ExchangeOrder.id == order_id)
                            result = await session.execute(stmt)
                            exchange_order = result.scalar_one_or_none()
                        except ValueError:
                            # If parsing fails, try querying by exchange_id field directly
                            logger.info(f"Could not parse numeric ID from {reference_id}, trying exchange_id field lookup")
                            stmt = select(ExchangeOrder).where(ExchangeOrder.exchange_id == reference_id)
                            result = await session.execute(stmt)
                            exchange_order = result.scalar_one_or_none()
                    else:
                        # Direct numeric format
                        try:
                            order_id = int(reference_id)
                            stmt = select(ExchangeOrder).where(ExchangeOrder.id == order_id)
                            result = await session.execute(stmt)
                            exchange_order = result.scalar_one_or_none()
                        except ValueError:
                            # If parsing fails, try querying by exchange_id field directly
                            logger.info(f"Could not parse numeric ID from {reference_id}, trying exchange_id field lookup")
                            stmt = select(ExchangeOrder).where(ExchangeOrder.exchange_id == reference_id)
                            result = await session.execute(stmt)
                            exchange_order = result.scalar_one_or_none()
                except Exception as e:
                    logger.error(f"Error parsing reference_id {reference_id}: {e}")
                
                # Final fallback: if still not found, try direct exchange_id lookup
                if not exchange_order:
                    logger.info(f"Final fallback: querying by exchange_id={reference_id}")
                    stmt = select(ExchangeOrder).where(ExchangeOrder.exchange_id == reference_id)
                    result = await session.execute(stmt)
                    exchange_order = result.scalar_one_or_none()
                    
                if not exchange_order:
                    logger.error(f"Exchange order not found for reference_id: {reference_id}")
                    raise HTTPException(status_code=404, detail="Exchange order not found")
                
                logger.info(f"📊 LEGACY_EXCHANGE_DYNOPAY: Processing payment for legacy exchange order {exchange_order.id}")
                
                # ENHANCED IDEMPOTENCY CHECK: Check multiple ways to prevent duplicates
                # Check by external_tx_id (primary DynoPay identifier)
                stmt = select(Transaction).where(Transaction.external_tx_id == transaction_id)
                result = await session.execute(stmt)
                existing_by_tx_id = result.scalar_one_or_none()
                
                if existing_by_tx_id:
                    logger.warning(f"DynoPay exchange payment already processed - found by external_tx_id: {transaction_id}")
                    return {
                        "status": "already_processed", 
                        "transaction_id": existing_by_tx_id.transaction_id,
                        "reason": "duplicate_tx_id"
                    }
                
                # Additional check: by user + transaction type + amount + currency (in case tx_hash changes)
                from decimal import Decimal
                check_user_id = int(exchange_order.user_id) if exchange_order.user_id is not None else 0  # type: ignore[arg-type]
                stmt = select(Transaction).where(
                    Transaction.user_id == check_user_id,
                    Transaction.transaction_type == TransactionType.DEPOSIT.value,
                    Transaction.amount == Decimal(str(paid_amount)),
                    Transaction.currency == paid_currency,
                    Transaction.escrow_id.is_(None)
                )
                result = await session.execute(stmt)
                existing_by_exchange = result.scalar_one_or_none()
                
                if existing_by_exchange:
                    logger.warning(f"DynoPay exchange payment already processed - found by user/amount: {reference_id}")
                    return {
                        "status": "already_processed", 
                        "transaction_id": existing_by_exchange.transaction_id,
                        "reason": "duplicate_exchange_deposit"
                    }
                
                # Validate payment amount and currency
                crypto_amount_decimal = Decimal(str(paid_amount))
                source_curr = exchange_order.source_currency
                expected_currency: str = str(source_curr) if source_curr is not None else ""  # type: ignore[arg-type]
                
                if str(paid_currency).upper() != expected_currency.upper():
                    logger.error(f"Currency mismatch: expected {expected_currency}, got {paid_currency}")
                    raise HTTPException(status_code=400, detail="Currency mismatch")
                
                # Create transaction record
                from datetime import datetime
                user_id_raw = exchange_order.user_id
                user_id_for_tx: int = int(user_id_raw) if user_id_raw is not None else 0  # type: ignore[arg-type]
                transaction = Transaction(
                    transaction_id=UniversalIDGenerator.generate_transaction_id(),
                    user_id=user_id_for_tx,
                    escrow_id=None,
                    transaction_type=TransactionType.DEPOSIT.value,
                    amount=crypto_amount_decimal,
                    currency=paid_currency,
                    status="confirmed",
                    description=f"DynoPay exchange deposit - {paid_amount} {paid_currency}",
                    external_tx_id=transaction_id,  # FIX: Use external_tx_id instead of tx_hash
                    confirmed_at=datetime.utcnow()
                )
                session.add(transaction)
                
                # Update exchange order status with validation
                try:
                    current_status = ExchangeStatus(exchange_order.status)
                    new_status = ExchangeStatus.PAYMENT_CONFIRMED
                    ExchangeStateValidator.validate_transition(
                        current_status, new_status, reference_id
                    )
                    setattr(exchange_order, 'status', new_status.value)
                    setattr(exchange_order, 'deposit_tx_hash', transaction_id)
                except Exception as validation_error:
                    logger.error(
                        f"🚫 DYNOPAY_EXCHANGE_BLOCKED: {current_status}→{new_status} for order {reference_id}: {validation_error}"
                    )
                    # Still update transaction hash for audit trail
                    setattr(exchange_order, 'deposit_tx_hash', transaction_id)
                    # Webhook idempotency: Don't crash, just log and continue
                
                # COMPREHENSIVE PAYMENT PROCESSING: Use OverpaymentService for tolerance handling
                source_amount_value = exchange_order.source_amount
                expected_amount = Decimal(str(source_amount_value if source_amount_value is not None else 0))
                received_amount = crypto_amount_decimal
                
                logger.info(f"📊 DYNOPAY EXCHANGE - Processing payment for order {reference_id}: Expected {expected_amount}, Received {received_amount}")
                
                try:
                    from services.overpayment_service import OverpaymentService
                    from services.fastforex_service import FastForexService, fastforex_service
                    
                    # WEBHOOK OPTIMIZATION: Get cached USD rate - never makes API calls  
                    cached_usd_rate = await fastforex_service.get_crypto_to_usd_rate_webhook_optimized(str(paid_currency))
                    if cached_usd_rate is None:
                        logger.error(f"❌ EXCHANGE_WEBHOOK: No cached rate for {paid_currency} - failing fast")
                        raise HTTPException(
                            status_code=503,
                            detail=f"Exchange rate temporarily unavailable for {paid_currency}. Please retry."
                        )
                    usd_rate = Decimal(str(cached_usd_rate))
                    
                    # CRITICAL FIX: Convert expected amount to USD for proper comparison
                    # Mirroring escrow fix from unified_payment_processor.py lines 80-92
                    if str(exchange_order.source_currency) == 'USD':
                        expected_usd = float(expected_amount)
                    else:
                        # Convert crypto to USD using cached rate
                        forex_service = FastForexService()
                        crypto_rate = await forex_service.get_crypto_to_usd_rate(str(exchange_order.source_currency))
                        expected_usd = float(expected_amount) * float(crypto_rate)
                        logger.info(f"💱 EXCHANGE_USD_CONVERSION: Converted {expected_amount} {str(exchange_order.source_currency)} to ${float(expected_usd):.2f} USD (rate: ${float(crypto_rate):.2f})")
                    
                    # Convert received amount to USD
                    received_usd = float(received_amount) * float(usd_rate)
                    logger.info(f"💱 EXCHANGE_USD_CONVERSION: Converted {received_amount} {paid_currency} to ${float(received_usd):.2f} USD (rate: ${float(usd_rate):.2f})")
                    
                    # Now compare USD amounts instead of crypto amounts
                    expected_amount_for_comparison = Decimal(str(expected_usd))
                    received_amount_for_comparison = Decimal(str(received_usd))
                    
                    # FIXED: Compare USD amounts instead of crypto amounts
                    if received_amount_for_comparison > expected_amount_for_comparison:
                        # Handle overpayment using existing service
                        logger.info(f"DynoPay exchange overpayment detected for order {reference_id} (${float(received_usd):.2f} > ${float(expected_usd):.2f})")
                        
                        user_id_raw_over = exchange_order.user_id
                        order_id_raw_over = exchange_order.id
                        user_id_over: int = int(user_id_raw_over) if user_id_raw_over is not None else 0  # type: ignore[arg-type]
                        order_id_over: int = int(order_id_raw_over) if order_id_raw_over is not None else 0  # type: ignore[arg-type]
                        overpayment_success = await OverpaymentService.handle_exchange_overpayment(
                            user_id=user_id_over,
                            order_id=order_id_over,
                            expected_amount=expected_amount,
                            received_amount=received_amount,
                            crypto_currency=str(paid_currency).upper(),
                            usd_rate=usd_rate
                        )
                        
                        if overpayment_success:
                            logger.info(f"✅ DynoPay exchange overpayment processed successfully for order {reference_id}")
                            # Validate state transition before processing
                            try:
                                current_status = ExchangeStatus(exchange_order.status)
                                new_status = ExchangeStatus.PROCESSING
                                ExchangeStateValidator.validate_transition(
                                    current_status, new_status, reference_id
                                )
                                setattr(exchange_order, 'status', new_status.value)
                                await DynoPayExchangeWebhookHandler._process_exchange_order(exchange_order, session)
                            except Exception as validation_error:
                                logger.error(
                                    f"🚫 DYNOPAY_EXCHANGE_BLOCKED: {current_status}→{new_status} for order {reference_id}: {validation_error}"
                                )
                                # Webhook idempotency: Log but continue
                        else:
                            logger.warning(f"Failed to process DynoPay exchange overpayment for order {reference_id}")
                            # Keep in PAYMENT_CONFIRMED for manual review
                            try:
                                current_status = ExchangeStatus(exchange_order.status)
                                new_status = ExchangeStatus.PAYMENT_CONFIRMED
                                ExchangeStateValidator.validate_transition(
                                    current_status, new_status, reference_id
                                )
                                setattr(exchange_order, 'status', new_status.value)
                            except Exception as validation_error:
                                logger.error(
                                    f"🚫 DYNOPAY_EXCHANGE_BLOCKED: {current_status}→{new_status} for order {reference_id}: {validation_error}"
                                )
                            
                    elif received_amount_for_comparison < expected_amount_for_comparison:
                        # Handle underpayment with tolerance check
                        logger.info(f"DynoPay exchange underpayment detected for order {reference_id} (${float(received_usd):.2f} < ${float(expected_usd):.2f})")
                        
                        user_id_raw_under = exchange_order.user_id
                        order_id_raw_under = exchange_order.id
                        user_id_under: int = int(user_id_raw_under) if user_id_raw_under is not None else 0  # type: ignore[arg-type]
                        order_id_under: int = int(order_id_raw_under) if order_id_raw_under is not None else 0  # type: ignore[arg-type]
                        underpayment_success = await OverpaymentService.handle_exchange_underpayment(
                            user_id=user_id_under,
                            order_id=order_id_under,
                            expected_amount=expected_amount,
                            received_amount=received_amount,
                            crypto_currency=str(paid_currency).upper(),
                            usd_rate=usd_rate
                        )
                        
                        if underpayment_success:
                            logger.info(f"✅ DynoPay exchange underpayment accepted (within tolerance) for order {reference_id}")
                            # Validate state transition before processing
                            try:
                                current_status = ExchangeStatus(exchange_order.status)
                                new_status = ExchangeStatus.PROCESSING
                                ExchangeStateValidator.validate_transition(
                                    current_status, new_status, reference_id
                                )
                                setattr(exchange_order, 'status', new_status.value)
                                await DynoPayExchangeWebhookHandler._process_exchange_order(exchange_order, session)
                            except Exception as validation_error:
                                logger.error(
                                    f"🚫 DYNOPAY_EXCHANGE_BLOCKED: {current_status}→{new_status} for order {reference_id}: {validation_error}"
                                )
                        else:
                            logger.warning(f"❌ DynoPay exchange underpayment exceeds tolerance for order {reference_id}")
                            # Keep in PAYMENT_CONFIRMED for manual review
                            try:
                                current_status = ExchangeStatus(exchange_order.status)
                                new_status = ExchangeStatus.PAYMENT_CONFIRMED
                                ExchangeStateValidator.validate_transition(
                                    current_status, new_status, reference_id
                                )
                                setattr(exchange_order, 'status', new_status.value)
                            except Exception as validation_error:
                                logger.error(
                                    f"🚫 DYNOPAY_EXCHANGE_BLOCKED: {current_status}→{new_status} for order {reference_id}: {validation_error}"
                                )
                            
                    else:
                        # Exact payment - process normally
                        logger.info(f"✅ DynoPay exact payment received for exchange {reference_id}: {paid_amount} {paid_currency} (${float(received_usd):.2f} USD)")
                        # Validate state transition before processing
                        try:
                            current_status = ExchangeStatus(exchange_order.status)
                            new_status = ExchangeStatus.PROCESSING
                            ExchangeStateValidator.validate_transition(
                                current_status, new_status, reference_id
                            )
                            setattr(exchange_order, 'status', new_status.value)
                            await DynoPayExchangeWebhookHandler._process_exchange_order(exchange_order, session)
                        except Exception as validation_error:
                            logger.error(
                                f"🚫 DYNOPAY_EXCHANGE_BLOCKED: {current_status}→{new_status} for order {reference_id}: {validation_error}"
                            )
                        
                except Exception as e:
                    logger.error(f"Error in DynoPay comprehensive payment processing for order {reference_id}: {e}")
                    
                    # Fallback to simple amount check
                    source_amount_fallback = exchange_order.source_amount
                    if float(crypto_amount_decimal) >= float(Decimal(str(source_amount_fallback if source_amount_fallback is not None else 0))):
                        logger.info(f"✅ DynoPay fallback: Full payment received for exchange {reference_id}")
                        # Validate state transition before processing
                        try:
                            current_status = ExchangeStatus(exchange_order.status)
                            new_status = ExchangeStatus.PROCESSING
                            ExchangeStateValidator.validate_transition(
                                current_status, new_status, reference_id
                            )
                            setattr(exchange_order, 'status', new_status.value)
                            await DynoPayExchangeWebhookHandler._process_exchange_order(exchange_order, session)
                        except Exception as validation_error:
                            logger.error(
                                f"🚫 DYNOPAY_EXCHANGE_BLOCKED: {current_status}→{new_status} for order {reference_id}: {validation_error}"
                            )
                    else:
                        logger.warning(f"⚠️ DynoPay fallback: Partial payment for exchange {reference_id}")
                        # Keep in PAYMENT_CONFIRMED for review
                        try:
                            current_status = ExchangeStatus(exchange_order.status)
                            new_status = ExchangeStatus.PAYMENT_CONFIRMED
                            ExchangeStateValidator.validate_transition(
                                current_status, new_status, reference_id
                            )
                            setattr(exchange_order, 'status', new_status.value)
                        except Exception as validation_error:
                            logger.error(
                                f"🚫 DYNOPAY_EXCHANGE_BLOCKED: {current_status}→{new_status} for order {reference_id}: {validation_error}"
                            )
                    
                # ENHANCED TRANSACTION VERIFICATION: Log successful completion
                logger.info(f"✅ TRANSACTION_COMPLETED: DynoPay exchange deposit processed successfully: {reference_id}")
                logger.debug("✅ TRANSACTION_VERIFICATION: All operations completed within single atomic transaction")
                
                return {
                    "status": "success",
                    "exchange_order_id": reference_id,
                    "transaction_id": transaction.transaction_id,
                    "amount_received": float(crypto_amount_decimal),
                    "currency": paid_currency
                }
                
        except Exception as e:
            logger.error(f"DynoPay exchange webhook handler error: {e}")
            raise HTTPException(status_code=500, detail="Internal server error")

    @staticmethod
    async def _process_exchange_order(exchange_order: ExchangeOrder, session):
        """Process the exchange order after payment confirmation"""
        try:
            # Import here to avoid circular imports
            
            if str(exchange_order.order_type) == "crypto_to_ngn":
                # Process crypto to NGN exchange
                logger.info(f"Processing crypto to NGN exchange: {exchange_order.id}")
                
                # The actual exchange processing logic would go here
                # This might involve:
                # 1. Converting crypto to USD
                # 2. Converting USD to NGN
                # 3. Initiating bank transfer via Fincra
                # 4. Updating order status to completed
                
                # Mark as processing with validation
                try:
                    current_status = ExchangeStatus(exchange_order.status)
                    new_status = ExchangeStatus.PROCESSING
                    order_id_val = exchange_order.id
                    order_id_str = str(order_id_val) if order_id_val is not None else "unknown"
                    ExchangeStateValidator.validate_transition(
                        current_status, new_status, order_id_str
                    )
                    setattr(exchange_order, 'status', new_status.value)
                except Exception as validation_error:
                    logger.error(
                        f"🚫 DYNOPAY_EXCHANGE_BLOCKED: {current_status}→{new_status} for order {exchange_order.id}: {validation_error}"
                    )
                
            elif str(exchange_order.order_type) == "sell_crypto":
                # FIX: Process sell_crypto exchange - credit user wallet with final_amount
                order_id_raw = exchange_order.id
                user_id_raw = exchange_order.user_id
                order_id_sell: int = int(order_id_raw) if order_id_raw is not None else 0  # type: ignore[arg-type]
                user_id_sell: int = int(user_id_raw) if user_id_raw is not None else 0  # type: ignore[arg-type]
                logger.info(f"Processing sell_crypto exchange: {order_id_sell}")
                
                # Credit user's wallet with the final_amount (after fees)
                stmt = select(Wallet).where(Wallet.user_id == user_id_sell)
                result = await session.execute(stmt)
                wallet = result.scalar_one_or_none()
                
                if wallet:
                    wallet_balance = wallet.available_balance
                    final_amt = exchange_order.final_amount
                    old_balance = Decimal(str(wallet_balance if wallet_balance is not None else 0))
                    new_balance = old_balance + Decimal(str(final_amt if final_amt is not None else 0))
                    setattr(wallet, 'available_balance', new_balance)
                    logger.info(f"💰 EXCHANGE_WALLET_CREDIT: user={user_id_sell}, old=${old_balance}, new=${new_balance}, added=${Decimal(str(final_amt if final_amt is not None else 0))}")
                else:
                    logger.error(f"Wallet not found for user {user_id_sell}")
                
                # Mark as processing (will be completed after final processing) with validation
                try:
                    current_status = ExchangeStatus(exchange_order.status)
                    new_status = ExchangeStatus.PROCESSING
                    order_id_val = exchange_order.id
                    order_id_str = str(order_id_val) if order_id_val is not None else "unknown"
                    ExchangeStateValidator.validate_transition(
                        current_status, new_status, order_id_str
                    )
                    setattr(exchange_order, 'status', new_status.value)
                except Exception as validation_error:
                    logger.error(
                        f"🚫 DYNOPAY_EXCHANGE_BLOCKED: {current_status}→{new_status} for order {exchange_order.id}: {validation_error}"
                    )
                
            elif str(exchange_order.order_type) == "ngn_to_crypto":
                # Process NGN to crypto exchange
                logger.info(f"Processing NGN to crypto exchange: {exchange_order.id}")
                
                # The actual exchange processing logic would go here
                # Mark as processing with validation
                try:
                    current_status = ExchangeStatus(exchange_order.status)
                    new_status = ExchangeStatus.PROCESSING
                    order_id_val = exchange_order.id
                    order_id_str = str(order_id_val) if order_id_val is not None else "unknown"
                    ExchangeStateValidator.validate_transition(
                        current_status, new_status, order_id_str
                    )
                    setattr(exchange_order, 'status', new_status.value)
                except Exception as validation_error:
                    logger.error(
                        f"🚫 DYNOPAY_EXCHANGE_BLOCKED: {current_status}→{new_status} for order {exchange_order.id}: {validation_error}"
                    )
            
            # Notify user of successful payment
            await DynoPayExchangeWebhookHandler._notify_exchange_payment_confirmed(exchange_order, session)
            
        except Exception as e:
            order_id_raw_err = exchange_order.id
            order_id_err: int = int(order_id_raw_err) if order_id_raw_err is not None else 0  # type: ignore[arg-type]
            logger.error(f"Error processing exchange order {order_id_err}: {e}")
            # Mark as failed with validation
            try:
                current_status = ExchangeStatus(exchange_order.status)
                new_status = ExchangeStatus.FAILED
                ExchangeStateValidator.validate_transition(
                    current_status, new_status, str(order_id_err)
                )
                setattr(exchange_order, 'status', new_status.value)
            except Exception as validation_error:
                logger.error(
                    f"🚫 DYNOPAY_EXCHANGE_BLOCKED: {current_status}→{new_status} for order {order_id_err}: {validation_error}"
                )
                # Still set to FAILED even if validation fails - this is error recovery
                setattr(exchange_order, 'status', ExchangeStatus.FAILED.value)

    @staticmethod
    async def _notify_exchange_payment_confirmed(exchange_order: ExchangeOrder, session):
        """Send notifications when exchange payment is confirmed"""
        try:
            import asyncio
            from utils.notification_helpers import send_telegram_message
            
            # Notify user
            # Calculate USD equivalent for display
            usd_equiv_raw = exchange_order.usd_equivalent
            usd_amount: float = float(usd_equiv_raw) if usd_equiv_raw is not None else 0.0  # type: ignore[arg-type]
            usd_display = f" (${float(usd_amount):.2f})" if usd_amount > 0 else ""
            
            user_message = (
                f"✅ Exchange Payment Confirmed!\n\n"
                f"💰 Amount: {exchange_order.source_amount} {str(exchange_order.source_currency)}{usd_display}\n"
                f"🔄 Exchange: {str(exchange_order.source_currency)} → {str(exchange_order.target_currency)}\n"
                f"💵 You'll receive: {exchange_order.target_amount} {str(exchange_order.target_currency)}\n\n"
                f"🔒 Funds secured • Processing in 30 minutes"
            )
            
            try:
                user_id_raw_notify = exchange_order.user_id
                user_id_notify: int = int(user_id_raw_notify) if user_id_raw_notify is not None else 0  # type: ignore[arg-type]
                stmt = select(User).where(User.id == user_id_notify)
                result = await session.execute(stmt)
                user = result.scalar_one_or_none()
                if user:
                    telegram_id_raw = user.telegram_id
                    telegram_id_int: int = int(telegram_id_raw) if telegram_id_raw is not None else 0  # type: ignore[arg-type]
                    # Run async notification in sync context
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.create_task(send_telegram_message(telegram_id_int, user_message))
                        else:
                            loop.run_until_complete(send_telegram_message(telegram_id_int, user_message))
                    except RuntimeError:
                        asyncio.run(send_telegram_message(telegram_id_int, user_message))
            except Exception as e:
                user_id_raw_err_notify = exchange_order.user_id
                user_id_err_notify: int = int(user_id_raw_err_notify) if user_id_raw_err_notify is not None else 0  # type: ignore[arg-type]
                logger.error(f"Failed to notify user {user_id_err_notify}: {e}")
            
            order_id_raw_log = exchange_order.id
            order_id_log: int = int(order_id_raw_log) if order_id_raw_log is not None else 0  # type: ignore[arg-type]
            logger.info(f"Exchange payment confirmation sent for order {order_id_log}")
            
        except Exception as e:
            logger.error(f"Error sending exchange payment confirmation notifications: {e}")

    @staticmethod
    async def validate_exchange_webhook_request(request: Request) -> bool:
        """SECURITY: Validate DynoPay exchange webhook request authenticity with signature verification"""
        try:
            # CRITICAL SECURITY FIX: Add proper signature verification
            from utils.webhook_security import WebhookSecurity
            from config import Config
            import json
            
            # Get raw request body for signature verification
            raw_body = await request.body()
            if not raw_body:
                logger.error("🚨 DYNOPAY_EXCHANGE_SECURITY: Empty request body - rejecting")
                return False
            
            # Parse JSON
            try:
                body = json.loads(raw_body)
            except json.JSONDecodeError as e:
                logger.error(f"🚨 DYNOPAY_EXCHANGE_SECURITY: Invalid JSON in webhook: {e}")
                return False
            
            # Extract signature from headers
            signature = WebhookSecurity.extract_webhook_signature(dict(request.headers), "dynopay")
            
            # PRODUCTION SECURITY: Enforce signature verification in production
            is_production = getattr(Config, "ENVIRONMENT", "development").lower() in ["production", "prod"]
            
            if is_production:
                # Production mode: REQUIRE signature verification
                if not signature:
                    logger.critical("🚨 PRODUCTION_SECURITY_BREACH: No signature header in DynoPay exchange webhook")
                    WebhookSecurity.log_security_violation(
                        "dynopay", 
                        request.client.host if request.client else "unknown",
                        "missing_signature",
                        "Production exchange webhook without signature"
                    )
                    return False
                
                # Verify signature
                is_valid = WebhookSecurity.verify_dynopay_webhook(body, signature)
                if not is_valid:
                    logger.critical("🚨 PRODUCTION_SECURITY_BREACH: DynoPay exchange webhook signature verification FAILED")
                    WebhookSecurity.log_security_violation(
                        "dynopay", 
                        request.client.host if request.client else "unknown",
                        "invalid_signature",
                        f"Exchange webhook - Signature: {signature[:16] if signature else 'None'}..."
                    )
                    return False
                    
                logger.info("✅ PRODUCTION_SECURITY: DynoPay exchange webhook signature verified successfully")
                
            else:
                # Development mode: Allow optional verification with warnings
                if signature:
                    is_valid = WebhookSecurity.verify_dynopay_webhook(body, signature)
                    if is_valid:
                        logger.info("✅ DEV_SECURITY: DynoPay exchange webhook signature verified successfully")
                    else:
                        logger.warning("⚠️ DEV_SECURITY: DynoPay exchange webhook signature verification failed")
                        logger.warning("⚠️ DEV_SECURITY: Processing anyway in development mode")
                else:
                    logger.warning("⚠️ DEV_SECURITY: No signature header found - processing anyway in development")
                    logger.warning("⚠️ CONFIGURE: Set DYNOPAY_WEBHOOK_SECRET environment variable for security")
            
            # Validate required fields after signature verification
            required_fields = ['meta_data', 'paid_amount', 'paid_currency', 'id']
            
            for field in required_fields:
                if field not in body:
                    logger.error(f"DynoPay exchange webhook missing required field: {field}")
                    return False
            
            # Validate it's an exchange operation
            meta_data = body.get('meta_data', {})
            operation_type = meta_data.get('operation_type')
            
            if operation_type not in ['exchange_deposit', 'exchange_switch']:
                logger.error(f"Invalid operation type for exchange webhook: {operation_type}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"DynoPay exchange webhook validation error: {e}")
            return False
    
    @staticmethod
    async def _validate_webhook_request(request: Request) -> bool:
        """
        ARCHITECT REQUIREMENT #2: Test compatibility method for authentication validation
        
        This method provides the interface expected by authentication tests while 
        delegating to the main validation logic.
        """
        return await DynoPayExchangeWebhookHandler.validate_exchange_webhook_request(request)
    
    @staticmethod
    async def _log_webhook_event(
        webhook_data: Dict[str, Any], reference_id: str, transaction_id: str
    ) -> None:
        """Log webhook event for auditing and idempotency using asyncio.to_thread pattern"""
        try:
            import asyncio
            from database import managed_session
            
            def _write():
                """Synchronous DB write function for webhook audit logging"""
                with managed_session() as s:
                    # Check if webhook event already logged (idempotency)
                    existing_event = s.query(WebhookEventLedger).filter_by(
                        event_provider='dynopay',
                        event_id=transaction_id
                    ).first()
                    
                    if existing_event:
                        logger.info(f"📝 WEBHOOK_IDEMPOTENCY: Event {transaction_id} already logged")
                        return
                    
                    # Create webhook event ledger entry
                    webhook_event = WebhookEventLedger(
                        event_provider='dynopay',
                        event_id=transaction_id,
                        event_type='exchange_payment_confirmation',
                        payload=webhook_data,
                        reference_id=reference_id,
                        status='processed'
                    )
                    
                    s.add(webhook_event)
                    s.flush()
                    
                    logger.info(f"📝 WEBHOOK_AUDIT: Logged DynoPay exchange event {transaction_id} for order {reference_id}")
            
            # Execute sync DB operation in thread pool
            await asyncio.to_thread(_write)
                
        except Exception as e:
            logger.error(f"❌ WEBHOOK_AUDIT: Failed to log webhook event {transaction_id}: {e}")
            # Don't raise - webhook processing should continue even if audit logging fails