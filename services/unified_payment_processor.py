"""
Unified Payment Processor
Centralized payment processing for all escrow and exchange operations with comprehensive overpayment/underpayment handling
"""

import logging
from decimal import Decimal
from dataclasses import dataclass
from typing import Dict, Any, Optional, cast

from models import Escrow
from services.escrow_fund_manager import EscrowFundManager
from services.enhanced_payment_tolerance_service import enhanced_payment_tolerance
from config import Config

logger = logging.getLogger(__name__)


@dataclass
class ProcessingResult:
    """Result of unified payment processing"""
    success: bool
    escrow_confirmed: bool = False
    overpayment_handled: bool = False
    underpayment_handled: bool = False
    error_message: str = ""
    fund_breakdown: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        if self.fund_breakdown is None:
            self.fund_breakdown = {}


class UnifiedPaymentProcessor:
    """Centralized payment processor with comprehensive overpayment/underpayment logic"""
    
    def __init__(self):
        self.tolerance_usd = Config.UNDERPAYMENT_TOLERANCE_USD
        logger.info(f"UnifiedPaymentProcessor initialized with tolerance: ${self.tolerance_usd}")
    
    async def process_escrow_payment(
        self,
        escrow: Escrow,
        received_amount: Decimal,
        received_usd: Decimal,
        crypto_currency: str,
        tx_hash: str,
        price_usd: Decimal,
        session=None
    ) -> ProcessingResult:
        """
        Process escrow payment with ENHANCED tolerance handling and wallet-based refunds
        
        NEW: Uses dynamic tolerance system and seamless user experience
        
        Args:
            escrow: Escrow object
            received_amount: Amount of crypto received
            received_usd: USD value of received crypto
            crypto_currency: Type of cryptocurrency
            tx_hash: Transaction hash
            price_usd: USD price per crypto unit
            session: Optional existing session for transaction boundary sharing
            
        Returns:
            ProcessingResult with processing outcome
        """
        try:
            # Extract scalar values from escrow object for type safety
            escrow_amount = cast(Decimal, escrow.amount)
            escrow_buyer_id = cast(int, escrow.buyer_id)
            cast(int, escrow.seller_id)
            escrow_currency = cast(str, escrow.currency)
            escrow_id = cast(str, escrow.escrow_id)
            escrow_buyer_fee = cast(Decimal, escrow.buyer_fee_amount)
            
            # CRITICAL FIX: Use STORED buyer_fee_amount instead of recalculating
            # This ensures consistency with what was shown to the user at escrow creation
            # and prevents overpayment miscalculations when MIN_ESCROW_FEE is applied
            expected_amount_crypto = escrow_amount + escrow_buyer_fee
            
            logger.info(
                f"💰 EXPECTED_AMOUNT: Using stored values - Base: ${escrow_amount}, "
                f"Buyer Fee: ${escrow_buyer_fee}, Total: ${expected_amount_crypto}"
            )
            
            # Convert to USD if escrow is crypto
            if escrow_currency == 'USD':
                expected_usd = expected_amount_crypto
            else:
                # Import and use exchange rate service
                from services.fastforex_service import FastForexService
                forex_service = FastForexService()
                crypto_rate = await forex_service.get_crypto_to_usd_rate(escrow_currency)
                expected_usd = Decimal(str(expected_amount_crypto or 0)) * Decimal(str(crypto_rate or 0))
                logger.info(f"Converted {expected_amount_crypto} {escrow_currency} to ${expected_usd:.2f} USD (rate: ${crypto_rate:.2f})")
            
            logger.info(
                f"ENHANCED processing escrow payment {escrow_id}: "
                f"Expected: ${expected_usd:.2f}, Received: ${received_usd:.2f}"
            )
            
            # NEW: Use enhanced payment tolerance system for seamless UX
            tolerance_result = await enhanced_payment_tolerance.process_payment_with_tolerance(
                user_id=escrow_buyer_id,
                expected_amount_usd=expected_usd,
                received_amount_usd=received_usd,
                transaction_id=escrow_id,
                transaction_type="escrow",
                metadata={
                    "crypto_amount": str(received_amount),
                    "crypto_currency": crypto_currency,
                    "tx_hash": tx_hash,
                    "price_usd": price_usd,
                    "buyer_fee_amount": str(escrow_buyer_fee),
                    "escrow_base_amount": str(escrow_amount)
                },
                session=session
            )
            
            logger.info(f"Enhanced tolerance result: {tolerance_result.get('response_type', 'unknown')}")
            
            # Handle different tolerance responses
            if tolerance_result.get("response_type") == "auto_accept":
                # Proceed with escrow processing
                fund_result = await EscrowFundManager.process_escrow_payment(
                    escrow_id=escrow_id,
                    total_received_usd=Decimal(str(received_usd)),
                    expected_total_usd=Decimal(str(expected_usd)),
                    crypto_amount=received_amount,
                    crypto_currency=crypto_currency,
                    tx_hash=tx_hash,
                    session=session,
                    funds_source="external_crypto"
                )
                
                if not fund_result.get('success', False):
                    return ProcessingResult(
                        success=False,
                        error_message=fund_result.get('error', 'Fund processing failed')
                    )
                
                # CRITICAL FIX: Extract and verify holding verification BEFORE returning
                holding_verification = fund_result.get('holding_verification', {})
                holding_verified = holding_verification.get('success', False)
                holding_auto_recovered = holding_verification.get('auto_recovered', False)
                
                # Check if fund processing itself failed
                if not fund_result.get('success', False):
                    return ProcessingResult(
                        success=False,
                        escrow_confirmed=False,
                        error_message=fund_result.get('error', 'Fund processing failed'),
                        fund_breakdown=fund_result.get('fund_breakdown', {})
                    )
                
                # Log holding verification status at processor level
                if holding_verified:
                    if holding_auto_recovered:
                        logger.warning(
                            f"🔧 UNIFIED_PROCESSOR: Holding verification required auto-recovery for {escrow_id}"
                        )
                    else:
                        logger.info(
                            f"✅ UNIFIED_PROCESSOR: Holding verification successful for {escrow_id}"
                        )
                elif holding_auto_recovered:
                    logger.warning(
                        f"✅ UNIFIED_PROCESSOR: Holding auto-recovered successfully for {escrow_id}"
                    )
                else:
                    logger.error(
                        f"❌ UNIFIED_PROCESSOR: Holding verification FAILED for {escrow_id} - "
                        f"Critical issue detected!"
                    )
                
                # FIXED: Use fund_result success flag which accounts for both verification and auto-recovery
                payment_success = fund_result.get('success', False)  # EscrowFundManager handles verification + auto-recovery
                escrow_confirmation = payment_success  # Escrow confirmed if fund processing succeeded (includes auto-recovery)
                
                # Check if tolerance service explicitly set excess_credited
                if "excess_credited" in tolerance_result:
                    # Tolerance service detected overpayment - validate it
                    overpayment_amount = tolerance_result["excess_credited"]
                    
                    # CRITICAL VALIDATION: Ensure the amount is positive
                    try:
                        amount_float = float(overpayment_amount)
                        if amount_float <= 0:
                            logger.error(
                                f"❌ OVERPAYMENT_VALIDATION_FAILED: tolerance service returned "
                                f"excess_credited={overpayment_amount} which is non-positive. "
                                f"Escrow: {escrow.escrow_id}, Tolerance result: {tolerance_result}"
                            )
                            raise ValueError(
                                f"Overpayment validation failed: excess_credited={overpayment_amount} must be positive"
                            )
                    except (TypeError, ValueError) as e:
                        if "must be positive" not in str(e):  # Don't double-wrap our own ValueError
                            logger.error(
                                f"❌ OVERPAYMENT_TYPE_ERROR: excess_credited={overpayment_amount} is not a valid number. "
                                f"Escrow: {escrow_id}, Error: {e}"
                            )
                            raise ValueError(f"Invalid overpayment amount: {overpayment_amount}") from e
                        else:
                            raise  # Re-raise our own validation error
                else:
                    # Tolerance service did not detect overpayment
                    overpayment_amount = 0
                
                return ProcessingResult(
                    success=payment_success,
                    escrow_confirmed=escrow_confirmation,
                    overpayment_handled=bool(overpayment_amount),  # True if overpayment exists
                    error_message="" if payment_success else "Holding verification failed",
                    fund_breakdown={
                        **fund_result.get('fund_breakdown', {}),
                        'holding_verification': holding_verification,
                        'holding_verified': holding_verified,
                        'holding_auto_recovered': holding_auto_recovered,
                        'overpayment_credited': float(overpayment_amount) if overpayment_amount else 0  # BUG FIX #1: Add overpayment amount!
                    }
                )
                
            elif tolerance_result.get("response_type") == "self_service":
                # User gets options - don't process escrow yet
                return ProcessingResult(
                    success=True,
                    escrow_confirmed=False,
                    underpayment_handled=False,
                    error_message="Payment requires user decision - options provided",
                    fund_breakdown={"requires_user_choice": True}
                )
                
            elif tolerance_result.get("response_type") == "auto_refund":
                # Auto-refunded to wallet - escrow not created
                return ProcessingResult(
                    success=True,
                    escrow_confirmed=False,
                    underpayment_handled=True,
                    error_message="Payment refunded to wallet due to significant underpayment",
                    fund_breakdown={
                        "refunded_to_wallet": tolerance_result.get("refund_amount", 0),
                        "auto_refund": True
                    }
                )
            
            else:
                # Fallback to original system if tolerance service fails
                logger.warning("Enhanced tolerance system failed, falling back to original processing")
                return await self._fallback_to_original_processing(
                    escrow, received_amount, received_usd, crypto_currency, tx_hash, price_usd, session
                )
            
        except Exception as e:
            logger.error(f"Error in unified payment processor for {cast(str, escrow.escrow_id)}: {e}")
            return ProcessingResult(
                success=False,
                error_message=f"Processing error: {str(e)}"
            )
    
    async def process_exchange_payment(
        self,
        order_id: int,
        user_id: int,
        received_amount: Decimal,
        expected_amount: Decimal,
        crypto_currency: str,
        usd_rate: Decimal
    ) -> ProcessingResult:
        """
        Process exchange order payment with overpayment/underpayment logic
        
        Args:
            order_id: Exchange order ID
            user_id: User making the payment
            received_amount: Amount of crypto received
            expected_amount: Expected crypto amount
            crypto_currency: Type of cryptocurrency
            usd_rate: USD conversion rate
            
        Returns:
            ProcessingResult with processing outcome
        """
        try:
            # NEW: Use enhanced payment tolerance system for exchanges too
            expected_usd = Decimal(str(expected_amount or 0)) * Decimal(str(usd_rate or 0))
            received_usd = Decimal(str(received_amount or 0)) * Decimal(str(usd_rate or 0))
            
            logger.info(
                f"ENHANCED exchange processing order {order_id}: "
                f"Expected: ${expected_usd:.2f}, Received: ${received_usd:.2f}"
            )
            
            # Apply enhanced payment tolerance to exchange orders
            tolerance_result = await enhanced_payment_tolerance.process_payment_with_tolerance(
                user_id=user_id,
                expected_amount_usd=expected_usd,
                received_amount_usd=received_usd,
                transaction_id=str(order_id),
                transaction_type="exchange",
                metadata={
                    "crypto_amount": str(received_amount),
                    "crypto_currency": crypto_currency,
                    "expected_crypto": str(expected_amount),
                    "usd_rate": str(usd_rate)
                }
            )
            
            logger.info(f"Enhanced tolerance result for exchange: {tolerance_result.get('response_type', 'unknown')}")
            
            # Handle different tolerance responses for exchanges
            if tolerance_result.get("response_type") == "auto_accept":
                # Proceed with exchange processing
                return ProcessingResult(
                    success=True,
                    escrow_confirmed=True,  # Exchange confirmed
                    overpayment_handled=tolerance_result.get("excess_credited", False),
                    fund_breakdown={
                        'expected_amount': Decimal(str(expected_amount or 0)),
                        'received_amount': Decimal(str(received_amount or 0)),
                        'expected_usd': expected_usd,
                        'received_usd': received_usd,
                        'tolerance_applied': True
                    }
                )
                
            elif tolerance_result.get("response_type") == "self_service":
                # User gets options for exchange - don't process yet
                return ProcessingResult(
                    success=True,
                    escrow_confirmed=False,
                    underpayment_handled=False,
                    error_message="Exchange payment requires user decision - options provided",
                    fund_breakdown={"requires_user_choice": True, "exchange_order": True}
                )
                
            elif tolerance_result.get("response_type") == "auto_refund":
                # Auto-refunded to wallet - exchange not processed
                return ProcessingResult(
                    success=True,
                    escrow_confirmed=False,
                    underpayment_handled=True,
                    error_message="Exchange payment refunded to wallet due to significant underpayment",
                    fund_breakdown={
                        "refunded_to_wallet": tolerance_result.get("refund_amount", 0),
                        "auto_refund": True,
                        "exchange_order": True
                    }
                )
            
            else:
                # Fallback to legacy system if tolerance service fails
                logger.warning("Enhanced tolerance system failed for exchange, falling back to legacy processing")
                return await self._fallback_exchange_processing(
                    order_id, user_id, received_amount, expected_amount, crypto_currency, usd_rate
                )
                
        except Exception as e:
            logger.error(f"Error processing exchange payment for order {order_id}: {e}")
            return ProcessingResult(
                success=False,
                error_message=f"Exchange processing error: {str(e)}"
            )


    async def _fallback_to_original_processing(
        self,
        escrow: Escrow,
        received_amount: Decimal,
        received_usd: Decimal,
        crypto_currency: str,
        tx_hash: str,
        price_usd: Decimal,
        session=None
    ) -> ProcessingResult:
        """Fallback to original processing if enhanced system fails"""
        try:
            # Extract scalar values from escrow object for type safety
            escrow_amount = cast(Decimal, escrow.amount)
            escrow_currency = cast(str, escrow.currency)
            
            # Calculate expected payment amount using original method
            # Convert escrow amount to USD
            if escrow_currency == 'USD':
                expected_usd = Decimal(str(escrow_amount or 0))
            else:
                # Get crypto rate and convert
                from services.fastforex_service import FastForexService
                forex_service = FastForexService()
                try:
                    crypto_rate = await forex_service.get_crypto_to_usd_rate(escrow_currency)
                    expected_usd = Decimal(str(escrow_amount or 0)) * Decimal(str(crypto_rate or 0))
                    logger.info(f"Converted {escrow_amount} {escrow_currency} to ${expected_usd:.2f} USD (rate: ${crypto_rate:.2f})")
                except Exception as e:
                    logger.error(f"Failed to get rate for {escrow_currency}: {e}")
                    expected_usd = Decimal('0')
            
            payment_variance = received_usd - expected_usd
            
            logger.info(f"Fallback processing: Expected ${expected_usd:.2f}, Received ${received_usd:.2f}")
            
            # Use original tolerance logic
            if abs(payment_variance) <= self.tolerance_usd:
                # Within tolerance - proceed with escrow
                return ProcessingResult(
                    success=True,
                    escrow_confirmed=True,
                    overpayment_handled=payment_variance > 0,
                    underpayment_handled=payment_variance < 0,
                    fund_breakdown={
                        "base_amount": expected_usd,
                        "variance": payment_variance,
                        "total_processed": received_usd,
                        "fallback_mode": True
                    }
                )
            else:
                # Outside tolerance - reject
                return ProcessingResult(
                    success=False,
                    error_message=f"Payment variance ${abs(payment_variance):.2f} exceeds tolerance ${self.tolerance_usd}"
                )
                
        except Exception as e:
            logger.error(f"Error in fallback processing: {e}")
            return ProcessingResult(
                success=False,
                error_message=f"Fallback processing failed: {str(e)}"
            )

    async def _fallback_exchange_processing(
        self,
        order_id: int,
        user_id: int,
        received_amount: Decimal,
        expected_amount: Decimal,
        crypto_currency: str,
        usd_rate: Decimal
    ) -> ProcessingResult:
        """Fallback to legacy exchange processing if enhanced system fails"""
        try:
            payment_variance = received_amount - expected_amount
            variance_usd = Decimal(str(abs(payment_variance * usd_rate) or 0))
            
            logger.info(f"Fallback exchange processing: Expected {expected_amount}, Received {received_amount}")
            
            # Use original tolerance logic for exchanges
            if variance_usd <= self.tolerance_usd:
                # Within tolerance - proceed with exchange
                return ProcessingResult(
                    success=True,
                    escrow_confirmed=True,
                    overpayment_handled=payment_variance > 0,
                    underpayment_handled=payment_variance < 0,
                    fund_breakdown={
                        "expected_amount": Decimal(str(expected_amount or 0)),
                        "received_amount": Decimal(str(received_amount or 0)),
                        "variance_usd": variance_usd,
                        "total_usd": Decimal(str(received_amount * usd_rate or 0)),
                        "fallback_mode": True
                    }
                )
            else:
                # Outside tolerance - reject
                return ProcessingResult(
                    success=False,
                    error_message=f"Exchange variance ${variance_usd:.2f} exceeds tolerance ${self.tolerance_usd}"
                )
                
        except Exception as e:
            logger.error(f"Error in fallback exchange processing: {e}")
            return ProcessingResult(
                success=False,
                error_message=f"Fallback exchange processing failed: {str(e)}"
            )


# Create singleton instance
unified_processor = UnifiedPaymentProcessor()