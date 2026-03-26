"""
Enhanced Payment Tolerance Service
User-friendly overpayment/underpayment handling with dynamic tolerance and wallet-based refunds
"""

import logging
from decimal import Decimal
from dataclasses import dataclass
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from enum import Enum

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from models import Transaction
from services.crypto import CryptoServiceAtomic
from config import Config

logger = logging.getLogger(__name__)


class PaymentTolerance(Enum):
    """Payment tolerance categories"""
    SMALL_TRANSACTION = "small"      # <$50: 5% tolerance
    MEDIUM_TRANSACTION = "medium"    # $50-$500: 3% tolerance
    LARGE_TRANSACTION = "large"      # >$500: 1% tolerance


class PaymentResponse(Enum):
    """Payment response categories"""
    AUTO_ACCEPT = "auto_accept"           # Within tolerance - proceed
    SELF_SERVICE = "self_service"         # Moderate issue - offer options
    AUTO_REFUND = "auto_refund"          # Significant issue - refund to wallet


@dataclass
class ToleranceResult:
    """Result of tolerance calculation"""
    category: PaymentTolerance
    tolerance_percentage: Decimal
    tolerance_amount_usd: Decimal
    min_acceptable: Decimal
    max_acceptable: Decimal


@dataclass
class PaymentDecision:
    """Decision on how to handle payment variance"""
    response_type: PaymentResponse
    tolerance_result: ToleranceResult
    variance_usd: Decimal
    user_message: str
    action_options: Dict[str, Any]
    requires_notification: bool = True


