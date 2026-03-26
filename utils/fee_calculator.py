"""Comprehensive fee calculation utilities for escrow transactions"""

import logging
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Tuple, Dict, Optional, Any
from config import Config
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class FeeCalculator:
    """Handles all fee-related calculations with mathematical precision"""

    # Precision settings from environment configuration
    USD_PRECISION = Decimal(
        "0.01"
    )  # 2 decimal places for USD (from Config.USD_DECIMAL_PLACES)
    CRYPTO_PRECISION = Decimal(
        "0.00000001"
    )  # 8 decimal places for crypto (from Config.CRYPTO_DECIMAL_PLACES)

    @classmethod
    def get_platform_fee_percentage(cls) -> Decimal:
        """Get the platform fee percentage from configuration - CRITICAL: Returns Decimal for precision"""
        try:
            # Config already imported globally at top of file
            return Decimal(str(Config.ESCROW_FEE_PERCENTAGE))
        except Exception as e:
            logger.error(f"Error getting platform fee percentage: {e}")
            return Decimal("10.0")  # Default 10% fallback

    @classmethod
    async def is_users_first_escrow_async(cls, user_id: int, session: AsyncSession) -> bool:
        """
        Async version: Check if this is user's first PAID escrow trade as a BUYER.
        BUSINESS RULE: First-trade-free applies only to users who have NEVER PAID for an escrow as a BUYER before.
        
        Promotion is consumed when user's payment is confirmed for ANY escrow, regardless of outcome.
        This includes escrows that are completed, disputed, or refunded - as long as payment was confirmed.
        
        ANTI-ABUSE FIX: Checking payment_confirmed_at prevents loophole where users could:
        - Create escrow → Pay → Dispute → Get refund → Still eligible for "first trade free"
        - Repeat the cycle to get unlimited free escrows
        
        Escrows that DO NOT count (never consumed the promotion):
        - Expired before payment (payment_confirmed_at = NULL)
        - Cancelled before payment (payment_confirmed_at = NULL)
        - Payment failed (payment_confirmed_at = NULL)
        
        Being invited as a SELLER doesn't count against the first trade free promotion.
        
        Args:
            user_id: User ID to check
            session: Async database session
            
        Returns:
            True if this is their first paid escrow as buyer, False otherwise
        """
        try:
            from models import Escrow
            
            # Count ONLY escrows where user was the BUYER AND payment was confirmed
            # CRITICAL: Check payment_confirmed_at instead of status to prevent abuse
            stmt = select(func.count(Escrow.id)).where(
                Escrow.buyer_id == user_id,
                Escrow.payment_confirmed_at.is_not(None)
            )
            result = await session.execute(stmt)
            paid_buyer_escrows = result.scalar() or 0
            
            is_first = paid_buyer_escrows == 0
            logger.info(f"First trade check for user {user_id}: {paid_buyer_escrows} paid buyer escrows, is_first={is_first}")
            return is_first
            
        except Exception as e:
            logger.error(f"Error checking first escrow for user {user_id}: {e}")
            return False  # Default to not first trade on error

    @classmethod
    def is_users_first_escrow(cls, user_id: int, session) -> bool:
        """
        Check if this is user's first PAID escrow trade as a BUYER - works with both sync and async sessions.
        BUSINESS RULE: First-trade-free applies only to users who have NEVER PAID for an escrow as a BUYER before.
        
        Promotion is consumed when user's payment is confirmed for ANY escrow, regardless of outcome.
        This includes escrows that are completed, disputed, or refunded - as long as payment was confirmed.
        
        ANTI-ABUSE FIX: Checking payment_confirmed_at prevents loophole where users could:
        - Create escrow → Pay → Dispute → Get refund → Still eligible for "first trade free"
        - Repeat the cycle to get unlimited free escrows
        
        Escrows that DO NOT count (never consumed the promotion):
        - Expired before payment (payment_confirmed_at = NULL)
        - Cancelled before payment (payment_confirmed_at = NULL)
        - Payment failed (payment_confirmed_at = NULL)
        
        Being invited as a SELLER doesn't count against the first trade free promotion.
        This prevents abuse while allowing sellers to get the buyer promotion when they make their first purchase.
        
        Args:
            user_id: User ID to check
            session: Database session (sync or async)
            
        Returns:
            True if this is their first paid escrow as buyer, False otherwise
        """
        try:
            from models import Escrow
            
            # For AsyncSession, safely skip the check to prevent errors
            # Business logic preserved: async flows won't get first-trade benefits, but won't crash
            if isinstance(session, AsyncSession):
                logger.info(f"AsyncSession detected for user {user_id} first trade check - safely defaulting to not first trade")
                return False
            
            # Count ONLY escrows where user was the BUYER AND payment was confirmed
            # CRITICAL: Check payment_confirmed_at instead of status to prevent abuse
            stmt = select(func.count(Escrow.id)).where(
                Escrow.buyer_id == user_id,
                Escrow.payment_confirmed_at.is_not(None)
            )
            result = session.execute(stmt)
            paid_buyer_escrows = result.scalar() or 0
            
            is_first = paid_buyer_escrows == 0
            logger.info(f"First trade check for user {user_id}: {paid_buyer_escrows} paid buyer escrows, is_first={is_first}")
            return is_first
            
        except Exception as e:
            logger.error(f"Error checking first escrow for user {user_id}: {e}")
            return False  # Default to not first trade on error

    @classmethod
    async def get_trader_fee_discount_async(cls, user, session: AsyncSession) -> Decimal:
        """Async version: Get fee discount based on trader level - CRITICAL: Returns Decimal for precision"""
        try:
            from utils.trusted_trader import TrustedTraderSystem

            level_info = await TrustedTraderSystem.get_trader_level_async(user, session)

            # Fee discounts by trader level - Using Decimal for financial precision
            discounts = {
                "New User": Decimal("0.0"),  # 0% discount (5% fee)
                "New Trader": Decimal("0.0"),  # 0% discount (5% fee)
                "Active Trader": Decimal("0.1"),  # 10% discount (4.5% fee)
                "Experienced Trader": Decimal("0.2"),  # 20% discount (4% fee)
                "Trusted Trader": Decimal("0.3"),  # 30% discount (3.5% fee)
                "Elite Trader": Decimal("0.4"),  # 40% discount (3% fee)
                "Master Trader": Decimal("0.5"),  # 50% discount (2.5% fee)
            }

            return discounts.get(level_info["name"], Decimal("0.0"))
        except Exception as e:
            logger.error(f"Error getting async trader fee discount: {e}")
            return Decimal("0.0")

    @classmethod
    def get_trader_fee_discount(cls, user, session) -> Decimal:
        """Get fee discount based on trader level - CRITICAL: Returns Decimal for precision"""
        try:
            from utils.trusted_trader import TrustedTraderSystem

            # For AsyncSession, safely skip to prevent errors
            # Business logic preserved: async flows should use get_trader_fee_discount_async()
            if isinstance(session, AsyncSession):
                logger.warning("AsyncSession detected in sync get_trader_fee_discount - returning 0% discount. Use get_trader_fee_discount_async() instead.")
                return Decimal("0.0")

            level_info = TrustedTraderSystem.get_trader_level(user, session)

            # Fee discounts by trader level - Using Decimal for financial precision
            discounts = {
                "New User": Decimal("0.0"),  # 0% discount (5% fee)
                "New Trader": Decimal("0.0"),  # 0% discount (5% fee)
                "Active Trader": Decimal("0.1"),  # 10% discount (4.5% fee)
                "Experienced Trader": Decimal("0.2"),  # 20% discount (4% fee)
                "Trusted Trader": Decimal("0.3"),  # 30% discount (3.5% fee)
                "Elite Trader": Decimal("0.4"),  # 40% discount (3% fee)
                "Master Trader": Decimal("0.5"),  # 50% discount (2.5% fee)
            }

            return discounts.get(level_info["name"], Decimal("0.0"))
        except Exception as e:
            logger.error(f"Error getting trader fee discount: {e}")
            return Decimal("0.0")

    @classmethod
    def _calculate_fee_split(
        cls, total_platform_fee: Decimal, fee_split_option: str
    ) -> Tuple[Decimal, Decimal]:
        """
        Calculate buyer and seller fee amounts based on split option.

        Args:
            total_platform_fee: Total platform fee as Decimal
            fee_split_option: 'buyer_pays', 'seller_pays', or 'split'

        Returns:
            Tuple of (buyer_fee_amount, seller_fee_amount) as Decimals
        """
        if fee_split_option == "buyer_pays":
            return total_platform_fee, Decimal("0.00")
        elif fee_split_option == "seller_pays":
            return Decimal("0.00"), total_platform_fee
        elif fee_split_option == "split":
            # Split evenly, ensure exact sum with proper rounding
            half_fee = (total_platform_fee / Decimal("2")).quantize(
                cls.USD_PRECISION, rounding=ROUND_HALF_UP
            )
            # Give any remainder to buyer (deterministic rounding rule)
            buyer_fee = half_fee
            seller_fee = total_platform_fee - buyer_fee
            return buyer_fee, seller_fee
        else:
            # Default to buyer pays if invalid option
            return total_platform_fee, Decimal("0.00")

    @classmethod
    async def calculate_escrow_breakdown_async(
        cls,
        escrow_amount: Decimal,
        payment_currency: str = "USD",
        fee_split_option: str = "buyer_pays",
        user=None,
        session: Optional[AsyncSession] = None,
        is_first_trade: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Async version: Calculate complete escrow breakdown including platform and blockchain fees with fee split support.

        Args:
            escrow_amount: Base escrow amount
            payment_currency: Currency for payment (USD, BTC, etc.)
            fee_split_option: 'buyer_pays', 'seller_pays', or 'split'
            user: User object for trader discounts
            session: Async database session for trader level lookup
            is_first_trade: Optional pre-computed first trade status (if provided, skips first trade check)

        Returns:
            Dictionary with all fee calculations and split details
        """
        try:
            escrow_decimal = Decimal(str(escrow_amount))
            base_fee_percentage = Decimal(str(Config.ESCROW_FEE_PERCENTAGE)) / Decimal(
                "100"
            )

            # Apply trader level discount using async version
            fee_discount = 0.0
            if user and session:
                fee_discount = await cls.get_trader_fee_discount_async(user, session)
            
            discounted_fee_percentage = base_fee_percentage * (
                Decimal("1") - Decimal(str(fee_discount))
            )

            # Calculate total platform fee with discount
            total_platform_fee = escrow_decimal * discounted_fee_percentage
            total_platform_fee = total_platform_fee.quantize(
                cls.USD_PRECISION, rounding=ROUND_HALF_UP
            )

            # FIRST TRADE FREE LOGIC: Override fees to zero if this is user's first escrow trade
            is_first_trade_free = False
            try:
                if getattr(Config, 'FIRST_TRADE_FREE_ENABLED', True):
                    should_apply_free = False
                    
                    # Primary: Use pre-computed is_first_trade if provided
                    if is_first_trade is not None:
                        should_apply_free = is_first_trade
                    # Fallback: If user object provided, check via async session
                    elif user and session:
                        should_apply_free = await cls.is_users_first_escrow_async(user.id, session)
                    
                    if should_apply_free:
                        total_platform_fee = Decimal("0.00")
                        is_first_trade_free = True
                        user_info = f"user {user.id}" if user else "user (no object)"
                        logger.info(f"🎉 First trade free applied for {user_info}: ${escrow_amount} escrow with $0.00 fees")
            except Exception as e:
                logger.error(f"Error applying first trade free logic: {e}")

            # MINIMUM FEE LOGIC: Apply minimum fee for small escrows (profitability)
            # Only applies if NOT first trade free AND escrow below threshold AND calculated fee below minimum
            if not is_first_trade_free and hasattr(Config, 'MIN_ESCROW_FEE_AMOUNT'):
                min_fee = Config.MIN_ESCROW_FEE_AMOUNT
                threshold = getattr(Config, 'MIN_ESCROW_FEE_THRESHOLD', Decimal("100.0"))
                
                # Apply minimum fee if enabled (min_fee > 0) AND escrow below threshold AND calculated fee below minimum
                if min_fee > 0 and escrow_decimal < threshold and total_platform_fee < min_fee:
                    original_fee = total_platform_fee
                    total_platform_fee = min_fee
                    logger.info(
                        f"💰 Minimum fee applied: ${escrow_decimal} escrow "
                        f"(calculated: ${original_fee}, minimum: ${min_fee})"
                    )

            # Calculate fee split based on option
            buyer_fee_amount, seller_fee_amount = cls._calculate_fee_split(
                total_platform_fee, fee_split_option
            )

            # Blockchain fee calculation (removed for escrow payments - only apply to cashouts)
            blockchain_fee = Decimal("0.0")

            # Calculate buyer's total payment (escrow + buyer's portion of fee + blockchain fee)
            buyer_total_payment = escrow_decimal + buyer_fee_amount + blockchain_fee

            # Calculate seller's net amount (escrow - seller's portion of fee)
            seller_net_amount = escrow_decimal - seller_fee_amount

            # Amount available for refund depends on fee split option
            if fee_split_option == "seller_pays":
                refundable_amount = escrow_decimal
            elif fee_split_option == "split":
                refundable_amount = escrow_decimal
            else:  # buyer_pays
                refundable_amount = escrow_decimal

            return {
                "escrow_amount": Decimal(str(escrow_amount)),
                "total_platform_fee": Decimal(str(total_platform_fee)),
                "buyer_fee_amount": Decimal(str(buyer_fee_amount)),
                "seller_fee_amount": Decimal(str(seller_fee_amount)),
                "blockchain_fee": Decimal(str(blockchain_fee)),
                "buyer_total_payment": Decimal(str(buyer_total_payment)),
                "seller_net_amount": Decimal(str(seller_net_amount)),
                "refundable_amount": Decimal(str(refundable_amount)),
                "fee_split_option": str(fee_split_option),
                "platform_fee_percentage": float(discounted_fee_percentage * 100),
                "base_fee_percentage": float(Config.ESCROW_FEE_PERCENTAGE),
                "trader_discount": float(fee_discount * 100),
                "is_first_trade_free": is_first_trade_free,
                # Legacy compatibility
                "platform_fee": Decimal(str(buyer_fee_amount)),
                "total_payment": Decimal(str(buyer_total_payment)),
            }

        except Exception as e:
            logger.error(f"Error calculating async escrow breakdown: {e}")
            # Safe fallback
            escrow_amount_decimal = Decimal(str(escrow_amount))
            fee_percentage = 5.0  # Default 5%
            total_platform_fee = escrow_amount_decimal * Decimal(str(fee_percentage / 100))

            # Apply fee split to fallback
            if fee_split_option == "buyer_pays":
                buyer_fee = total_platform_fee
                seller_fee = Decimal("0.0")
                buyer_total = escrow_amount_decimal + total_platform_fee
            elif fee_split_option == "seller_pays":
                buyer_fee = Decimal("0.0")
                seller_fee = total_platform_fee
                buyer_total = escrow_amount_decimal
            else:  # split
                buyer_fee = seller_fee = total_platform_fee / 2
                buyer_total = escrow_amount_decimal + buyer_fee

            return {
                "escrow_amount": Decimal(str(escrow_amount)),
                "total_platform_fee": Decimal(str(total_platform_fee)),
                "buyer_fee_amount": Decimal(str(buyer_fee)),
                "seller_fee_amount": Decimal(str(seller_fee)),
                "blockchain_fee": Decimal("0.0"),
                "buyer_total_payment": Decimal(str(buyer_total)),
                "seller_net_amount": Decimal(str(escrow_amount_decimal - seller_fee)),
                "refundable_amount": Decimal(str(escrow_amount)),
                "fee_split_option": str(fee_split_option),
                "platform_fee_percentage": fee_percentage,
                "base_fee_percentage": fee_percentage,
                "trader_discount": 0.0,
                "platform_fee": Decimal(str(buyer_fee)),
                "total_payment": Decimal(str(buyer_total)),
            }

    @classmethod
    def calculate_escrow_breakdown(
        cls,
        escrow_amount: Decimal,
        payment_currency: str = "USD",
        fee_split_option: str = "buyer_pays",
        user=None,
        session=None,
        is_first_trade: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Calculate complete escrow breakdown including platform and blockchain fees with fee split support.

        Args:
            escrow_amount: Base escrow amount
            payment_currency: Currency for payment (USD, BTC, etc.)
            fee_split_option: 'buyer_pays', 'seller_pays', or 'split'
            user: User object for trader discounts
            session: Database session for trader level lookup
            is_first_trade: Optional pre-computed first trade status (if provided, skips first trade check)

        Returns:
            Dictionary with all fee calculations and split details
        """
        try:
            escrow_decimal = Decimal(str(escrow_amount))
            base_fee_percentage = Decimal(str(Config.ESCROW_FEE_PERCENTAGE)) / Decimal(
                "100"
            )

            # Apply trader level discount
            fee_discount = (
                cls.get_trader_fee_discount(user, session) if user and session else 0.0
            )
            discounted_fee_percentage = base_fee_percentage * (
                Decimal("1") - Decimal(str(fee_discount))
            )

            # Calculate total platform fee with discount
            total_platform_fee = escrow_decimal * discounted_fee_percentage
            total_platform_fee = total_platform_fee.quantize(
                cls.USD_PRECISION, rounding=ROUND_HALF_UP
            )

            # FIRST TRADE FREE LOGIC: Override fees to zero if this is user's first escrow trade
            is_first_trade_free = False
            try:
                # Config already imported globally at top of file
                if getattr(Config, 'FIRST_TRADE_FREE_ENABLED', True):  # Default enabled
                    # FIXED: Honor is_first_trade parameter regardless of user object
                    should_apply_free = False
                    
                    # Primary: Use pre-computed is_first_trade if provided (works without user object)
                    if is_first_trade is not None:
                        should_apply_free = is_first_trade
                    # Fallback: If user object provided, check via session
                    elif user and session:
                        should_apply_free = cls.is_users_first_escrow(user.id, session)
                    
                    if should_apply_free:
                        total_platform_fee = Decimal("0.00")
                        is_first_trade_free = True
                        user_info = f"user {user.id}" if user else "user (no object)"
                        logger.info(f"🎉 First trade free applied for {user_info}: ${escrow_amount} escrow with $0.00 fees")
            except Exception as e:
                logger.error(f"Error applying first trade free logic: {e}")

            # MINIMUM FEE LOGIC: Apply minimum fee for small escrows (profitability)
            # Only applies if NOT first trade free AND escrow below threshold AND calculated fee below minimum
            if not is_first_trade_free and hasattr(Config, 'MIN_ESCROW_FEE_AMOUNT'):
                min_fee = Config.MIN_ESCROW_FEE_AMOUNT
                threshold = getattr(Config, 'MIN_ESCROW_FEE_THRESHOLD', Decimal("100.0"))
                
                # Apply minimum fee if enabled (min_fee > 0) AND escrow below threshold AND calculated fee below minimum
                if min_fee > 0 and escrow_decimal < threshold and total_platform_fee < min_fee:
                    original_fee = total_platform_fee
                    total_platform_fee = min_fee
                    logger.info(
                        f"💰 Minimum fee applied: ${escrow_decimal} escrow "
                        f"(calculated: ${original_fee}, minimum: ${min_fee})"
                    )

            # Calculate fee split based on option
            buyer_fee_amount, seller_fee_amount = cls._calculate_fee_split(
                total_platform_fee, fee_split_option
            )

            # Blockchain fee calculation (removed for escrow payments - only apply to cashouts)
            blockchain_fee = Decimal("0.0")

            # Calculate buyer's total payment (escrow + buyer's portion of fee + blockchain fee)
            buyer_total_payment = escrow_decimal + buyer_fee_amount + blockchain_fee

            # Calculate seller's net amount (escrow - seller's portion of fee)
            seller_net_amount = escrow_decimal - seller_fee_amount

            # Amount available for refund depends on fee split option
            if fee_split_option == "seller_pays":
                # Buyer paid no fee, gets full escrow amount back
                refundable_amount = escrow_decimal
            elif fee_split_option == "split":
                # Buyer loses only their portion of the fee
                refundable_amount = escrow_decimal
            else:  # buyer_pays
                # Buyer loses the full platform fee
                refundable_amount = escrow_decimal

            return {
                "escrow_amount": Decimal(str(escrow_amount)),
                "total_platform_fee": Decimal(str(total_platform_fee)),
                "buyer_fee_amount": Decimal(str(buyer_fee_amount)),
                "seller_fee_amount": Decimal(str(seller_fee_amount)),
                "blockchain_fee": Decimal(str(blockchain_fee)),
                "buyer_total_payment": Decimal(str(buyer_total_payment)),
                "seller_net_amount": Decimal(str(seller_net_amount)),
                "refundable_amount": Decimal(str(refundable_amount)),
                "fee_split_option": str(fee_split_option),
                "platform_fee_percentage": float(discounted_fee_percentage * 100),
                "base_fee_percentage": float(Config.ESCROW_FEE_PERCENTAGE),
                "trader_discount": float(fee_discount * 100),
                "is_first_trade_free": is_first_trade_free,  # First trade free indicator
                # Legacy compatibility
                "platform_fee": Decimal(str(buyer_fee_amount)),  # For backward compatibility
                "total_payment": Decimal(str(buyer_total_payment)),  # For backward compatibility
            }

        except Exception as e:
            logger.error(f"Error calculating escrow breakdown: {e}")
            # Safe fallback - use hardcoded safe values to avoid Config access issues in exception handler
            try:
                # Try to access Config safely
                fee_percentage = float(getattr(Config, 'ESCROW_FEE_PERCENTAGE', 5.0))
                processing_fee = float(getattr(Config, 'PROCESSING_FEE_PERCENTAGE', 0.0))
            except Exception:
                # Ultimate fallback with hardcoded values
                fee_percentage = 5.0  # Default 5%
                processing_fee = 0.0  # Default 0%
            
            escrow_amount_decimal = Decimal(str(escrow_amount))
            total_platform_fee = escrow_amount_decimal * Decimal(str(fee_percentage / 100))

            # Apply fee split to fallback
            if fee_split_option == "buyer_pays":
                buyer_fee = total_platform_fee
                seller_fee = Decimal("0.0")
                buyer_total = escrow_amount_decimal + total_platform_fee
            elif fee_split_option == "seller_pays":
                buyer_fee = Decimal("0.0")
                seller_fee = total_platform_fee
                buyer_total = escrow_amount_decimal
            else:  # split
                buyer_fee = seller_fee = total_platform_fee / 2
                buyer_total = escrow_amount_decimal + buyer_fee

            blockchain_fee = Decimal(str(processing_fee))
            return {
                "escrow_amount": Decimal(str(escrow_amount)),
                "total_platform_fee": Decimal(str(total_platform_fee)),
                "buyer_fee_amount": Decimal(str(buyer_fee)),
                "seller_fee_amount": Decimal(str(seller_fee)),
                "blockchain_fee": Decimal(str(blockchain_fee)),
                "buyer_total_payment": Decimal(str(buyer_total)),
                "seller_net_amount": Decimal(str(escrow_amount_decimal - seller_fee)),
                "refundable_amount": Decimal(str(escrow_amount)),
                "fee_split_option": str(fee_split_option),
                "platform_fee_percentage": fee_percentage,
                "base_fee_percentage": fee_percentage,
                "trader_discount": 0.0,
                # Legacy compatibility
                "platform_fee": Decimal(str(buyer_fee if buyer_fee else 0)),
                "total_payment": Decimal(str(buyer_total if buyer_total else 0)),
            }

    @classmethod
    def calculate_refund_amount(
        cls,
        escrow_amount: float,
        buyer_fee_amount: Optional[float] = None,
        fee_split_option: str = "buyer_pays",
    ) -> float:
        """
        Calculate the amount to refund to buyer upon cancellation based on fee split option.
        BUSINESS RULE: Platform fees are ALWAYS retained for ACTIVE escrows.

        Args:
            escrow_amount: Base escrow amount
            buyer_fee_amount: Amount of fee buyer actually paid (from stored data)
            fee_split_option: Fee split option used in the original trade

        Returns:
            Amount to refund to buyer (escrow amount minus platform fees)
        """
        try:
            escrow_decimal = Decimal(str(escrow_amount))
            buyer_fee_decimal = Decimal(str(buyer_fee_amount or 0))

            # CRITICAL BUSINESS RULE: Deduct platform fees from refund
            # Buyer gets escrow amount minus the platform fee they paid
            refund_amount = escrow_decimal - buyer_fee_decimal

            # Ensure refund is never negative
            if refund_amount < 0:
                refund_amount = Decimal("0")
                logger.warning(
                    "Refund calculation resulted in negative amount, setting to 0"
                )

            return float(refund_amount.quantize(cls.USD_PRECISION, rounding=ROUND_HALF_UP))
        except Exception as e:
            logger.error(f"Error calculating refund amount: {e}")
            return max(0, escrow_amount - (buyer_fee_amount or 0))

    @classmethod
    def calculate_cancellation_refund_breakdown(
        cls,
        escrow_amount: float,
        buyer_fee_amount: float,
        seller_fee_amount: float,
        fee_split_option: str,
    ) -> Dict[str, Any]:
        """
        Calculate what buyer gets back vs what platform retains on cancellation using stored fee amounts.

        Args:
            escrow_amount: Base escrow amount from stored data
            buyer_fee_amount: Fee amount buyer paid from stored data
            seller_fee_amount: Fee amount seller was supposed to pay from stored data
            fee_split_option: Fee split option from stored data

        Returns:
            Breakdown of refund amounts and platform retention
        """
        try:
            escrow_decimal = Decimal(str(escrow_amount))
            buyer_fee_decimal = Decimal(str(buyer_fee_amount or 0))
            seller_fee_decimal = Decimal(str(seller_fee_amount or 0))

            # Calculate what buyer originally paid
            buyer_total_paid = escrow_decimal + buyer_fee_decimal

            # BUSINESS RULE: Buyer gets escrow amount minus platform fees
            refund_amount = escrow_decimal - buyer_fee_decimal

            # Platform keeps the fee that buyer actually paid
            platform_keeps = buyer_fee_decimal

            # Ensure refund is never negative
            if refund_amount < 0:
                refund_amount = Decimal("0")

            # Seller fee is not collected on cancellation (since trade didn't complete)
            seller_fee_not_collected = seller_fee_decimal

            return {
                "total_paid_by_buyer": Decimal(str(buyer_total_paid)),
                "escrow_amount": Decimal(str(escrow_decimal)),
                "buyer_fee_paid": Decimal(str(buyer_fee_decimal)),
                "seller_fee_not_collected": Decimal(str(seller_fee_not_collected)),
                "refund_amount": Decimal(str(refund_amount)),
                "platform_keeps": Decimal(str(platform_keeps)),
                "fee_split_option": str(fee_split_option),
            }

        except Exception as e:
            logger.error(f"Error calculating cancellation refund breakdown: {e}")
            # Safe fallback using provided amounts
            buyer_total_paid = Decimal(str(escrow_amount)) + Decimal(str(buyer_fee_amount or 0))
            return {
                "total_paid_by_buyer": Decimal(str(buyer_total_paid)),
                "escrow_amount": Decimal(str(escrow_amount)),
                "buyer_fee_paid": Decimal(str(buyer_fee_amount or 0)),
                "seller_fee_not_collected": Decimal(str(seller_fee_amount or 0)),
                "refund_amount": Decimal(str(escrow_amount)),
                "platform_keeps": Decimal(str(buyer_fee_amount or 0)),
                "fee_split_option": str(fee_split_option),
            }

    @classmethod
    def calculate_ngn_cashout_fee(cls, amount_usd: float) -> Dict[str, float]:
        """Calculate NGN cashout fees - FREE processing since we profit from exchange markup"""
        try:
            # NGN cashout fees: ₦0 processing fee (updated January 2025)
            ngn_fee = 0.0  # FREE processing - profit from exchange markup

            return {
                "fee_amount_ngn": ngn_fee,
                "fee_amount_usd": 0.0,  # No USD fee either
                "processing_fee": 0.0,  # Completely free processing
            }

        except Exception as e:
            logger.error(f"Error calculating NGN cashout fee: {e}")
            return {
                "fee_amount_ngn": 0.0,  # FREE on error too
                "fee_amount_usd": 0.0,
                "processing_fee": 0.0,
            }

    @classmethod
    def calculate_release_amount(
        cls, escrow_amount: Decimal, seller_fee_amount: Optional[Decimal] = None
    ) -> Decimal:
        """
        Calculate the amount to release to seller after deducting their portion of the fee.

        Args:
            escrow_amount: Base escrow amount (Decimal for precision)
            seller_fee_amount: Fee amount seller must pay (from stored data, Decimal for precision)

        Returns:
            Net amount to release to seller (Decimal)
        """
        try:
            escrow_decimal = Decimal(str(escrow_amount))
            seller_fee_decimal = Decimal(str(seller_fee_amount or 0))

            # Seller gets escrow amount minus their fee portion
            release_amount = escrow_decimal - seller_fee_decimal

            return release_amount.quantize(cls.USD_PRECISION, rounding=ROUND_HALF_UP)
        except Exception as e:
            logger.error(f"Error calculating release amount: {e}")
            return escrow_amount - (seller_fee_amount or 0)

    @classmethod
    def validate_fee_split_option(cls, fee_split_option: str) -> bool:
        """
        Validate that fee split option is valid.

        Args:
            fee_split_option: Fee split option to validate

        Returns:
            True if valid, False otherwise
        """
        valid_options = ["buyer_pays", "seller_pays", "split"]
        return fee_split_option in valid_options

    @classmethod
    def validate_escrow_amounts(
        cls,
        escrow_amount: float,
        buyer_fee_amount: Optional[float],
        seller_fee_amount: Optional[float],
        fee_split_option: str,
    ) -> Tuple[bool, str]:
        """
        Validate escrow amounts and fee amounts for consistency.

        Args:
            escrow_amount: Base escrow amount
            buyer_fee_amount: Fee amount buyer pays
            seller_fee_amount: Fee amount seller pays
            fee_split_option: Fee split option

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            # Validate fee split option
            if not cls.validate_fee_split_option(fee_split_option):
                return False, f"Invalid fee split option: {fee_split_option}"

            # Validate minimum escrow amount (from Config)
            # Config already imported globally at top of file

            if escrow_amount < Config.MIN_ESCROW_AMOUNT_USD:
                return (
                    False,
                    f"Escrow amount ${escrow_amount:.2f} is below minimum ${Config.MIN_ESCROW_AMOUNT_USD:.2f}",
                )

            # Validate maximum escrow amount (reasonable limit)
            MAX_ESCROW_AMOUNT = 100000.0  # $100,000 USD
            if escrow_amount > MAX_ESCROW_AMOUNT:
                return (
                    False,
                    f"Escrow amount ${escrow_amount:.2f} exceeds maximum ${MAX_ESCROW_AMOUNT:.2f}",
                )

            # Validate fee amounts are non-negative
            if buyer_fee_amount is not None and buyer_fee_amount < 0:
                return (
                    False,
                    f"Buyer fee amount cannot be negative: ${buyer_fee_amount:.2f}",
                )

            if seller_fee_amount is not None and seller_fee_amount < 0:
                return (
                    False,
                    f"Seller fee amount cannot be negative: ${seller_fee_amount:.2f}",
                )

            # Validate fee split logic consistency
            buyer_fee = buyer_fee_amount or 0.0
            seller_fee = seller_fee_amount or 0.0
            total_fee = buyer_fee + seller_fee

            # Calculate expected total fee
            expected_breakdown = cls.calculate_escrow_breakdown(
                escrow_amount, fee_split_option
            )
            expected_total_fee = expected_breakdown["total_platform_fee"]

            # Allow for small rounding differences (1 cent tolerance)
            fee_difference = abs(total_fee - expected_total_fee)
            if fee_difference > 0.01:
                return (
                    False,
                    f"Fee amounts don't match expected total: got ${total_fee:.2f}, expected ${expected_total_fee:.2f}",
                )

            # Validate fee split option consistency
            if fee_split_option == "buyer_pays":
                if seller_fee > 0.01:  # Allow small rounding tolerance
                    return (
                        False,
                        f"Seller fee should be zero for buyer_pays option, got ${seller_fee:.2f}",
                    )
            elif fee_split_option == "seller_pays":
                if buyer_fee > 0.01:  # Allow small rounding tolerance
                    return (
                        False,
                        f"Buyer fee should be zero for seller_pays option, got ${buyer_fee:.2f}",
                    )
            elif fee_split_option == "split":
                # For split, both should have roughly equal amounts (within 1 cent)
                expected_each = expected_total_fee / 2
                if (
                    abs(buyer_fee - expected_each) > 0.01
                    or abs(seller_fee - expected_each) > 0.01
                ):
                    return (
                        False,
                        f"Split fees should be roughly equal: buyer ${buyer_fee:.2f}, seller ${seller_fee:.2f}, expected ${expected_each:.2f} each",
                    )

            return True, ""

        except Exception as e:
            logger.error(f"Error validating escrow amounts: {e}")
            return False, f"Validation error: {str(e)}"

    @classmethod
    def validate_escrow_data_integrity(cls, escrow) -> Tuple[bool, str]:
        """
        Validate escrow object has all required fee split data.

        Args:
            escrow: Escrow object to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            # Check core required fields exist (fee_split_option is stored in pricing_snapshot, not as column)
            if not hasattr(escrow, "amount") or escrow.amount is None:
                return False, "Escrow missing amount"

            if not hasattr(escrow, "fee_amount") or escrow.fee_amount is None:
                return False, "Escrow missing fee_amount"

            if not hasattr(escrow, "total_amount") or escrow.total_amount is None:
                return False, "Escrow missing total_amount"

            # Validate amount integrity
            if escrow.amount <= 0:
                return False, f"Invalid escrow amount: {escrow.amount}"

            if escrow.fee_amount < 0:
                return False, f"Invalid fee amount: {escrow.fee_amount}"

            # Validate total = amount + fees (with small tolerance for floating point precision)
            expected_total = escrow.amount + escrow.fee_amount
            if abs(float(escrow.total_amount) - float(expected_total)) > 0.01:
                return False, f"Total amount mismatch: {escrow.total_amount} != {expected_total}"

            return True, ""

        except Exception as e:
            logger.error(f"Error validating escrow data integrity: {e}")
            return False, f"Validation error: {str(e)}"

    @classmethod
    def create_fee_transactions_at_release(
        cls, escrow, seller_user_id: int, session
    ) -> list:
        """
        Create fee transactions at release time based on fee split option.
        Only creates transactions for fees that should be recognized at release time.

        Args:
            escrow: Escrow object with fee split data
            seller_user_id: User ID of seller receiving release
            session: Database session

        Returns:
            List of Transaction objects to be added to session
        """
        transactions = []

        # Only create fee transaction for seller's portion based on fee split option
        if escrow.fee_split_option == "seller_pays":
            # Seller pays full fee at release time
            if escrow.seller_fee_amount and escrow.seller_fee_amount > 0:
                from models import Transaction

                fee_transaction = Transaction(
                    transaction_id=f"SF{escrow.escrow_id[-8:]}",  # SF = Seller Fee
                    escrow_id=escrow.id,
                    user_id=seller_user_id,
                    transaction_type="fee",
                    amount=Decimal(str(escrow.seller_fee_amount)),
                    currency="USD",
                    status="completed",
                    description=f"💳 Platform fee for trade #{escrow.escrow_id} (Seller pays full fee)",
                    confirmed_at=datetime.utcnow(),
                )
                transactions.append(fee_transaction)
        elif escrow.fee_split_option == "split":
            # Seller pays their portion at release time
            if escrow.seller_fee_amount and escrow.seller_fee_amount > 0:
                from models import Transaction

                fee_transaction = Transaction(
                    transaction_id=f"SFS{escrow.escrow_id[-8:]}",  # SFS = Seller Fee Split
                    escrow_id=escrow.id,
                    user_id=seller_user_id,
                    transaction_type="fee",
                    amount=Decimal(str(escrow.seller_fee_amount)),
                    currency="USD",
                    status="completed",
                    description=f"💳 Seller portion of platform fee for trade #{escrow.escrow_id} (Split fee: ${escrow.buyer_fee_amount:.2f} buyer, ${escrow.seller_fee_amount:.2f} seller)",
                    confirmed_at=datetime.utcnow(),
                )
                transactions.append(fee_transaction)
        # For 'buyer_pays', no fee transaction at release time

        return transactions

    @classmethod
    def calculate_split_amounts(
        cls, escrow_amount: float, buyer_percentage: float
    ) -> Tuple[float, float]:
        """
        Calculate split amounts for dispute resolution.
        Both amounts come from the escrow amount (platform 5% fee was already collected).
        """
        try:
            escrow_decimal = Decimal(str(escrow_amount))
            buyer_percent_decimal = Decimal(str(buyer_percentage)) / Decimal("100")

            buyer_amount = escrow_decimal * buyer_percent_decimal
            seller_amount = escrow_decimal - buyer_amount

            buyer_final = buyer_amount.quantize(cls.USD_PRECISION, rounding=ROUND_HALF_UP)
            seller_final = seller_amount.quantize(cls.USD_PRECISION, rounding=ROUND_HALF_UP)

            # Ensure total adds up exactly (adjust seller amount if needed)
            escrow_amount_decimal = Decimal(str(escrow_amount))
            calculated_total = buyer_final + seller_final
            if calculated_total != escrow_amount_decimal:
                adjustment = escrow_amount_decimal - calculated_total
                seller_final += adjustment

            return float(buyer_final), float(seller_final)

        except Exception as e:
            logger.error(f"Error calculating split amounts: {e}")
            buyer_amount = escrow_amount * (buyer_percentage / 100)
            return buyer_amount, escrow_amount - buyer_amount

    @classmethod
    def calculate_network_fee(
        cls, currency: str, operation: str = "cashout"
    ) -> float:
        """Calculate network fee for blockchain operations"""
        try:
            network_fees = Config.NETWORK_FEES
            fee = network_fees.get(currency, 1.0)  # Default $1 if not specified
            fee_decimal = Decimal(str(fee))
            return float(fee_decimal.quantize(cls.USD_PRECISION, rounding=ROUND_HALF_UP))
        except Exception as e:
            logger.error(f"Error calculating network fee: {e}")
            return 1.0  # Safe default

    @classmethod
    def calculate_deposit_net_amount(cls, gross_amount: float, currency: str) -> float:
        """Calculate net amount for deposit (no blockchain fees deducted for deposits)"""
        try:
            gross_decimal = Decimal(str(gross_amount))
            # No blockchain fee deduction for deposits - user gets full amount
            return float(gross_decimal.quantize(cls.USD_PRECISION, rounding=ROUND_HALF_UP))
        except Exception as e:
            logger.error(f"Error calculating deposit net amount: {e}")
            return gross_amount

    @classmethod
    def calculate_cashout_net_amount(
        cls, gross_amount: float, currency: str
    ) -> float:
        """Calculate net cashout amount after deducting network fee"""
        try:
            gross_decimal = Decimal(str(gross_amount))
            network_fee = cls.calculate_network_fee(currency)
            fee_decimal = Decimal(str(network_fee))

            net_amount = gross_decimal - fee_decimal
            return float(net_amount.quantize(cls.USD_PRECISION, rounding=ROUND_HALF_UP))
        except Exception as e:
            logger.error(f"Error calculating cashout net amount: {e}")
            return gross_amount - cls.calculate_network_fee(currency)

    @classmethod
    def get_fee_summary(
        cls, escrow_amount: float, payment_currency: str = "USD"
    ) -> str:
        """Get human-readable fee summary for display"""
        breakdown = cls.calculate_escrow_breakdown(escrow_amount, payment_currency)

        fee_lines = []
        fee_lines.append(f"• Escrow Amount: ${breakdown['escrow_amount']:.2f}")
        fee_lines.append(f"• Platform Fee (5%): ${breakdown['platform_fee']:.2f}")

        if breakdown["blockchain_fee"] > 0:
            fee_lines.append(
                f"• Blockchain Fee ({payment_currency}): ${breakdown['blockchain_fee']:.2f}"
            )

        fee_summary = "\n".join(fee_lines)

        return f"""💰 Fee Breakdown:
{fee_summary}
• Total Payment: ${breakdown['total_payment']:.2f}

ℹ️ Refund Policy:
• If refunded: ${breakdown['refundable_amount']:.2f} (fees non-refundable)
• If released: Seller gets ${breakdown['escrow_amount']:.2f}"""

    @classmethod
    def create_fee_transactions_at_payment(
        cls, escrow, payer_user_id: int, session
    ) -> list:
        """Create fee transactions for buyer's portion at payment time (idempotent)"""
        from models import Transaction
        from utils.universal_id_generator import UniversalIDGenerator
        from datetime import datetime

        transactions_created = []

        # Only create buyer fee transaction if buyer pays any fees
        if escrow.buyer_fee_amount and Decimal(str(escrow.buyer_fee_amount)) > 0:
            # Check if buyer fee transaction already exists (idempotency)
            existing_tx = (
                session.query(Transaction)
                .filter(
                    Transaction.escrow_id == escrow.id,
                    Transaction.user_id == payer_user_id,
                    Transaction.transaction_type == "fee",
                    Transaction.description.contains("payment-time"),
                )
                .first()
            )

            if not existing_tx:
                fee_transaction = Transaction(
                    transaction_id=UniversalIDGenerator.generate_transaction_id(),
                    user_id=payer_user_id,
                    escrow_id=escrow.id,
                    transaction_type="fee",
                    amount=Decimal(str(escrow.buyer_fee_amount)),
                    currency="USD",
                    status="completed",
                    description=f"Buyer fee (payment-time) for escrow {escrow.escrow_id}: ${escrow.buyer_fee_amount:.2f} USD",
                    reference=f"BF{escrow.escrow_id}",
                    confirmed_at=datetime.utcnow(),
                )
                session.add(fee_transaction)
                transactions_created.append(fee_transaction)
                logger.info(
                    f"Created buyer fee transaction: ${escrow.buyer_fee_amount:.2f} for escrow {escrow.escrow_id}"
                )
            else:
                logger.info(
                    f"Buyer fee transaction already exists for escrow {escrow.escrow_id}"
                )

        return transactions_created

    @classmethod
    def create_fee_transactions_at_release_duplicate(
        cls, escrow, seller_user_id: int, session
    ) -> list:
        """Create fee transactions for seller's portion at release time (idempotent) - DUPLICATE TO REMOVE"""
        from models import Transaction
        from utils.universal_id_generator import UniversalIDGenerator
        from datetime import datetime

        transactions_created = []

        # Only create seller fee transaction if seller pays any fees
        if escrow.seller_fee_amount and Decimal(str(escrow.seller_fee_amount)) > 0:
            # Check if seller fee transaction already exists (idempotency)
            existing_tx = (
                session.query(Transaction)
                .filter(
                    Transaction.escrow_id == escrow.id,
                    Transaction.user_id == seller_user_id,
                    Transaction.transaction_type == "fee",
                    Transaction.description.contains("release-time"),
                )
                .first()
            )

            if not existing_tx:
                fee_transaction = Transaction(
                    transaction_id=UniversalIDGenerator.generate_transaction_id(),
                    user_id=seller_user_id,
                    escrow_id=escrow.id,
                    transaction_type="fee",
                    amount=Decimal(str(escrow.seller_fee_amount)),
                    currency="USD",
                    status="completed",
                    description=f"Seller fee (release-time) for escrow {escrow.escrow_id}: ${escrow.seller_fee_amount:.2f} USD",
                    reference=f"SF{escrow.escrow_id}",
                    confirmed_at=datetime.utcnow(),
                )
                session.add(fee_transaction)
                transactions_created.append(fee_transaction)
                logger.info(
                    f"Created seller fee transaction: ${escrow.seller_fee_amount:.2f} for escrow {escrow.escrow_id}"
                )
            else:
                logger.info(
                    f"Seller fee transaction already exists for escrow {escrow.escrow_id}"
                )

        return transactions_created
