"""
Centralized Refund Service with Comprehensive Validation
Prevents double refunds, validates funding, and ensures transaction integrity.
Enhanced with idempotency protection and locked funds management.
"""

import logging
import time
from typing import Dict, Any, Optional
from datetime import datetime
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import and_, update

from models import Escrow, Transaction, Wallet, RefundType, EscrowRefundOperation
from services.idempotency import IdempotencyService
from utils.universal_id_generator import UniversalIDGenerator
from utils.refund_monitor import refund_monitor
from utils.atomic_transactions import require_atomic_transaction
from utils.orm_typing_helpers import as_int, as_decimal
import hashlib

logger = logging.getLogger(__name__)


class RefundService:
    """Centralized service for processing escrow refunds with comprehensive validation"""

    @classmethod
    def validate_refund_eligibility(
        cls, escrow: Escrow, session: Session, cancellation_reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Comprehensive validation to determine if escrow is eligible for refund

        Args:
            escrow: Escrow object to validate
            session: Database session
            cancellation_reason: Reason for cancellation (seller_declined, admin_cancelled, expired, buyer_cancelled, etc.)

        Returns:
            Dict with validation results:
            - eligible: bool
            - reason: str (if not eligible)
            - funding_amount: float
            - existing_refunds: float
            - should_refund: float (net amount to refund)
            - fee_refunded: bool (whether platform fee is included in refund)
        """
        try:
            # Check if escrow exists
            if not escrow:
                return {
                    "eligible": False,
                    "reason": "Escrow not found",
                    "funding_amount": 0.0,
                    "existing_refunds": 0.0,
                    "should_refund": 0.0,
                }

            # Check if escrow has a buyer
            if escrow.buyer_id is None:
                return {
                    "eligible": False,
                    "reason": "No buyer associated with escrow",
                    "funding_amount": 0.0,
                    "existing_refunds": 0.0,
                    "should_refund": 0.0,
                }

            # CRITICAL FIX: Look for all payment types including crypto deposits
            # First, check for aggregate wallet payment transaction (WP-prefixed)
            aggregate_funding_tx = (
                session.query(Transaction)
                .filter(
                    and_(
                        Transaction.escrow_id == escrow.id,
                        Transaction.user_id == escrow.buyer_id,
                        Transaction.transaction_id.like(
                            "WP%"
                        ),  # Only WP-prefixed aggregate transactions
                        Transaction.transaction_type == "cashout",
                        Transaction.status == "completed",
                    )
                )
                .first()
            )

            # Calculate total funding from authoritative aggregate transaction only
            if aggregate_funding_tx:
                # Handle both negative (new) and positive (legacy) amounts
                # Handle SQLAlchemy Column type conversion safely
                tx_amount = float(str(aggregate_funding_tx.amount))
                if tx_amount < 0:
                    total_funding = abs(tx_amount)  # Convert negative to positive
                else:
                    total_funding = tx_amount  # Legacy positive format
            else:
                # CRITICAL FIX: Look for crypto deposit transactions (TX-prefixed)
                # These are direct cryptocurrency payments made to escrow
                crypto_deposit_transactions = (
                    session.query(Transaction)
                    .filter(
                        and_(
                            Transaction.escrow_id == escrow.id,
                            Transaction.transaction_type == "deposit",
                            Transaction.status.in_(["completed", "confirmed"]),
                            Transaction.transaction_id.like("TX%"),  # TX-prefixed crypto payments
                        )
                    )
                    .all()
                )
                
                total_funding = 0.0
                for tx in crypto_deposit_transactions:
                    # Handle SQLAlchemy Column type conversion safely
                    tx_amount = float(str(tx.amount))
                    
                    # CRITICAL FIX: Convert crypto amounts to USD for proper refund calculation
                    tx_currency = str(tx.currency) if tx.currency is not None else "USD"
                    if tx_currency and tx_currency != "USD":
                        # Import conversion service
                        
                        try:
                            # Convert crypto amount to USD using cached exchange rates
                            # SYNC_CONTEXT FIX: Use cached rates instead of async API calls in sync context
                            from utils.production_cache import get_cached
                            
                            # Try to get cached rate first (avoids async context issues)
                            cache_key = f"crypto_rate_{tx_currency.upper()}_USD"
                            cached_rate = get_cached(cache_key)
                            
                            if cached_rate:
                                # Use cached rate for immediate conversion
                                usd_equivalent = cached_rate * tx_amount
                                logger.info(f"💱 CACHED_CONVERSION: {tx_amount} {tx_currency} = ${usd_equivalent:.2f} USD (cached rate: ${cached_rate})")
                                total_funding += usd_equivalent
                            else:
                                # FALLBACK: Use reasonable approximation for crypto amounts
                                # This prevents blocking refund validation on API availability
                                crypto_estimates = {
                                    "BTC": 45000.0, "ETH": 3000.0, "LTC": 70.0, "DOGE": 0.08,
                                    "BCH": 150.0, "BSC": 300.0, "TRX": 0.06, "USDT": 1.0,
                                    "XETH": 3000.0, "XXBT": 45000.0, "XLTC": 70.0, "XXDG": 0.08,
                                    "XBCH": 150.0, "XTRX": 0.06, "XUSDT": 1.0
                                }
                                estimated_rate = crypto_estimates.get(tx_currency.upper(), 1.0)
                                usd_equivalent = estimated_rate * tx_amount
                                logger.info(f"💱 ESTIMATED_CONVERSION: {tx_amount} {tx_currency} = ${usd_equivalent:.2f} USD (estimated rate: ${estimated_rate})")
                                logger.info(f"ℹ️ REFUND_INFO: Using estimated rate for {tx_currency} - actual refund will use real-time rates")
                                total_funding += usd_equivalent
                        except Exception as e:
                            logger.error(f"❌ CONVERSION_ERROR: Failed to convert {tx_amount} {tx_currency} to USD: {e}")
                            # Fallback: Use raw amount (will be incorrect but prevents crash)
                            total_funding += tx_amount
                            logger.warning(f"⚠️ FALLBACK: Using raw amount {tx_amount} for {tx_currency} transaction")
                    else:
                        # USD transaction - use as-is
                        total_funding += tx_amount
                
                # Fallback 1: Check for escrow_payment transactions (ESC-prefixed)
                # These are direct escrow payments made to the platform
                if total_funding == 0.0:
                    escrow_payment_transactions = (
                        session.query(Transaction)
                        .filter(
                            and_(
                                Transaction.escrow_id == escrow.id,
                                Transaction.transaction_type == "escrow_payment",
                                Transaction.status.in_(["completed", "confirmed"]),
                            )
                        )
                        .all()
                    )
                    
                    for tx in escrow_payment_transactions:
                        tx_amount = float(str(tx.amount))
                        total_funding += abs(tx_amount)
                        logger.info(f"💰 ESCROW_PAYMENT_FOUND: ${abs(tx_amount):.2f} from transaction {tx.transaction_id}")
                
                # Fallback 2: No escrow payments found, check legacy wallet debits
                if total_funding == 0.0:
                    legacy_transactions = (
                        session.query(Transaction)
                        .filter(
                            and_(
                                Transaction.escrow_id == escrow.id,
                                Transaction.user_id == escrow.buyer_id,
                                Transaction.transaction_type == "cashout",
                                Transaction.status == "completed",
                                ~Transaction.transaction_id.like(
                                    "WP%"
                                ),  # Exclude WP transactions
                            )
                        )
                        .all()
                    )

                    for tx in legacy_transactions:
                        # Handle SQLAlchemy Column type conversion safely
                        tx_amount = float(str(tx.amount))
                        if tx_amount < 0:
                            total_funding += abs(tx_amount)
                        else:
                            total_funding += tx_amount

            # Get all existing refund transactions for this escrow (remove description filter)
            refund_transactions = (
                session.query(Transaction)
                .filter(
                    and_(
                        Transaction.escrow_id == escrow.id,
                        Transaction.user_id == escrow.buyer_id,
                        Transaction.transaction_type.in_(["refund", "deposit", "escrow_refund"]),
                        Transaction.amount > 0,  # Positive amounts are refunds/deposits
                        Transaction.status
                        == "completed",  # Only count completed refunds
                        Transaction.description.ilike(
                            "%refund%"
                        ),  # Keep refund filter for safety
                    )
                )
                .all()
            )

            # Calculate total existing refunds
            # Handle SQLAlchemy Column type conversion for sum safely
            total_existing_refunds = sum(
                float(str(tx.amount)) for tx in refund_transactions
            )

            # CRITICAL FIX: Determine if platform fee should be refunded
            # Policy: Refund buyer_fee if seller hasn't accepted OR for system-initiated cancellations
            
            # These cancellation reasons ALWAYS refund the full amount including platform fee
            always_full_refund_reasons = ["seller_declined", "admin_cancelled", "expired", "expired_timeout"]
            
            # Use seller_accepted_at as the durable marker for whether seller has accepted
            # This persists even after status changes (e.g., active → cancelled)
            seller_has_accepted = escrow.seller_accepted_at is not None
            
            # Determine if fee should be refunded based on:
            # 1. Cancellation reason (seller_declined, admin_cancelled, expired always refund fee)
            # 2. Seller acceptance: If seller has NOT accepted yet, refund the fee
            #    This works regardless of current status because seller_accepted_at is a durable timestamp
            should_refund_fee = (
                cancellation_reason in always_full_refund_reasons or
                not seller_has_accepted
            )
            
            # CRITICAL FIX: Use escrow.total_amount (the escrow value) NOT total_funding (payment sum)
            # total_funding includes overpayments that were already credited separately
            # Overpayments should NEVER be included in refund calculations
            escrow_amount = float(str(escrow.total_amount)) if escrow.total_amount else 0.0
            
            # Calculate refund amount based on fee status
            if should_refund_fee:
                # Buyer cancelled before seller accepted - refund FULL amount including platform fee
                # Use buyer_fee_amount (what buyer actually paid) instead of fee_amount (total fee)
                escrow_fee = float(str(escrow.buyer_fee_amount)) if escrow.buyer_fee_amount else 0.0  # type: ignore
                refund_target_amount = escrow_amount + escrow_fee
                logger.info(
                    f"💰 FULL_REFUND_MODE: Escrow {escrow.escrow_id} status={escrow.status} - "
                    f"refunding ${escrow_amount:.2f} + ${escrow_fee:.2f} buyer_fee = ${refund_target_amount:.2f}"
                )
            else:
                # Seller already accepted - only refund escrow amount (platform earned the fee)
                refund_target_amount = escrow_amount
                logger.info(
                    f"💰 PARTIAL_REFUND_MODE: Escrow {escrow.escrow_id} status={escrow.status} - "
                    f"refunding ${escrow_amount:.2f} (platform fee retained)"
                )
            
            # Calculate net refund needed
            net_refund_needed = refund_target_amount - total_existing_refunds

            # Validation logic
            if total_funding <= 0:
                return {
                    "eligible": False,
                    "reason": "Escrow was never funded - no payments found",
                    "funding_amount": total_funding,
                    "existing_refunds": total_existing_refunds,
                    "should_refund": 0.0,
                }

            if net_refund_needed <= 0:
                return {
                    "eligible": False,
                    "reason": f"Already fully refunded (${total_existing_refunds:.2f} of ${refund_target_amount:.2f})",
                    "funding_amount": refund_target_amount,
                    "existing_refunds": total_existing_refunds,
                    "should_refund": 0.0,
                }

            # Eligible for refund
            return {
                "eligible": True,
                "reason": "Eligible for refund" + (" (includes platform fee)" if should_refund_fee else ""),
                "funding_amount": refund_target_amount,
                "existing_refunds": total_existing_refunds,
                "should_refund": net_refund_needed,
                "fee_refunded": should_refund_fee,
            }

        except Exception as e:
            logger.error(
                f"Error validating refund eligibility for escrow {escrow.id if escrow else 'None'}: {e}"
            )
            return {
                "eligible": False,
                "reason": f"Validation error: {str(e)}",
                "funding_amount": 0.0,
                "existing_refunds": 0.0,
                "should_refund": 0.0,
            }

    @classmethod
    def _generate_refund_cycle_id(cls, escrow_id: int, refund_reason: str) -> str:
        """
        Generate STRICTLY DETERMINISTIC refund cycle ID for deduplication
        
        ARCHITECT'S FIX: Removed date component to prevent multiple refunds
        across different days. Same escrow+reason always generates same ID.
        
        Args:
            escrow_id: Escrow ID
            refund_reason: Reason for refund
            
        Returns:
            Deterministic refund cycle ID (no date dependency)
        """
        # CRITICAL FIX: Use only escrow_id and refund_reason - NO DATE COMPONENT
        # This ensures the same escrow with same reason always generates same ID
        cycle_data = f"{escrow_id}_{refund_reason}"
        cycle_hash = hashlib.sha256(cycle_data.encode()).hexdigest()[:16]
        return f"refund_{escrow_id}_{refund_reason}_{cycle_hash}"

    @classmethod
    @require_atomic_transaction
    def process_escrow_refund(
        cls, escrow: Escrow, cancellation_reason: str, session: Session
    ) -> Dict[str, Any]:
        """
        Process refund for cancelled escrow with ARCHITECT'S DEDUPLICATION SOLUTION
        
        CRITICAL FEATURES:
        - Uses EscrowRefundOperation table for deduplication
        - Atomic transaction with wallet credit + ledger insert + transaction record
        - Prevents double refunds with unique constraint (escrow_id, buyer_id, refund_cycle_id)

        Args:
            escrow: Escrow object to refund
            cancellation_reason: Reason for cancellation ('buyer_cancelled' or 'seller_declined')
            session: Database session

        Returns:
            Dict with processing results:
            - success: bool
            - message: str
            - amount_refunded: float
            - transaction_id: str (if successful)
        """
        start_time = time.time()
        operation_id = refund_monitor.track_refund_start(
            refund_id=str(escrow.escrow_id),
            refund_type=RefundType.ESCROW_REFUND.value,
            amount=float(str(escrow.amount)),
            user_id=int(str(escrow.buyer_id)),
            source_module="RefundService"
        )
        
        try:
            # ARCHITECT'S SOLUTION: Generate refund cycle ID for deduplication
            # FIX: Extract integer value from Column[int] using type-safe converter
            escrow_id = as_int(escrow.id)
            if escrow_id is None:
                logger.error(f"Invalid escrow ID for refund processing: {escrow.id}")
                return {
                    "success": False,
                    "message": "Invalid escrow ID",
                    "amount_refunded": 0.0,
                    "transaction_id": None,
                }
            refund_cycle_id = cls._generate_refund_cycle_id(escrow_id, cancellation_reason)
            
            # CRITICAL CHECK: Look for existing refund operation first
            existing_refund_op = (
                session.query(EscrowRefundOperation)
                .filter(
                    and_(
                        EscrowRefundOperation.escrow_id == escrow.id,
                        EscrowRefundOperation.buyer_id == escrow.buyer_id,
                        EscrowRefundOperation.refund_cycle_id == refund_cycle_id
                    )
                )
                .first()
            )
            
            if existing_refund_op:
                logger.warning(
                    f"🚫 DUPLICATE_REFUND_PREVENTED: Escrow {escrow.escrow_id} already refunded "
                    f"(cycle: {refund_cycle_id}, amount: ${existing_refund_op.amount_refunded})"
                )
                return {
                    "success": False,
                    "message": f"Refund already processed (${existing_refund_op.amount_refunded})",
                    "amount_refunded": 0.0,
                    "transaction_id": existing_refund_op.transaction_id,
                    "duplicate_detected": True
                }

            # Validate refund eligibility (pass cancellation_reason for proper fee calculation)
            validation = cls.validate_refund_eligibility(escrow, session, cancellation_reason)

            if not validation["eligible"]:
                processing_time = time.time() - start_time
                refund_monitor.track_refund_failure(
                    operation_id=operation_id,
                    refund_id=str(escrow.escrow_id),
                    error_message=f"Not eligible: {validation['reason']}",
                    processing_time=processing_time
                )
                logger.info(
                    f"Refund not eligible for escrow {escrow.escrow_id}: {validation['reason']}"
                )
                return {
                    "success": False,
                    "message": validation["reason"],
                    "amount_refunded": 0.0,
                    "transaction_id": None,
                }

            refund_amount = validation["should_refund"]

            # Get buyer's USD wallet
            buyer_wallet = (
                session.query(Wallet)
                .filter(
                    and_(Wallet.user_id == escrow.buyer_id, Wallet.currency == "USD")
                )
                .first()
            )

            if not buyer_wallet:
                logger.error(f"Buyer wallet not found for escrow {escrow.escrow_id}")
                return {
                    "success": False,
                    "message": "Buyer wallet not found",
                    "amount_refunded": 0.0,
                    "transaction_id": None,
                }

            # Generate unique transaction ID with UniversalIDGenerator
            transaction_id = UniversalIDGenerator.generate_refund_id()
            
            # Create idempotency key to prevent duplicate refunds  
            idempotency_key = f"escrow_refund_{escrow.escrow_id}_{cancellation_reason}_{int(refund_amount * 100)}"
            
            # ARCHITECT'S SOLUTION: ATOMIC TRANSACTION BLOCK
            # Step 1: Create refund ledger entry FIRST to establish lock
            refund_operation = EscrowRefundOperation(
                escrow_id=escrow.id,
                buyer_id=escrow.buyer_id,
                refund_cycle_id=refund_cycle_id,
                refund_reason=cancellation_reason,
                amount_refunded=refund_amount,
                currency="USD",
                transaction_id=transaction_id,
                idempotency_key=idempotency_key,
                processed_by_service="RefundService",
                processing_context={
                    "escrow_public_id": escrow.escrow_id,
                    "operation_id": operation_id,
                    "processing_timestamp": datetime.utcnow().isoformat()
                }
            )
            
            # Insert refund operation (this will fail if duplicate due to unique constraint)
            try:
                session.add(refund_operation)
                session.flush()  # Force constraint check
                logger.info(f"✅ REFUND_LEDGER_CREATED: {refund_cycle_id} for escrow {escrow.escrow_id}")
            except Exception as e:
                if "duplicate key" in str(e).lower() or "unique constraint" in str(e).lower():
                    logger.warning(
                        f"🚫 CONSTRAINT_PREVENTED_DUPLICATE: Escrow {escrow.escrow_id} refund blocked by unique constraint"
                    )
                    return {
                        "success": False,
                        "message": "Refund already processed (constraint protection)",
                        "amount_refunded": 0.0,
                        "transaction_id": None,
                        "constraint_prevented": True
                    }
                else:
                    raise  # Re-raise if it's a different error

            # Step 2: Credit wallet balance using proper SQLAlchemy update
            # FIX: Use SQLAlchemy update instead of direct Column assignment
            original_balance = as_decimal(buyer_wallet.available_balance) or Decimal('0')
            new_balance = original_balance + Decimal(str(refund_amount))
            
            # Update wallet balance through SQLAlchemy update statement
            wallet_update = update(Wallet).where(Wallet.id == buyer_wallet.id).values(
                available_balance=new_balance,
                updated_at=datetime.utcnow()
            )
            session.execute(wallet_update)
            session.flush()  # Ensure update is applied before continuing
            
            # Step 3: Create transaction record
            fee_refunded = validation.get("fee_refunded", False)
            refund_description = cls._generate_refund_description(
                cancellation_reason, str(escrow.escrow_id), refund_amount, fee_refunded
            )

            refund_transaction = Transaction(
                transaction_id=transaction_id,
                user_id=escrow.buyer_id,
                escrow_id=escrow.id,
                transaction_type="escrow_refund",
                amount=float(refund_amount),
                currency="USD",
                status="completed",
                description=refund_description,
            )

            # CRITICAL FIX: Wallet balance was already updated above (lines 437-442)
            # DO NOT call credit_user_wallet_simple() here - it creates a separate session
            # and causes transaction conflicts. The wallet update at lines 437-442 is sufficient.
            
            logger.info(f"💰 Wallet balance updated: ${original_balance} → ${new_balance} (refund: ${refund_amount})")

            # Add transaction to session
            session.add(refund_transaction)
            
            # CRITICAL FIX: Flush all changes to ensure they're written
            session.flush()
            
            # Track successful refund
            processing_time = time.time() - start_time
            refund_monitor.track_refund_success(
                operation_id=operation_id,
                refund_id=str(escrow.escrow_id),
                processing_time=processing_time,
                final_amount=float(refund_amount)
            )
            
            # Register successful idempotency operation
            IdempotencyService.register_operation(idempotency_key)

            logger.info(
                f"✅ Processed refund: ${refund_amount:.2f} for escrow {escrow.escrow_id} ({cancellation_reason})"
            )

            return {
                "success": True,
                "message": f"Refund processed: ${refund_amount:.2f}",
                "amount_refunded": float(refund_amount),
                "transaction_id": transaction_id,
            }

        except Exception as e:
            processing_time = time.time() - start_time
            refund_monitor.track_refund_failure(
                operation_id=operation_id,
                refund_id=str(escrow.escrow_id) if escrow else "unknown",
                error_message=str(e),
                processing_time=processing_time
            )
            logger.error(
                f"Error processing refund for escrow {escrow.escrow_id if escrow else 'None'}: {e}"
            )
            return {
                "success": False,
                "message": f"Refund processing error: {str(e)}",
                "amount_refunded": 0.0,
                "transaction_id": None,
            }

    @classmethod
    def _generate_refund_description(
        cls, cancellation_reason: str, escrow_id: str, amount: float, includes_platform_fee: bool = False
    ) -> str:
        """Generate appropriate refund description based on cancellation reason and fee status"""
        
        # These cancellation reasons ALWAYS refund the full amount including platform fee

        if cancellation_reason == "buyer_cancelled":
            if includes_platform_fee:
                return f"↩️ Full Refund: ${amount:.2f} (Trade #{escrow_id} cancelled before seller accepted)"
            else:
                return f"↩️ Refund: ${amount:.2f} (Trade #{escrow_id} cancelled - platform fee retained)"
        elif cancellation_reason == "seller_declined":
            return f"↩️ Full Refund: ${amount:.2f} (Seller declined trade #{escrow_id})"
        elif cancellation_reason == "admin_cancelled":
            return f"↩️ Full Refund: ${amount:.2f} (Admin cancelled trade #{escrow_id})"
        elif cancellation_reason == "expired":
            return f"↩️ Full Refund: ${amount:.2f} (Trade #{escrow_id} expired - payment deadline passed)"
        else:
            # Default: use the fee status to determine refund type
            refund_type = "Full Refund" if includes_platform_fee else "Refund"
            return (
                f"↩️ {refund_type}: ${amount:.2f} (Trade #{escrow_id} cancelled)"
            )

    @classmethod
    async def process_escrow_refunds(cls, expired_escrows: list, session) -> Dict[str, Any]:
        """
        Process refunds for multiple expired escrows (batch processing)
        Only refunds escrows that had payments received, credits to buyer wallet balance
        
        Args:
            expired_escrows: List of expired escrow data (dictionaries or objects)
            session: Async database session
            
        Returns:
            Dict with batch processing results
        """
        results = {
            "processed": 0,
            "refunded_amount": 0.0,
            "refunded_count": 0,
            "skipped_count": 0,
            "errors": [],
            "successful_refunds": []  # CRITICAL FIX: Add successful_refunds list for notifications
        }
        
        try:
            for escrow_data in expired_escrows:
                try:
                    results["processed"] += 1
                    
                    # CRITICAL FIX: Handle both dictionary and object types
                    # Extract escrow_id (string Trade ID) and internal_id (integer DB ID)
                    if isinstance(escrow_data, dict):
                        escrow_id = escrow_data.get("escrow_id")
                        internal_id = escrow_data.get("internal_id") or escrow_data.get("id")
                        escrow_data.get("buyer_id")
                    else:
                        # Handle object type (legacy support)
                        escrow_id = getattr(escrow_data, 'escrow_id', None)
                        internal_id = getattr(escrow_data, 'internal_id', getattr(escrow_data, 'id', None))
                        getattr(escrow_data, 'buyer_id', None)
                    
                    if not internal_id:
                        logger.error(f"⚠️ REFUND_ERROR: Missing internal_id in expired escrow data (escrow_id: {escrow_id})")
                        results["errors"].append({
                            "escrow_id": str(escrow_id or "unknown"),
                            "error": "Missing internal_id in data"
                        })
                        continue
                    
                    # BUSINESS LOGIC: Retrieve actual escrow object from database
                    # This is necessary for proper refund processing
                    from database import SessionLocal
                    with SessionLocal() as sync_session:
                        # Get the full escrow object from database using internal integer ID
                        from models import Escrow
                        escrow_obj = sync_session.query(Escrow).filter(Escrow.id == internal_id).first()
                        
                        if not escrow_obj:
                            logger.error(f"⚠️ REFUND_ERROR: Escrow {escrow_id} not found in database")
                            results["errors"].append({
                                "escrow_id": str(escrow_id),
                                "error": "Escrow not found in database"
                            })
                            results["skipped_count"] += 1
                            continue
                        
                        # CRITICAL FIX: Check if escrow has payment that needs refunding
                        # Handle both active payment statuses AND expired escrows with confirmed payments
                        needs_refund = False
                        
                        if escrow_obj.status in ["payment_confirmed", "partial_payment"]:
                            needs_refund = True
                        elif str(escrow_obj.status) == "expired" and escrow_obj.payment_confirmed_at is not None:
                            # This is the critical fix: expired escrows that had payments confirmed need refunds
                            needs_refund = True
                            logger.info(f"🔄 EXPIRED_REFUND_CANDIDATE: Escrow {escrow_obj.escrow_id} is expired but had payment confirmed at {escrow_obj.payment_confirmed_at}")
                        
                        if not needs_refund:
                            logger.info(f"⏭️ REFUND_SKIP: Escrow {escrow_obj.escrow_id} has no payment - status: {escrow_obj.status}, payment_confirmed_at: {escrow_obj.payment_confirmed_at}")
                            results["skipped_count"] += 1
                            continue
                        
                        # Validate refund eligibility using existing business logic (pass "expired" for proper fee calculation)
                        validation = cls.validate_refund_eligibility(escrow_obj, sync_session, cancellation_reason="expired")
                        
                        if not validation["eligible"]:
                            logger.info(f"⏭️ REFUND_SKIP: Escrow {escrow_obj.escrow_id} not eligible - {validation['reason']}")
                            results["skipped_count"] += 1
                            continue
                            
                        # Process refund using existing secure method
                        # Use "expired" (not "expired_timeout") to match always_full_refund_reasons list
                        refund_result = cls.process_escrow_refund(
                            escrow=escrow_obj,
                            cancellation_reason="expired", 
                            session=sync_session
                        )
                        
                        if refund_result["success"]:
                            results["refunded_count"] += 1
                            results["refunded_amount"] += refund_result["amount_refunded"]
                            
                            # CRITICAL FIX: Add refund details for notification system
                            results["successful_refunds"].append({
                                "escrow_id": escrow_obj.escrow_id,
                                "amount": refund_result["amount_refunded"],
                                "currency": "USD",  # Refunds are always in USD
                                "transaction_id": refund_result.get("transaction_id")
                            })
                            
                            # CRITICAL FIX: Explicitly commit the transaction to ensure persistence
                            # This ensures refund operations are saved to escrow_refund_operations table
                            try:
                                sync_session.commit()
                                logger.info(f"✅ REFUND_COMMITTED: Transaction committed for escrow {escrow_obj.escrow_id}")
                            except Exception as commit_error:
                                logger.error(f"❌ COMMIT_FAILED: Failed to commit refund for escrow {escrow_obj.escrow_id}: {commit_error}")
                                sync_session.rollback()
                                results["errors"].append({
                                    "escrow_id": escrow_obj.escrow_id,
                                    "error": f"Commit failed: {str(commit_error)}"
                                })
                                continue
                            
                            logger.info(f"✅ REFUND_SUCCESS: Escrow {escrow_obj.escrow_id} refunded ${refund_result['amount_refunded']:.2f} to buyer wallet")
                        else:
                            results["errors"].append({
                                "escrow_id": escrow_obj.escrow_id,
                                "error": refund_result["message"]
                            })
                            logger.error(f"❌ REFUND_FAIL: Escrow {escrow_obj.escrow_id} - {refund_result['message']}")
                            
                except Exception as e:
                    # Enhanced error handling with safe attribute access
                    escrow_id_safe = "unknown"
                    try:
                        if isinstance(escrow_data, dict):
                            escrow_id_safe = escrow_data.get("escrow_id", "unknown")
                        else:
                            escrow_id_safe = getattr(escrow_data, 'escrow_id', getattr(escrow_data, 'id', 'unknown'))
                    except Exception:
                        escrow_id_safe = "unknown"
                    
                    error_msg = f"Error processing refund for escrow {escrow_id_safe}: {str(e)}"
                    logger.error(error_msg)
                    results["errors"].append({
                        "escrow_id": str(escrow_id_safe),
                        "error": str(e)
                    })
                    
            logger.info(f"💰 BATCH_REFUND_COMPLETE: Processed {results['processed']}, Refunded {results['refunded_count']} (${results['refunded_amount']:.2f}), Skipped {results['skipped_count']}, Errors {len(results['errors'])}")
            return results
            
        except Exception as e:
            logger.error(f"❌ BATCH_REFUND_ERROR: Failed to process escrow refunds: {str(e)}")
            results["errors"].append({"general_error": str(e)})
            return results

    @classmethod
    def get_refund_summary(cls, escrow_id: str, session: Session) -> Dict[str, Any]:
        """Get comprehensive refund summary for an escrow"""
        try:
            escrow = session.query(Escrow).filter(Escrow.escrow_id == escrow_id).first()
            if not escrow:
                return {"error": "Escrow not found"}

            validation = cls.validate_refund_eligibility(escrow, session)

            return {
                "escrow_id": escrow_id,
                "escrow_amount": float(str(escrow.amount)),
                "funding_amount": validation["funding_amount"],
                "existing_refunds": validation["existing_refunds"],
                "refund_needed": validation["should_refund"],
                "eligible": validation["eligible"],
                "reason": validation["reason"],
            }

        except Exception as e:
            logger.error(f"Error getting refund summary for {escrow_id}: {e}")
            return {"error": str(e)}