class EnhancedPaymentToleranceService:
    """
    Enhanced payment tolerance service with dynamic thresholds and wallet-based refunds
    
    Features:
    - Dynamic tolerance based on transaction size
    - Three-tier response system
    - All refunds go to available wallet balance
    - Seamless user notifications with self-service options
    """
    
    # Dynamic tolerance configuration
    TOLERANCE_CONFIG = {
        PaymentTolerance.SMALL_TRANSACTION: {
            "percentage": 5.0,     # 5% tolerance
            "min_amount": 0.50,    # Minimum $0.50
            "max_amount": 2.50,    # Maximum $2.50
            "threshold": 50.0      # <$50 transactions
        },
        PaymentTolerance.MEDIUM_TRANSACTION: {
            "percentage": 3.0,     # 3% tolerance
            "min_amount": 1.00,    # Minimum $1.00
            "max_amount": 15.00,   # Maximum $15.00
            "threshold": 500.0     # $50-$500 transactions
        },
        PaymentTolerance.LARGE_TRANSACTION: {
            "percentage": 1.0,     # 1% tolerance
            "min_amount": 2.00,    # Minimum $2.00
            "max_amount": 10.00,   # Maximum $10.00
            "threshold": None  # >$500 transactions (not used - else clause)
        }
    }
    
    # Self-service recovery thresholds (beyond tolerance but recoverable)
    SELF_SERVICE_MULTIPLIER = Decimal("2.0")  # 2x tolerance for self-service options
    
    @classmethod
    def calculate_dynamic_tolerance(cls, expected_amount_usd) -> ToleranceResult:
        """
        Calculate dynamic tolerance based on transaction size
        
        Args:
            expected_amount_usd: Expected payment amount in USD (accepts float or Decimal)
            
        Returns:
            ToleranceResult with tolerance details
        """
        # Convert to Decimal for precise financial calculations
        expected_decimal = Decimal(str(expected_amount_usd or 0))
        
        # Determine transaction category
        threshold_small = Decimal(str(cls.TOLERANCE_CONFIG[PaymentTolerance.SMALL_TRANSACTION]["threshold"]))
        threshold_medium = Decimal(str(cls.TOLERANCE_CONFIG[PaymentTolerance.MEDIUM_TRANSACTION]["threshold"]))
        
        if expected_decimal < threshold_small:
            category = PaymentTolerance.SMALL_TRANSACTION
        elif expected_decimal < threshold_medium:
            category = PaymentTolerance.MEDIUM_TRANSACTION
        else:
            category = PaymentTolerance.LARGE_TRANSACTION
        
        config = cls.TOLERANCE_CONFIG[category]
        
        # Calculate percentage-based tolerance using Decimal arithmetic
        percentage = Decimal(str(config["percentage"]))
        percentage_tolerance = expected_decimal * (percentage / Decimal("100"))
        
        # Apply min/max bounds using Decimal
        min_amount = Decimal(str(config["min_amount"]))
        max_amount = Decimal(str(config["max_amount"]))
        tolerance_amount = max(
            min_amount,
            min(percentage_tolerance, max_amount)
        )
        
        return ToleranceResult(
            category=category,
            tolerance_percentage=percentage,
            tolerance_amount_usd=tolerance_amount,
            min_acceptable=expected_decimal - tolerance_amount,
            max_acceptable=expected_decimal + tolerance_amount
        )
    
    @classmethod
    def analyze_payment_variance(
        cls, 
        expected_amount_usd, 
        received_amount_usd,
        transaction_type: str = "escrow"
    ) -> PaymentDecision:
        """
        Analyze payment variance and determine response strategy
        
        Args:
            expected_amount_usd: Expected payment amount
            received_amount_usd: Actual received amount
            transaction_type: Type of transaction (escrow/exchange)
            
        Returns:
            PaymentDecision with recommended action
        """
        # Convert to Decimal for precise financial calculations
        expected_decimal = Decimal(str(expected_amount_usd or 0))
        received_decimal = Decimal(str(received_amount_usd or 0))
        
        tolerance_result = cls.calculate_dynamic_tolerance(expected_decimal)
        variance_usd = received_decimal - expected_decimal
        
        # MINIMUM_OVERPAYMENT_THRESHOLD: Only credit overpayments >= $0.10 to avoid rounding noise
        MINIMUM_OVERPAYMENT_THRESHOLD = Decimal("0.10")
        
        # Case 1: Exact payment (within 1 cent) - CHECK FIRST to avoid rounding issues
        if abs(variance_usd) < Decimal("0.01"):
            return PaymentDecision(
                response_type=PaymentResponse.AUTO_ACCEPT,
                tolerance_result=tolerance_result,
                variance_usd=Decimal("0"),  # Treat as exact - no variance shown
                user_message=f"✅ Payment confirmed: ${received_decimal:.2f}",
                action_options={"proceed": True},
                requires_notification=True
            )
        
        # Case 2: Significant overpayment (>= $0.10) - credit to wallet
        if variance_usd >= MINIMUM_OVERPAYMENT_THRESHOLD:
            return PaymentDecision(
                response_type=PaymentResponse.AUTO_ACCEPT,
                tolerance_result=tolerance_result,
                variance_usd=variance_usd,
                user_message=f"✅ Payment received: ${received_decimal:.2f} (${variance_usd:.2f} excess credited to your wallet)",
                action_options={
                    "proceed": True,
                    "excess_credited": variance_usd
                }
            )
        
        # Case 3: Tiny overpayment ($0.01 - $0.09) - treat as exact, don't credit rounding noise
        if variance_usd > 0:
            logger.info(
                f"💰 TOLERANCE_TINY_OVERPAYMENT: ${variance_usd:.4f} overpayment below threshold "
                f"(${MINIMUM_OVERPAYMENT_THRESHOLD}) - treating as exact payment"
            )
            return PaymentDecision(
                response_type=PaymentResponse.AUTO_ACCEPT,
                tolerance_result=tolerance_result,
                variance_usd=Decimal("0"),  # Don't show tiny variance
                user_message=f"✅ Payment confirmed: ${received_decimal:.2f}",
                action_options={"proceed": True},
                requires_notification=True
            )
        
        # Case 4: Underpayment analysis
        shortage_amount = abs(variance_usd)
        
        # Within tolerance - auto-accept
        if received_decimal >= tolerance_result.min_acceptable:
            return PaymentDecision(
                response_type=PaymentResponse.AUTO_ACCEPT,
                tolerance_result=tolerance_result,
                variance_usd=variance_usd,
                user_message=f"✅ Payment accepted: ${received_decimal:.2f} (${shortage_amount:.2f} short but within tolerance)",
                action_options={
                    "proceed": True,
                    "reduced_amount": received_decimal,
                    "shortage_amount": shortage_amount
                }
            )
        
        # FIXED: ALWAYS show action buttons for ANY underpayment (no threshold)
        # User should always have choice: Proceed Partial or Cancel & Refund
        # NOTE: escrow_amount for proceed_partial will be calculated later with buyer_fee deduction
        return PaymentDecision(
            response_type=PaymentResponse.SELF_SERVICE,
            tolerance_result=tolerance_result,
            variance_usd=variance_usd,
            user_message=(
                f"Expected: ${expected_decimal:.2f}\n"
                f"Received: ${received_decimal:.2f} ✅\n"
                f"Shortage: ${shortage_amount:.2f}"
            ),
            action_options={
                "complete_payment": {
                    "amount_needed": shortage_amount,
                    "timeout_minutes": 10
                },
                "proceed_partial": {
                    "escrow_amount": received_decimal  # Will be adjusted with buyer_fee in process_payment_with_tolerance
                },
                "cancel_refund": {
                    "refund_amount": received_decimal,
                    "refund_destination": "wallet"
                }
            }
        )
    
    @classmethod
    async def process_payment_with_tolerance(
        cls,
        user_id: int,
        expected_amount_usd,
        received_amount_usd,
        transaction_id: str,
        transaction_type: str = "escrow",
        metadata: Optional[Dict[str, Any]] = None,
        session: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """
        Process payment with enhanced tolerance handling and wallet-based refunds
        
        Args:
            user_id: User ID
            expected_amount_usd: Expected payment amount
            received_amount_usd: Actual received amount
            transaction_id: Transaction/escrow ID
            transaction_type: Type of transaction
            metadata: Additional transaction metadata
            session: Database session (required for wallet operations)
            
        Returns:
            Processing result with user-friendly response
        """
        try:
            # BUG FIX #2: Log input values at start for debugging
            logger.info(
                f"🔍 TOLERANCE_START: Processing {transaction_type} {transaction_id} - "
                f"Expected: ${expected_amount_usd:.2f}, Received: ${received_amount_usd:.2f}"
            )
            
            # Analyze payment variance
            decision = cls.analyze_payment_variance(
                expected_amount_usd, received_amount_usd, transaction_type
            )
            
            # BUG FIX #2: Comprehensive logging of payment decision
            variance_usd = Decimal(str(received_amount_usd or 0)) - Decimal(str(expected_amount_usd or 0))
            if variance_usd > 0:
                logger.info(
                    f"💰 OVERPAYMENT_DETECTED: {transaction_type} {transaction_id} - "
                    f"Overpayment: ${variance_usd:.2f}, Will credit to wallet"
                )
            elif variance_usd < 0:
                logger.info(
                    f"⚠️ UNDERPAYMENT_DETECTED: {transaction_type} {transaction_id} - "
                    f"Shortage: ${abs(variance_usd):.2f}, Response: {decision.response_type.value}"
                )
            else:
                logger.info(
                    f"✅ EXACT_PAYMENT: {transaction_type} {transaction_id} - "
                    f"Received exact amount: ${received_amount_usd:.2f}"
                )
            
            logger.info(
                f"📊 TOLERANCE_DECISION: {transaction_type} {transaction_id} - "
                f"Response: {decision.response_type.value}, Variance: ${variance_usd:.2f}"
            )
            
            # Execute decision
            if decision.response_type == PaymentResponse.AUTO_ACCEPT:
                result = await cls._handle_auto_accept(
                    user_id, decision, transaction_id, transaction_type, metadata or {}, session
                )
            elif decision.response_type == PaymentResponse.SELF_SERVICE:
                enriched_metadata = {
                    **(metadata or {}),
                    "expected_amount_usd": expected_amount_usd,
                    "received_amount_usd": received_amount_usd
                }
                result = await cls._handle_self_service_options(
                    user_id, decision, transaction_id, transaction_type, enriched_metadata, session
                )
            else:  # AUTO_REFUND
                result = await cls._handle_auto_refund_to_wallet(
                    user_id, decision, transaction_id, transaction_type, metadata or {}, session
                )
            
            # Send user notification if required
            if decision.requires_notification:
                await cls._send_payment_notification(user_id, decision, result)
            
            return result
            
        except Exception as e:
            logger.error(f"Error processing payment tolerance for {transaction_id}: {e}")
            return {
                "success": False,
                "error": str(e),
                "response_type": "error"
            }
    
    @classmethod
    async def _handle_auto_accept(
        cls, user_id: int, decision: PaymentDecision, 
        transaction_id: str, transaction_type: str, metadata: Dict[str, Any],
        session: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """Handle auto-accept scenarios (overpayment or within tolerance)"""
        try:
            result = {
                "success": True,
                "response_type": "auto_accept",
                "user_message": decision.user_message,
                "action_taken": "processed",
                "transaction_type": transaction_type,
                "escrow_id": transaction_id,
                "transaction_id": transaction_id
            }
            
            # CRITICAL FIX: Extract escrow data INSIDE session block for notification
            if transaction_type == "escrow" and session:
                from models import Escrow
                from sqlalchemy import select
                from sqlalchemy.orm import selectinload
                
                stmt = select(Escrow).options(
                    selectinload(Escrow.seller)
                ).where(Escrow.escrow_id == transaction_id)
                escrow_query_result = await session.execute(stmt)
                escrow_record = escrow_query_result.scalar_one_or_none()
                
                if escrow_record:
                    # Get seller display name
                    seller_display = "Seller"
                    if escrow_record.seller:
                        if escrow_record.seller.username:
                            seller_display = f"@{escrow_record.seller.username}"
                        elif escrow_record.seller.first_name:
                            seller_display = escrow_record.seller.first_name
                    
                    # Format delivery deadline
                    delivery_text = "Not set"
                    if escrow_record.delivery_deadline:
                        deadline = escrow_record.delivery_deadline
                        if isinstance(deadline, datetime):
                            delivery_text = deadline.strftime("%b %d, %Y %I:%M %p UTC")
                    
                    result["escrow_data"] = {
                        "escrow_id": escrow_record.escrow_id,
                        "amount": float(escrow_record.amount),
                        "currency": escrow_record.currency,
                        "seller_display": seller_display,
                        "delivery_deadline": delivery_text
                    }
                    logger.debug(f"✅ Escrow data extracted for notification: {transaction_id}")
            
            # Handle overpayment - credit excess to wallet
            if "excess_credited" in decision.action_options:
                excess_amount = decision.action_options["excess_credited"]
                
                # CRITICAL FIX: Look up escrow database ID for transaction linking (for escrow transactions only)
                escrow_db_id = None
                if transaction_type == "escrow":
                    from models import Escrow
                    from sqlalchemy import select
                    
                    stmt = select(Escrow).where(Escrow.escrow_id == transaction_id)
                    escrow_query_result = await session.execute(stmt)
                    escrow_record = escrow_query_result.scalar_one_or_none()
                    
                    if escrow_record:
                        escrow_db_id = escrow_record.id
                        logger.info(f"✅ Escrow lookup success: {transaction_id} → DB ID {escrow_db_id}")
                    else:
                        logger.error(
                            f"❌ ESCROW_LOOKUP_FAILED: Cannot find escrow {transaction_id} for overpayment credit. "
                            f"This will cause constraint violation!"
                        )
                        # Cannot proceed without escrow_id for escrow_overpayment type
                        return {
                            "success": False,
                            "error": f"Escrow {transaction_id} not found in database",
                            "response_type": "error"
                        }
                
                # IDEMPOTENCY FIX: Check if overpayment transaction already exists (webhook retry safety)
                from sqlalchemy import select, and_
                existing_overpayment_stmt = select(Transaction).where(
                    and_(
                        Transaction.user_id == user_id,
                        Transaction.escrow_id == escrow_db_id,
                        Transaction.transaction_type == f"{transaction_type}_overpayment",
                        Transaction.amount == excess_amount,
                        Transaction.status == "completed"
                    )
                )
                existing_overpayment = (await session.execute(existing_overpayment_stmt)).scalar_one_or_none()
                
                if existing_overpayment:
                    logger.warning(
                        f"⚠️ IDEMPOTENT_SKIP: Overpayment transaction already exists for user {user_id}, "
                        f"escrow {transaction_id} (DB ID {escrow_db_id}), amount ${excess_amount:.2f}. "
                        f"Skipping duplicate credit (webhook retry)."
                    )
                    result["excess_credited"] = excess_amount
                    result["wallet_credited"] = True
                    result["idempotent_skip"] = True
                    logger.info(f"✅ IDEMPOTENT_REUSED: Overpayment ${excess_amount:.2f} already credited on previous attempt")
                else:
                    try:
                        # BUG FIX #2: Log before attempting wallet credit
                        logger.info(
                            f"💳 WALLET_CREDIT_ATTEMPT: Crediting ${excess_amount:.2f} overpayment to user {user_id} wallet "
                            f"for {transaction_type} {transaction_id}"
                        )
                        
                        credit_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                            user_id=user_id,
                            amount=excess_amount,
                            currency="USD",
                            escrow_id=escrow_db_id,  # CRITICAL FIX: Pass escrow database ID for constraint compliance
                            transaction_type=f"{transaction_type}_overpayment",
                            description=f"Overpayment credit from {transaction_type} {transaction_id}: +${excess_amount:.2f}",
                            session=session
                        )
                        
                        if credit_success:
                            result["excess_credited"] = excess_amount
                            result["wallet_credited"] = True
                            # BUG FIX #2: Enhanced success logging
                            logger.info(
                                f"✅ WALLET_CREDIT_SUCCESS: Credited ${excess_amount:.2f} overpayment to user {user_id} wallet "
                                f"for {transaction_type} {transaction_id}"
                            )
                        else:
                            # BUG FIX #2: Enhanced failure logging
                            logger.error(
                                f"❌ WALLET_CREDIT_FAILED: Failed to credit ${excess_amount:.2f} overpayment to user {user_id} wallet "
                                f"for {transaction_type} {transaction_id}"
                            )
                            result["wallet_credited"] = False
                    except IntegrityError as e:
                        # Check if this is the overpayment constraint violation
                        if "ix_unique_escrow_overpayment" in str(e):
                            logger.warning(
                                f"⚠️ INTEGRITY_CONSTRAINT_IDEMPOTENT: Overpayment transaction already exists for user {user_id}, "
                                f"escrow {transaction_id} (DB ID {escrow_db_id}), amount ${excess_amount:.2f}. "
                                f"Wallet was credited in previous attempt but transaction record creation failed. "
                                f"Treating as idempotent success (webhook retry)."
                            )
                            result["excess_credited"] = excess_amount
                            result["wallet_credited"] = True
                            result["idempotent_skip"] = True
                            logger.info(f"✅ INTEGRITY_IDEMPOTENT_REUSED: Overpayment ${excess_amount:.2f} already credited in previous attempt")
                        else:
                            # Different IntegrityError, re-raise
                            logger.error(f"Unexpected IntegrityError during overpayment credit: {e}")
                            raise
            
            return result
            
        except Exception as e:
            logger.error(f"Error in auto-accept handling: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    async def _handle_self_service_options(
        cls, user_id: int, decision: PaymentDecision, 
        transaction_id: str, transaction_type: str, metadata: Dict[str, Any],
        session: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """Handle self-service recovery options"""
        try:
            # Store self-service session with SECURE validation data
            from services.state_manager import StateManager
            import hmac
            import hashlib
            
            # CRITICAL FIX: Extract amounts from metadata and convert Decimal to float
            from decimal import Decimal
            expected_amount_usd = float(metadata.get("expected_amount_usd", 0))
            received_amount_usd = float(metadata.get("received_amount_usd", 0))
            
            # CRITICAL FIX: Adjust escrow_amount for partial payment by subtracting buyer fee
            # When user proceeds with partial payment, they should only get (received - buyer_fee) as escrow
            adjusted_action_options = decision.action_options.copy()
            if "proceed_partial" in adjusted_action_options and "buyer_fee_amount" in metadata:
                buyer_fee = Decimal(str(metadata.get("buyer_fee_amount", 0)))
                received_dec = Decimal(str(received_amount_usd))
                
                # Calculate correct escrow amount: received - buyer_fee
                correct_escrow_amount = max(Decimal("0"), received_dec - buyer_fee)
                adjusted_action_options["proceed_partial"]["escrow_amount"] = correct_escrow_amount
                
                logger.info(
                    f"💰 ADJUSTED_ESCROW_AMOUNT: For partial payment {transaction_id} - "
                    f"Received: ${received_dec:.2f}, Buyer Fee: ${buyer_fee:.2f}, "
                    f"Escrow Amount: ${correct_escrow_amount:.2f}"
                )
            
            # Helper function to convert Decimal to float recursively
            def convert_decimals(obj):
                if isinstance(obj, Decimal):
                    return float(obj)
                elif isinstance(obj, dict):
                    return {k: convert_decimals(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [convert_decimals(item) for item in obj]
                return obj
            
            # Create secure session with server-side validation data (all Decimals converted to float)
            session_data = {
                "transaction_id": transaction_id,
                "transaction_type": transaction_type,
                "user_id": user_id,  # SECURITY: Validate user ownership
                "original_expected_usd": expected_amount_usd,  # SECURITY: Server-side expected amount
                "original_received_usd": received_amount_usd,  # SECURITY: Server-side received amount
                "calculated_variance": float(decision.variance_usd),  # SECURITY: Server-calculated variance
                "authorized_actions": convert_decimals(adjusted_action_options),  # SECURITY: Only these actions allowed (with buyer_fee adjustment)
                "decision": {
                    "variance_usd": float(decision.variance_usd),
                    "action_options": convert_decimals(adjusted_action_options),  # Use adjusted options with correct escrow_amount
                    "user_message": decision.user_message
                },
                "created_at": datetime.utcnow().isoformat(),
                "expires_at": (datetime.utcnow() + timedelta(minutes=10)).isoformat(),
                # SECURITY: Add signature to prevent tampering
                "signature": hmac.new(
                    Config.SECRET_KEY.encode() if hasattr(Config, 'SECRET_KEY') else b'default_key',
                    f"{user_id}:{transaction_id}:{expected_amount_usd}:{received_amount_usd}".encode(),
                    hashlib.sha256
                ).hexdigest()
            }
            
            state_manager = StateManager()
            session_key = f"payment_recovery_{user_id}_{transaction_id}"
            await state_manager.set_state(session_key, session_data, ttl=600)  # 10 minutes
            logger.info(f"SECURE self-service session created: {session_key}")
            
            result = {
                "success": True,
                "response_type": "self_service",
                "user_message": decision.user_message,
                "action_options": decision.action_options,
                "session_id": f"payment_recovery_{user_id}_{transaction_id}",
                "requires_user_choice": True,
                "transaction_type": transaction_type,
                "escrow_id": transaction_id,
                "transaction_id": transaction_id
            }
            
            # CRITICAL FIX: Extract escrow data INSIDE session block for notification
            if transaction_type == "escrow" and session:
                from models import Escrow
                from sqlalchemy import select
                from sqlalchemy.orm import selectinload
                
                stmt = select(Escrow).options(
                    selectinload(Escrow.seller)
                ).where(Escrow.escrow_id == transaction_id)
                escrow_query_result = await session.execute(stmt)
                escrow_record = escrow_query_result.scalar_one_or_none()
                
                if escrow_record:
                    # Get seller display name
                    seller_display = "Seller"
                    if escrow_record.seller:
                        if escrow_record.seller.username:
                            seller_display = f"@{escrow_record.seller.username}"
                        elif escrow_record.seller.first_name:
                            seller_display = escrow_record.seller.first_name
                    
                    # Format delivery deadline
                    delivery_text = "Not set"
                    if escrow_record.delivery_deadline:
                        deadline = escrow_record.delivery_deadline
                        if isinstance(deadline, datetime):
                            delivery_text = deadline.strftime("%b %d, %Y %I:%M %p UTC")
                    
                    result["escrow_data"] = {
                        "escrow_id": escrow_record.escrow_id,
                        "amount": float(escrow_record.amount),
                        "currency": escrow_record.currency,
                        "seller_display": seller_display,
                        "delivery_deadline": delivery_text
                    }
                    logger.debug(f"✅ Escrow data extracted for notification: {transaction_id}")
            
            return result
            
        except Exception as e:
            logger.error(f"Error in self-service handling: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    async def _handle_auto_refund_to_wallet(
        cls, user_id: int, decision: PaymentDecision, 
        transaction_id: str, transaction_type: str, metadata: Dict[str, Any],
        session: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """Handle automatic refund to wallet balance"""
        try:
            refund_amount = decision.action_options["auto_refund"]["refund_amount"]
            
            refund_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                user_id=user_id,
                amount=refund_amount,
                currency="USD",
                transaction_type=f"{transaction_type}_underpay_refund",
                description=f"Auto-refund for {transaction_type} {transaction_id}: underpayment too large (${abs(decision.variance_usd):.2f} short)",
                session=session
            )
            
            if not refund_success:
                logger.error(f"Failed to refund ${refund_amount:.2f} to user {user_id} wallet")
                return {
                    "success": False,
                    "error": "Failed to process wallet refund"
                }
            
            logger.info(f"Auto-refunded ${refund_amount:.2f} to user {user_id} wallet for underpayment")
            
            result = {
                "success": True,
                "response_type": "auto_refund",
                "user_message": decision.user_message,
                "refund_amount": refund_amount,
                "refund_destination": "wallet",
                "action_taken": "refunded_to_wallet",
                "restart_options": decision.action_options.get("restart_payment", {}),
                "transaction_type": transaction_type,
                "escrow_id": transaction_id,
                "transaction_id": transaction_id
            }
            
            # CRITICAL FIX: Extract escrow data INSIDE session block for notification
            if transaction_type == "escrow" and session:
                from models import Escrow
                from sqlalchemy import select
                from sqlalchemy.orm import selectinload
                
                stmt = select(Escrow).options(
                    selectinload(Escrow.seller)
                ).where(Escrow.escrow_id == transaction_id)
                escrow_query_result = await session.execute(stmt)
                escrow_record = escrow_query_result.scalar_one_or_none()
                
                if escrow_record:
                    # Get seller display name
                    seller_display = "Seller"
                    if escrow_record.seller:
                        if escrow_record.seller.username:
                            seller_display = f"@{escrow_record.seller.username}"
                        elif escrow_record.seller.first_name:
                            seller_display = escrow_record.seller.first_name
                    
                    # Format delivery deadline
                    delivery_text = "Not set"
                    if escrow_record.delivery_deadline:
                        deadline = escrow_record.delivery_deadline
                        if isinstance(deadline, datetime):
                            delivery_text = deadline.strftime("%b %d, %Y %I:%M %p UTC")
                    
                    result["escrow_data"] = {
                        "escrow_id": escrow_record.escrow_id,
                        "amount": float(escrow_record.amount),
                        "currency": escrow_record.currency,
                        "seller_display": seller_display,
                        "delivery_deadline": delivery_text
                    }
                    logger.debug(f"✅ Escrow data extracted for notification: {transaction_id}")
            
            return result
                
        except Exception as e:
            logger.error(f"Error in auto-refund handling: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    async def _send_payment_notification(
        cls, user_id: int, decision: PaymentDecision, result: Dict[str, Any]
    ):
        """
        Send user-friendly payment notification with escrow context
        
        CRITICAL: This method runs OUTSIDE the async session block (following wallet webhook pattern).
        All data must be extracted and passed via the result dict - NO database queries allowed here.
        """
        try:
            # Extract escrow information from result dict (pre-extracted by handler methods)
            transaction_type = result.get("transaction_type", "payment")
            escrow_data = result.get("escrow_data", {})
            
            # Create detailed notification message matching normal escrow flow
            if decision.response_type == PaymentResponse.AUTO_ACCEPT:
                # DISABLED: For escrow payments, buyer payment confirmation is sent by DynoPay webhook handler
                # This prevents duplicate notifications and ensures consistent formatting
                if transaction_type == "escrow" and escrow_data:
                    # Skip notification - buyer confirmation handled by dynopay_webhook.py
                    # Only log the overpayment credit here
                    if result.get("excess_credited"):
                        logger.info(f"💰 Overpayment handled: ${result['excess_credited']:.2f} credited to user {user_id} wallet for escrow {escrow_data.get('escrow_id')}")
                    return  # Exit early - no notification needed
                else:
                    # Non-escrow payment confirmation (fallback)
                    message = f"✅ Payment Confirmed\n\n{decision.user_message}\n\n⏳ Processing"
                    if result.get("excess_credited"):
                        message += f"\n\n💰 ${result['excess_credited']:.2f} → wallet"
                    
            elif decision.response_type == PaymentResponse.SELF_SERVICE:
                # Compact, mobile-friendly display
                if transaction_type == "escrow" and escrow_data:
                    escrow_id = escrow_data.get('escrow_id', 'N/A')
                    escrow_line = f"{escrow_id}\n"
                else:
                    escrow_line = ""
                
                # Get the reduced escrow amount for clear explanation
                escrow_amount = decision.action_options['proceed_partial']['escrow_amount']
                refund_amount = decision.action_options['cancel_refund']['refund_amount']
                
                message = "💳 Payment Update\n\n"
                message += f"{escrow_line}"
                message += f"{decision.user_message}\n\n"
                message += f"📉 Proceed: ${float(escrow_amount):.2f} escrow\n"
                message += f"🔄 Cancel: ${float(refund_amount):.2f} to wallet\n\n"
                message += "⏰ Choose within 10 min"
                
            else:  # AUTO_REFUND
                # Use pre-extracted escrow data for compact display
                escrow_info = ""
                if transaction_type == "escrow" and escrow_data:
                    escrow_info = f"Escrow: {escrow_data.get('escrow_id', 'N/A')}\n"
                
                message = f"🔄 Payment Refunded\n{escrow_info}{decision.user_message}\n\n"
                message += f"💰 ${result['refund_amount']:.2f} added to your wallet\n\n"
                message += f"🔄 Ready to retry? Pay ${result['restart_options']['correct_amount']:.2f} to continue"
            
            # Generate idempotency key to prevent duplicate notifications
            escrow_id = result.get("escrow_id") or result.get("transaction_id")
            idempotency_key = f"escrow_{escrow_id}_{decision.response_type.value}_payment_notification"
            
            # Create inline keyboard buttons for self-service options
            template_data = {"parse_mode": None}  # Disable Markdown parsing to avoid Telegram errors
            if decision.response_type == PaymentResponse.SELF_SERVICE:
                transaction_id = result.get("transaction_id", "")
                escrow_amount = decision.action_options['proceed_partial']['escrow_amount']
                refund_amount = decision.action_options['cancel_refund']['refund_amount']
                
                # Create compact, mobile-friendly buttons
                template_data["keyboard"] = [
                    [{"text": f"📉 Proceed (${float(escrow_amount):.2f})", 
                      "callback_data": f"pay_partial:{transaction_id}:{float(escrow_amount):.2f}"}],
                    [{"text": f"🔄 Cancel (${float(refund_amount):.2f} to wallet)", 
                      "callback_data": f"pay_cancel:{transaction_id}:{float(refund_amount):.2f}"}]
                ]
            
            # Send via consolidated notification service with BROADCAST MODE
            try:
                from services.consolidated_notification_service import (
                    consolidated_notification_service, NotificationRequest, 
                    NotificationCategory, NotificationPriority
                )
                request = NotificationRequest(
                    user_id=user_id,
                    title="💳 Escrow Payment Update" if transaction_type == "escrow" else "💳 Payment Update",
                    message=message,
                    category=NotificationCategory.PAYMENTS,
                    priority=NotificationPriority.HIGH,
                    broadcast_mode=True,  # Send to BOTH Telegram AND Email
                    idempotency_key=idempotency_key,  # Prevent duplicates
                    template_data=template_data if template_data else None  # Add keyboard for self-service
                )
                await consolidated_notification_service.send_notification(request)
                logger.info(f"✅ Enhanced payment notification sent to user {user_id} ({decision.response_type.value}) - broadcast_mode=True, idempotency_key={idempotency_key}")
            except Exception as e:
                logger.error(f"Failed to send notification via consolidated service: {e}")
                # Fallback to basic logging
                logger.info(f"Payment notification for user {user_id}: {message}")
            
        except Exception as e:
            logger.error(f"Error sending payment notification to user {user_id}: {e}")


# Global instance for easy import
enhanced_payment_tolerance = EnhancedPaymentToleranceService()