"""Wallet validation utilities for production safety"""

import logging
from decimal import Decimal
from typing import Optional, Tuple, Dict, Any, cast
from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from database import SessionLocal
from models import Wallet, Transaction, Escrow, EscrowStatus

logger = logging.getLogger(__name__)

class WalletValidator:
    """Production-critical wallet validation to prevent balance inconsistencies"""
    
    @staticmethod
    def _is_async_session(session) -> bool:
        """Check if the session is an AsyncSession"""
        return isinstance(session, AsyncSession)
    
    @staticmethod
    async def get_reserved_amount_in_escrows(
        user_id: int,
        currency: str = "USD",
        session=None
    ) -> Decimal:
        """
        Calculate total amount reserved/locked in active escrows for a user.
        This includes payment_pending, payment_confirmed, and active escrows
        where the user is the buyer and payment method is wallet or hybrid.
        
        Args:
            user_id: User ID to check reserved amounts for
            currency: Currency (default: USD)
            session: Database session
            
        Returns:
            Total reserved amount as Decimal
        """
        own_session = session is None
        if own_session:
            session = SessionLocal()
        
        is_async = WalletValidator._is_async_session(session)
        
        try:
            # Query escrows where funds are locked
            stmt = select(func.sum(Escrow.total_amount)).where(
                Escrow.buyer_id == user_id,
                Escrow.status.in_([
                    EscrowStatus.PAYMENT_PENDING.value,
                    EscrowStatus.PAYMENT_CONFIRMED.value,
                    EscrowStatus.ACTIVE.value,
                ]),
                or_(
                    Escrow.payment_method == "wallet",
                    Escrow.payment_method == "hybrid"
                )
            )
            
            if is_async:
                async_session = cast(AsyncSession, session)
                result = await async_session.execute(stmt)
            else:
                result = session.execute(stmt)
            
            reserved_sum = result.scalar() or 0
            reserved_amount = Decimal(str(reserved_sum))
            
            logger.info(f"User {user_id} has ${reserved_amount:.2f} reserved in active escrows")
            return reserved_amount
            
        except Exception as e:
            logger.error(f"Error calculating reserved amounts for user {user_id}: {e}")
            # Return 0 to be safe - don't block transactions on calculation error
            return Decimal("0")
        finally:
            if own_session and not is_async:
                session.close()
    
    @staticmethod
    async def validate_wallet_debit_completed(
        user_id: int, 
        escrow_id: Optional[int] = None, 
        expected_amount: Optional[Decimal] = None,
        session=None,
        cashout_id: Optional[str] = None,
        transaction_types: Optional[list] = None
    ) -> Tuple[bool, str]:
        """
        Validate that a wallet debit was properly completed for a trade or cashout.
        Returns (success, error_message)
        
        Args:
            user_id: User whose wallet to validate
            escrow_id: For escrow payment validation (optional)
            expected_amount: Expected debit amount (optional for existence check)
            cashout_id: For cashout payment validation (optional)
            transaction_types: List of transaction types to check (default: ["wallet_payment"])
        """
        own_session = session is None
        if own_session:
            session = SessionLocal()
        
        is_async = WalletValidator._is_async_session(session)
        
        try:
            # Set default transaction types if not provided
            if transaction_types is None:
                transaction_types = ["wallet_payment", "cashout"]
            
            # Build base query using SQLAlchemy 2.0 select pattern
            stmt = select(Transaction).where(
                Transaction.user_id == user_id,
                Transaction.transaction_type.in_(transaction_types),
                Transaction.status == "completed"
            )
            
            # Add escrow or cashout filtering
            if escrow_id is not None:
                stmt = stmt.where(Transaction.escrow_id == escrow_id)
            elif cashout_id is not None:
                stmt = stmt.where(Transaction.description.contains(cashout_id))
            
            # Execute query based on session type
            if is_async:
                async_session = cast(AsyncSession, session)
                result = await async_session.execute(stmt)
            else:
                result = session.execute(stmt)
            transaction = result.scalar_one_or_none()
            
            if not transaction:
                transaction_type_str = " or ".join(transaction_types)
                return False, f"No {transaction_type_str} transaction found"
            
            # Verify transaction amount matches expected (if specified)
            if expected_amount is not None:
                transaction_amount = abs(Decimal(str(transaction.amount)))
                expected_decimal = Decimal(str(expected_amount)) if expected_amount is not None else Decimal("0")
                if transaction_amount < expected_decimal - Decimal("0.01"):  # Allow 1 cent tolerance
                    return False, f"Transaction amount mismatch: {transaction_amount} < {expected_decimal}"
            
            # Check wallet balance was actually reduced
            wallet_stmt = select(Wallet).where(
                Wallet.user_id == user_id,
                Wallet.currency == "USD"
            )
            
            if is_async:
                async_session = cast(AsyncSession, session)
                wallet_result = await async_session.execute(wallet_stmt)
            else:
                wallet_result = session.execute(wallet_stmt)
            wallet = wallet_result.scalar_one_or_none()
            
            if not wallet:
                return False, "User wallet not found"
            
            # Verify by checking transaction history sum - debits
            debits_stmt = select(func.sum(Transaction.amount)).where(
                Transaction.user_id == user_id,
                Transaction.transaction_type.in_(["wallet_payment", "cashout"]),
                Transaction.status == "completed"
            )
            
            if is_async:
                async_session = cast(AsyncSession, session)
                debits_result = await async_session.execute(debits_stmt)
            else:
                debits_result = session.execute(debits_stmt)
            total_debits = debits_result.scalar() or Decimal("0")
            
            # Verify by checking transaction history sum - credits
            credits_stmt = select(func.sum(Transaction.amount)).where(
                Transaction.user_id == user_id,
                Transaction.transaction_type.in_(["deposit", "refund", "credit", "wallet_deposit", "release", "automatic_refund", "escrow_refund"]),
                Transaction.status == "completed"
            )
            
            if is_async:
                async_session = cast(AsyncSession, session)
                credits_result = await async_session.execute(credits_stmt)
            else:
                credits_result = session.execute(credits_stmt)
            total_credits = credits_result.scalar() or Decimal("0")
            
            # Expected balance should be credits - debits
            expected_balance = total_credits + total_debits  # debits are negative
            actual_balance = Decimal(str(wallet.available_balance))
            
            if abs(expected_balance - actual_balance) > Decimal("0.01"):
                logger.error(
                    f"CRITICAL: Wallet balance mismatch for user {user_id}: "
                    f"Expected ${expected_balance:.2f}, Actual ${actual_balance:.2f}"
                )
                # Don't fail the trade, but log for investigation
                
            return True, "Wallet debit validated successfully"
            
        except Exception as e:
            logger.error(f"Error validating wallet debit: {e}")
            return False, f"Validation error: {str(e)}"
        finally:
            if own_session and not is_async:
                session.close()
    
    @staticmethod
    async def ensure_trade_payment_integrity(escrow: Escrow, session=None) -> bool:
        """
        Ensure a trade has proper payment before allowing it to proceed.
        Critical for production safety.
        """
        own_session = session is None
        if own_session:
            session = SessionLocal()
        
        is_async = WalletValidator._is_async_session(session)
        
        try:
            # Only validate wallet payments
            if str(escrow.payment_method) != "wallet":
                return True
            
            # Check if payment was completed
            buyer_id = escrow.buyer_id if escrow.buyer_id is not None else None
            escrow_pk = escrow.id if escrow.id is not None else None
            amount = Decimal(str(escrow.amount)) if escrow.amount is not None else Decimal("0")
            fee_amount = Decimal(str(escrow.fee_amount)) if escrow.fee_amount is not None else Decimal("0")
            
            # Validate buyer_id is present
            if buyer_id is None:
                logger.error(f"CRITICAL: Trade {escrow.escrow_id} has no buyer_id")
                return False
            
            is_valid, error_msg = await WalletValidator.validate_wallet_debit_completed(
                user_id=cast(int, buyer_id),
                escrow_id=cast(Optional[int], escrow_pk),
                expected_amount=amount + fee_amount,
                session=session
            )
            
            if not is_valid:
                logger.error(
                    f"CRITICAL: Trade {escrow.escrow_id} has invalid wallet payment: {error_msg}"
                )
                
                # Set trade to error state to prevent further processing
                from sqlalchemy import update
                update_stmt = update(type(escrow)).where(type(escrow).id == escrow.id).values(status="payment_error")
                
                if is_async:
                    async_session = cast(AsyncSession, session)
                    await async_session.execute(update_stmt)
                    if own_session:
                        await async_session.commit()
                    else:
                        await async_session.flush()
                else:
                    session.execute(update_stmt)
                    if own_session:
                        session.commit()
                    else:
                        session.flush()
                
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error ensuring payment integrity: {e}")
            return False
        finally:
            if own_session and not is_async:
                session.close()
    
    @staticmethod
    async def validate_cashout_debit_exists(
        user_id: int,
        cashout_id: str,
        expected_amount: Optional[Decimal] = None,
        session=None
    ) -> Tuple[bool, str]:
        """
        Validate that a user's wallet was properly debited for a cashout.
        This is CRITICAL for preventing unauthorized refunds.
        
        Args:
            user_id: User who initiated the cashout
            cashout_id: Cashout ID to verify debit for
            expected_amount: Expected debit amount (optional)
            session: Database session (optional)
            
        Returns:
            (success: bool, error_message: str)
        """
        return await WalletValidator.validate_wallet_debit_completed(
            user_id=user_id,
            cashout_id=cashout_id,
            expected_amount=expected_amount,
            transaction_types=["cashout_debit", "cashout_hold"],
            session=session
        )
    
    @staticmethod
    async def validate_sufficient_balance(
        user_id: int,
        required_amount: Decimal,
        currency: str = "USD",
        session=None,
        include_frozen: bool = False,
        purpose: str = "transaction"
    ) -> Tuple[bool, str]:
        """
        Validate that a user has sufficient balance for a transaction.
        CRITICAL FIX: Now accounts for funds locked in pending/active escrows.
        
        Args:
            user_id: User to check balance for
            required_amount: Amount required for the transaction
            currency: Currency to check (default: USD)
            session: Database session (optional)
            include_frozen: Whether to include frozen balance in available funds
            purpose: Description of transaction purpose for error messages
            
        Returns:
            (success: bool, error_message: str)
        """
        own_session = session is None
        if own_session:
            session = SessionLocal()
        
        is_async = WalletValidator._is_async_session(session)
        
        try:
            # Convert to Decimal for precise calculations
            required_decimal = Decimal(str(required_amount))
            
            if required_decimal <= 0:
                return False, f"Invalid {purpose} amount: ${required_decimal:.2f}"
            
            # Get user wallet using SQLAlchemy 2.0 select pattern
            stmt = select(Wallet).where(
                Wallet.user_id == user_id,
                Wallet.currency == currency
            )
            
            if is_async:
                async_session = cast(AsyncSession, session)
                result = await async_session.execute(stmt)
            else:
                result = session.execute(stmt)
            wallet = result.scalar_one_or_none()
            
            if not wallet:
                return False, f"No {currency} wallet found for user"
            
            # Calculate available balance (base amount)
            wallet_available = Decimal(str(wallet.available_balance or 0))
            
            # CRITICAL BUG FIX: Subtract funds locked in pending/active escrows
            # This prevents users from creating multiple escrows exceeding their balance
            reserved_in_escrows = await WalletValidator.get_reserved_amount_in_escrows(
                user_id=user_id,
                currency=currency,
                session=session
            )
            
            # Calculate true withdrawable balance after subtracting locked funds
            withdrawable_balance = max(wallet_available - reserved_in_escrows, Decimal("0"))
            
            logger.info(
                f"User {user_id} balance breakdown: "
                f"wallet=${wallet_available:.2f}, "
                f"reserved=${reserved_in_escrows:.2f}, "
                f"withdrawable=${withdrawable_balance:.2f}"
            )
            
            # Start with withdrawable balance
            available_balance = withdrawable_balance
            
            # CRITICAL: Only include trading_credit for escrow/trade purposes, NOT for cashout
            if purpose not in ["cashout", "withdrawal"]:
                trading_credit = Decimal(str(wallet.trading_credit or 0))
                available_balance += trading_credit  # Trading credit can be used for escrow/exchange/fees
            
            if include_frozen:
                frozen_bal = wallet.frozen_balance
                frozen_amount = Decimal(str(frozen_bal)) if frozen_bal is not None else Decimal("0")
                available_balance += frozen_amount
            
            # Check if sufficient funds available (allow exact match with small tolerance for precision)
            tolerance = Decimal("0.01")  # 1 cent tolerance for floating point precision
            if available_balance < (required_decimal - tolerance):
                shortage = required_decimal - available_balance
                
                # Enhanced error message showing locked funds
                locked_info = ""
                if reserved_in_escrows > 0:
                    locked_info = f"\n🔒 Locked in Trades: ${reserved_in_escrows:.2f}"
                
                return False, (
                    f"Insufficient balance for {purpose}. "
                    f"Required: ${required_decimal:.2f}, "
                    f"Available: ${available_balance:.2f}, "
                    f"Shortage: ${shortage:.2f}"
                    f"{locked_info}"
                )
            
            logger.info(
                f"Balance validation passed for user {user_id}: "
                f"Required ${required_decimal:.2f}, Available ${available_balance:.2f} "
                f"(withdrawable: ${withdrawable_balance:.2f}, trading_credit: ${Decimal(str(wallet.trading_credit or 0)):.2f})"
            )
            
            return True, "Sufficient balance available"
            
        except Exception as e:
            logger.error(f"Error validating sufficient balance: {e}")
            return False, f"Balance validation error: {str(e)}"
        finally:
            if own_session and not is_async:
                session.close()
    
    @staticmethod
    async def validate_cashout_amount(
        user_id: int,
        cashout_amount: Decimal,
        estimated_fees: Optional[Decimal] = None,
        currency: str = "USD",
        session=None
    ) -> Tuple[bool, str]:
        """
        Validate cashout amount against available balance including fees.
        
        Args:
            user_id: User initiating the cashout
            cashout_amount: Amount user wants to cash out
            estimated_fees: Estimated fees for the cashout (optional)
            currency: Currency for the cashout (default: USD)
            session: Database session (optional)
            
        Returns:
            (success: bool, error_message: str)
        """
        own_session = session is None
        if own_session:
            session = SessionLocal()
        
        is_async = WalletValidator._is_async_session(session)
        
        try:
            # Convert to Decimal for precise calculations
            cashout_decimal = Decimal(str(cashout_amount))
            fees_decimal = Decimal(str(estimated_fees or 0))
            
            # For cashouts, fee is deducted FROM the amount, not added on top
            # User only needs the cashout amount in their balance
            total_required = cashout_decimal
            net_amount = cashout_decimal - fees_decimal
            
            # Validate basic amount constraints
            if cashout_decimal <= 0:
                return False, "Cashout amount must be greater than zero"
            
            if net_amount <= 0:
                return False, f"Cashout amount (${cashout_decimal:.2f}) must be greater than fees (${fees_decimal:.2f})"
            
            # Check sufficient balance (don't include frozen funds for cashouts)
            is_valid, error_msg = await WalletValidator.validate_sufficient_balance(
                user_id=user_id,
                required_amount=total_required,
                currency=currency,
                session=session,
                include_frozen=False,
                purpose="cashout"
            )
            
            if not is_valid:
                # Enhance error message with cashout-specific details
                if "Insufficient balance" in error_msg:
                    stmt = select(Wallet).where(
                        Wallet.user_id == user_id,
                        Wallet.currency == currency
                    )
                    
                    if is_async:
                        async_session = cast(AsyncSession, session)
                        result = await async_session.execute(stmt)
                        wallet = result.scalar_one_or_none()
                    else:
                        result = session.execute(stmt)
                        wallet = result.scalar_one_or_none()
                    
                    if wallet:
                        available = Decimal(str(wallet.available_balance))
                        fee_text = f" (${fees_decimal:.2f} fee deducted, ${net_amount:.2f} net)" if fees_decimal > 0 else ""
                        
                        return False, (
                            f"Insufficient balance for cashout. "
                            f"Need: ${cashout_decimal:.2f}{fee_text}, "
                            f"Available: ${available:.2f}"
                        )
                
                return False, error_msg
            
            logger.info(
                f"Cashout validation passed for user {user_id}: "
                f"${cashout_decimal:.2f} (${fees_decimal:.2f} fee, ${net_amount:.2f} net)"
            )
            
            return True, "Cashout amount validated successfully"
            
        except Exception as e:
            logger.error(f"Error validating cashout amount: {e}")
            return False, f"Cashout validation error: {str(e)}"
        finally:
            if own_session and not is_async:
                session.close()
    
    @staticmethod
    async def validate_payment_amount(
        user_id: int,
        payment_amount: Decimal,
        escrow_fees: Optional[Decimal] = None,
        currency: str = "USD",
        session=None,
        escrow_id: Optional[str] = None
    ) -> Tuple[bool, str]:
        """
        Validate escrow payment amount against available balance including fees.
        
        Args:
            user_id: User making the payment (buyer)
            payment_amount: Amount to be paid for the escrow
            escrow_fees: Escrow service fees (optional)
            currency: Currency for the payment (default: USD)
            session: Database session (optional)
            escrow_id: Escrow ID for logging purposes (optional)
            
        Returns:
            (success: bool, error_message: str)
        """
        own_session = session is None
        if own_session:
            session = SessionLocal()
        
        is_async = WalletValidator._is_async_session(session)
        
        try:
            # Convert to Decimal for precise calculations
            payment_decimal = Decimal(str(payment_amount))
            fees_decimal = Decimal(str(escrow_fees or 0))
            
            # Calculate total amount needed (payment + fees)
            total_required = payment_decimal + fees_decimal
            
            # Validate basic amount constraints
            if payment_decimal <= 0:
                return False, "Payment amount must be greater than zero"
            
            if total_required <= 0:
                return False, "Invalid total payment amount including fees"
            
            # Check sufficient balance (don't include frozen funds for payments)
            is_valid, error_msg = await WalletValidator.validate_sufficient_balance(
                user_id=user_id,
                required_amount=total_required,
                currency=currency,
                session=session,
                include_frozen=False,
                purpose="escrow payment"
            )
            
            if not is_valid:
                # Enhance error message with payment-specific details
                if "Insufficient balance" in error_msg:
                    stmt = select(Wallet).where(
                        Wallet.user_id == user_id,
                        Wallet.currency == currency
                    )
                    
                    if is_async:
                        async_session = cast(AsyncSession, session)
                        result = await async_session.execute(stmt)
                        wallet = result.scalar_one_or_none()
                    else:
                        result = session.execute(stmt)
                        wallet = result.scalar_one_or_none()
                    
                    if wallet:
                        available = Decimal(str(wallet.available_balance))
                        fee_text = f" + ${fees_decimal:.2f} fees" if fees_decimal > 0 else ""
                        escrow_text = f" for escrow {escrow_id}" if escrow_id else ""
                        
                        return False, (
                            f"Insufficient balance for payment{escrow_text}. "
                            f"Required: ${payment_decimal:.2f}{fee_text} = ${total_required:.2f}, "
                            f"Available: ${available:.2f}. "
                            f"Please add funds to your wallet before proceeding."
                        )
                
                return False, error_msg
            
            logger.info(
                f"Payment validation passed for user {user_id}: "
                f"${payment_decimal:.2f} + ${fees_decimal:.2f} fees = ${total_required:.2f}"
                f"{f' for escrow {escrow_id}' if escrow_id else ''}"
            )
            
            return True, "Payment amount validated successfully"
            
        except Exception as e:
            logger.error(f"Error validating payment amount: {e}")
            return False, f"Payment validation error: {str(e)}"
        finally:
            if own_session and not is_async:
                session.close()
    
    @staticmethod
    async def get_balance_summary(
        user_id: int,
        currency: str = "USD",
        session=None
    ) -> Tuple[bool, Dict[str, Decimal], str]:
        """
        Get detailed balance summary for a user's wallet.
        
        Args:
            user_id: User to get balance summary for
            currency: Currency to check (default: USD)
            session: Database session (optional)
            
        Returns:
            (success: bool, balance_dict: Dict[str, Decimal], error_message: str)
        """
        own_session = session is None
        if own_session:
            session = SessionLocal()
        
        is_async = WalletValidator._is_async_session(session)
        
        try:
            # Get user wallet using SQLAlchemy 2.0 select pattern
            stmt = select(Wallet).where(
                Wallet.user_id == user_id,
                Wallet.currency == currency
            )
            
            if is_async:
                async_session = cast(AsyncSession, session)
                result = await async_session.execute(stmt)
            else:
                result = session.execute(stmt)
            wallet = result.scalar_one_or_none()
            
            if not wallet:
                return False, {}, f"No {currency} wallet found for user"
            
            balance_summary = {
                "available_balance": Decimal(str(wallet.available_balance)),
                "frozen_balance": Decimal(str(wallet.frozen_balance or 0)),
                "total_balance": Decimal(str(wallet.available_balance)) + Decimal(str(wallet.frozen_balance or 0))
            }
            
            return True, balance_summary, "Balance summary retrieved successfully"
            
        except Exception as e:
            logger.error(f"Error getting balance summary: {e}")
            return False, {}, f"Balance summary error: {str(e)}"
        finally:
            if own_session and not is_async:
                session.close()
    
    @staticmethod
    def validate_minimum_cashout_amount(
        amount: Decimal,
        network: str = "USDT",
        currency: str = "USD"
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Validate if cashout amount meets minimum requirements before fee calculation.
        
        Args:
            amount: Cashout amount to validate
            network: Payment network (USDT, BTC, etc.)
            currency: Currency for cashout (default: USD)
            
        Returns:
            (success: bool, message: str, fee_info: Dict)
        """
        try:
            # Import here to avoid circular imports
            from services.percentage_cashout_fee_service import percentage_cashout_fee_service
            
            amount_decimal = Decimal(str(amount))
            
            # Basic amount validation
            if amount_decimal <= 0:
                return False, "Amount must be greater than zero", {}
            
            # Get fee calculation result from the service
            fee_result = percentage_cashout_fee_service.calculate_cashout_fee(amount_decimal, network)
            
            if not fee_result["success"]:
                # Extract specific error guidance
                error_msg = fee_result.get("error", "Invalid amount")
                
                # Provide enhanced guidance with minimum viable amount
                if "suggested_minimum" in fee_result:
                    min_amount = fee_result["suggested_minimum"]
                    enhanced_msg = (
                        f"{error_msg}\n\n"
                        f"💡 Try ${min_amount:.2f} or more for a viable cashout."
                    )
                    return False, enhanced_msg, fee_result
                else:
                    return False, error_msg, fee_result
            
            # Success case - amount is viable
            final_fee = fee_result["final_fee"]
            net_amount = fee_result["net_amount"]
            
            success_msg = (
                f"✅ Amount ${amount_decimal:.2f} is valid\n"
                f"📝 Fee: ${final_fee:.2f} ({fee_result['fee_percentage']}%)\n"
                f"💰 You'll receive: ${net_amount:.2f}"
            )
            
            return True, success_msg, fee_result
            
        except Exception as e:
            logger.error(f"Error validating minimum cashout amount: {e}")
            return False, f"Validation error: {str(e)}", {}
    
    @staticmethod
    def get_minimum_viable_cashout_amount(
        network: str = "USDT",
        currency: str = "USD"
    ) -> Tuple[Decimal, Dict[str, Any]]:
        """
        Calculate the minimum viable cashout amount for a given network.
        
        Args:
            network: Payment network (USDT, BTC, etc.)
            currency: Currency for cashout (default: USD)
            
        Returns:
            (minimum_amount: Decimal, fee_info: Dict)
        """
        try:
            from services.percentage_cashout_fee_service import percentage_cashout_fee_service
            
            # Try progressively larger amounts to find the minimum viable one
            test_amounts = [
                Decimal('0.50'), Decimal('1.00'), Decimal('2.00'), 
                Decimal('2.50'), Decimal('3.00'), Decimal('5.00'), 
                Decimal('10.00'), Decimal('25.00')
            ]
            
            for test_amount in test_amounts:
                fee_result = percentage_cashout_fee_service.calculate_cashout_fee(test_amount, network)
                
                if fee_result["success"]:
                    logger.info(f"Minimum viable cashout amount for {network}: ${test_amount}")
                    return test_amount, fee_result
            
            # Fallback - use suggested minimum from a failed calculation
            fee_result = percentage_cashout_fee_service.calculate_cashout_fee(Decimal('1.00'), network)
            if "suggested_minimum" in fee_result:
                suggested = fee_result["suggested_minimum"]
                logger.info(f"Using suggested minimum for {network}: ${suggested}")
                return suggested, fee_result
            
            # Final fallback
            fallback_amount = Decimal('2.50')
            logger.warning(f"Using fallback minimum amount: ${fallback_amount}")
            return fallback_amount, {"success": False, "error": "Could not determine minimum"}
            
        except Exception as e:
            logger.error(f"Error calculating minimum viable cashout amount: {e}")
            return Decimal('2.50'), {"success": False, "error": str(e)}
    
    @staticmethod
    async def validate_cashout_amount_with_fees(
        user_id: int,
        cashout_amount: Decimal,
        network: str = "USDT",
        currency: str = "USD",
        session=None
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Comprehensive cashout validation including minimum amounts, fees, and balance.
        
        Args:
            user_id: User initiating the cashout
            cashout_amount: Amount user wants to cash out
            network: Payment network (USDT, BTC, etc.)
            currency: Currency for cashout (default: USD)
            session: Database session (optional)
            
        Returns:
            (success: bool, message: str, validation_info: Dict)
        """
        own_session = session is None
        if own_session:
            session = SessionLocal()
        
        is_async = WalletValidator._is_async_session(session)
        
        try:
            # Step 1: Validate minimum amount requirements
            is_min_valid, min_msg, fee_info = WalletValidator.validate_minimum_cashout_amount(
                cashout_amount, network, currency
            )
            
            if not is_min_valid:
                return False, min_msg, fee_info
            
            # Step 2: Check user's available balance
            total_cost = cashout_amount  # User pays the full amount, fee is deducted from it
            
            is_balance_valid, balance_msg = await WalletValidator.validate_sufficient_balance(
                user_id=user_id,
                required_amount=total_cost,
                currency=currency,
                session=session,
                include_frozen=False,
                purpose="cashout"
            )
            
            if not is_balance_valid:
                # Enhance balance error with fee context
                fee_amount = fee_info.get("final_fee", Decimal('0'))
                net_amount = fee_info.get("net_amount", Decimal('0'))
                
                enhanced_balance_msg = (
                    f"{balance_msg}\n\n"
                    f"💡 Cashout breakdown:\n"
                    f"• Amount requested: ${cashout_amount:.2f}\n"
                    f"• Processing fee: ${fee_amount:.2f}\n"
                    f"• You would receive: ${net_amount:.2f}"
                )
                
                return False, enhanced_balance_msg, fee_info
            
            # Step 3: Success - provide complete breakdown
            fee_amount = fee_info.get("final_fee", Decimal('0'))
            net_amount = fee_info.get("net_amount", Decimal('0'))
            fee_percentage = fee_info.get("fee_percentage", Decimal('2.0'))
            
            success_msg = (
                f"✅ Cashout validated successfully!\n\n"
                f"💰 Amount: ${cashout_amount:.2f}\n"
                f"📝 Fee: ${fee_amount:.2f} ({fee_percentage}%)\n"
                f"💵 You'll receive: ${net_amount:.2f}\n"
                f"🌐 Network: {network}"
            )
            
            return True, success_msg, fee_info
            
        except Exception as e:
            logger.error(f"Error in comprehensive cashout validation: {e}")
            return False, f"Validation error: {str(e)}", {}
        finally:
            if own_session and not is_async:
                session.close()
    
    @staticmethod
    async def get_cashout_guidance(
        user_id: int,
        currency: str = "USD",
        network: str = "USDT",
        session=None
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Provide user-friendly guidance for cashout requirements and minimums.
        
        Args:
            user_id: User to provide guidance for
            currency: Currency for cashout (default: USD)
            network: Payment network (USDT, BTC, etc.)
            session: Database session (optional)
            
        Returns:
            (success: bool, guidance_message: str, info: Dict)
        """
        own_session = session is None
        if own_session:
            session = SessionLocal()
        
        is_async = WalletValidator._is_async_session(session)
        
        try:
            # Get user's current balance
            is_valid, balance_info, error_msg = await WalletValidator.get_balance_summary(
                user_id=user_id,
                currency=currency,
                session=session
            )
            
            if not is_valid:
                return False, error_msg, {}
            
            # Get minimum viable cashout amount
            min_amount, fee_info = WalletValidator.get_minimum_viable_cashout_amount(network, currency)
            
            available_balance = balance_info.get("available_balance", Decimal('0'))
            
            # Build guidance message
            guidance = (
                f"💰 Your Balance: ${available_balance:.2f}\n"
                f"💵 Minimum Cashout: ${min_amount:.2f}\n\n"
            )
            
            if available_balance >= min_amount:
                guidance += (
                    f"✅ You can cash out up to ${available_balance:.2f}\n"
                    f"📝 Processing fees apply (typically 2-5%)\n"
                    f"🌐 Network: {network}"
                )
            else:
                shortage = min_amount - available_balance
                guidance += (
                    f"⚠️ Insufficient balance for cashout\n"
                    f"💡 Add ${shortage:.2f} more to meet minimum\n"
                    f"🌐 Network: {network}"
                )
            
            info = {
                "balance": balance_info,
                "minimum_amount": min_amount,
                "can_cashout": available_balance >= min_amount,
                "network": network
            }
            
            return True, guidance, info
            
        except Exception as e:
            logger.error(f"Error getting cashout guidance: {e}")
            return False, f"Guidance error: {str(e)}", {}
        finally:
            if own_session and not is_async:
                session.close()
