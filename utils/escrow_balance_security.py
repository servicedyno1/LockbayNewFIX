"""
Simplified Balance Security Module
Critical security functions using proper database columns and real-time locking
SECURITY: No caching for critical decisions - always uses fresh locked database reads
"""

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)


def calculate_available_wallet_balance(user_id: int) -> Decimal:
    """
    Get available wallet balance using proper database columns with locking - CRITICAL: Returns Decimal for precision
    SECURITY: Uses real-time locked reads - no caching for critical security decisions
    FIXED: available_balance is already net of holds - no double subtraction
    """
    from utils.atomic_transactions import atomic_transaction
    from models import Wallet

    try:
        with atomic_transaction() as session:
            # Start transaction first, then lock
            session.begin()
            
            # SECURITY: Use SELECT FOR UPDATE to lock the wallet during balance checks
            wallet = (
                session.query(Wallet)
                .filter(Wallet.user_id == user_id, Wallet.currency == "USD")
                .with_for_update()  # LOCK THE ROW for consistency
                .first()
            )

            if not wallet:
                logger.warning(f"No USD wallet found for user {user_id}")
                return Decimal("0.0")

            # FIXED: available_balance is already the spendable amount (net of holds)
            available = Decimal(str(wallet.available_balance or 0))
            frozen = Decimal(str(wallet.frozen_balance or 0))
            
            # Ensure non-negative (safety check)
            final_available = max(available, Decimal("0"))

            # CRITICAL: Format without implicit float conversion
            avail_fmt = available.quantize(Decimal("0.01"))
            frozen_fmt = frozen.quantize(Decimal("0.01"))
            logger.info(
                f"SECURITY: User {user_id} - Available: ${avail_fmt}, "
                f"Frozen: ${frozen_fmt} (invariant: available is spendable)"
            )

            return final_available

    except Exception as e:
        logger.error(f"SECURITY ERROR calculating balance for user {user_id}: {e}")
        # Return 0 on error to be safe - conservative approach
        return Decimal("0.0")


def create_fund_hold(user_id: int, amount: Decimal | float, hold_type: str, reference_id: str) -> bool:
    """
    Create a fund hold by moving money from available_balance to frozen_balance - CRITICAL: Accepts Decimal for precision
    SIMPLIFIED: Uses proper database columns with atomic transaction
    """
    from utils.atomic_transactions import atomic_transaction
    from models import Wallet

    try:
        with atomic_transaction() as session:
            # Lock the wallet row during fund hold operation
            wallet = (
                session.query(Wallet)
                .filter(Wallet.user_id == user_id, Wallet.currency == "USD")
                .with_for_update()
                .first()
            )

            if not wallet:
                logger.error(f"No wallet found for user {user_id}")
                return False

            available = Decimal(str(wallet.available_balance or 0))
            frozen = Decimal(str(wallet.frozen_balance or 0))
            hold_amount = Decimal(str(amount))

            if available < hold_amount:
                # CRITICAL: Format without implicit float conversion
                avail_fmt = available.quantize(Decimal("0.01"))
                hold_fmt = hold_amount.quantize(Decimal("0.01"))
                logger.warning(
                    f"Insufficient funds for hold: User {user_id}, "
                    f"Available: ${avail_fmt}, Requested: ${hold_fmt}"
                )
                return False

            # Move money from available to frozen
            wallet.available_balance = available - hold_amount
            wallet.frozen_balance = frozen + hold_amount

            # CRITICAL: Format without implicit float conversion
            hold_fmt = hold_amount.quantize(Decimal("0.01"))
            logger.info(
                f"FUND_HOLD: User {user_id} - Moved ${hold_fmt} to frozen "
                f"({hold_type}:{reference_id})"
            )
            
            session.commit()
            return True

    except Exception as e:
        logger.error(f"Error creating fund hold for user {user_id}: {e}")
        return False


def release_fund_hold(user_id: int, amount: Decimal | float, reference_id: str) -> bool:
    """
    Release a fund hold by moving money from frozen_balance back to available_balance - CRITICAL: Accepts Decimal for precision
    SIMPLIFIED: Uses proper database columns with atomic transaction
    """
    from utils.atomic_transactions import atomic_transaction
    from models import Wallet

    try:
        with atomic_transaction() as session:
            # Lock the wallet row during fund release operation
            wallet = (
                session.query(Wallet)
                .filter(Wallet.user_id == user_id, Wallet.currency == "USD")
                .with_for_update()
                .first()
            )

            if not wallet:
                logger.error(f"No wallet found for user {user_id}")
                return False

            available = Decimal(str(wallet.available_balance or 0))
            frozen = Decimal(str(wallet.frozen_balance or 0))
            release_amount = Decimal(str(amount))

            if frozen < release_amount:
                # CRITICAL: Format without implicit float conversion
                frozen_fmt = frozen.quantize(Decimal("0.01"))
                release_fmt = release_amount.quantize(Decimal("0.01"))
                logger.warning(
                    f"Insufficient frozen funds for release: User {user_id}, "
                    f"Frozen: ${frozen_fmt}, Requested: ${release_fmt}"
                )
                return False

            # Move money from frozen back to available
            wallet.available_balance = available + release_amount
            wallet.frozen_balance = frozen - release_amount

            # CRITICAL: Format without implicit float conversion
            release_fmt = release_amount.quantize(Decimal("0.01"))
            logger.info(
                f"FUND_RELEASE: User {user_id} - Released ${release_fmt} from frozen "
                f"({reference_id})"
            )
            
            session.commit()
            return True

    except Exception as e:
        logger.error(f"Error releasing fund hold for user {user_id}: {e}")
        return False


def verify_sufficient_funds_for_escrow(
    buyer_id: int,
    required_amount: Decimal | float,
    payment_method: str = "wallet"
) -> tuple[bool, str]:
    """
    Verify user has sufficient funds for escrow creation - CRITICAL: Accepts Decimal for precision
    SIMPLIFIED: Uses proper balance checking
    Returns (is_sufficient, error_message)
    """
    try:
        available_balance = calculate_available_wallet_balance(buyer_id)
        required_decimal = Decimal(str(required_amount))

        if payment_method == "wallet" and available_balance < required_decimal:
            # CRITICAL: Format without implicit float conversion
            avail_fmt = available_balance.quantize(Decimal("0.01"))
            req_fmt = required_decimal.quantize(Decimal("0.01"))
            return (
                False,
                f"Insufficient balance. Available: ${avail_fmt}, "
                f"Required: ${req_fmt}"
            )

        return True, ""

    except Exception as e:
        logger.error(f"Error verifying funds for user {buyer_id}: {e}")
        return False, "Error verifying funds. Please try again."
