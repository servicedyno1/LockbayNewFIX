"""Security service for cashout and transaction validation"""

import logging
from datetime import datetime, timedelta
from typing import Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func
from models import Cashout, User
from services.crypto import CryptoServiceAtomic

logger = logging.getLogger(__name__)


class SecurityService:
    """Comprehensive security validation for cashouts and transactions"""

    # Security thresholds
    MAX_DAILY_CASHOUT_RATIO = 1.5  # Max 150% of balance per day
    MIN_ADDRESS_LENGTH = 26
    MAX_ADDRESS_LENGTH = 62
    SUSPICIOUS_CASHOUT_THRESHOLD = 0.8  # 80% of balance in one go

    @classmethod
    async def validate_cashout_security(
        cls, cashout: Cashout, session: Session, admin_id: int
    ) -> Tuple[bool, str]:
        """
        Comprehensive security validation for cashout approval
        Returns: (is_valid, error_message)
        """
        try:
            # 1. Balance Validation
            is_valid, message = await cls._validate_sufficient_balance(cashout)
            if not is_valid:
                logger.warning(
                    f"SECURITY: Balance validation failed for cashout {cashout.id} - {message}"
                )
                return False, message

            # 2. State Validation
            is_valid, message = cls._validate_cashout_state(cashout)
            if not is_valid:
                logger.warning(
                    f"SECURITY: State validation failed for cashout {cashout.id} - {message}"
                )
                return False, message

            # 3. Address Validation
            is_valid, message = cls._validate_address_security(cashout)
            if not is_valid:
                logger.warning(
                    f"SECURITY: Address validation failed for cashout {cashout.id} - {message}"
                )
                return False, message

            # 4. Pattern Analysis
            is_valid, message = await cls._analyze_cashout_patterns(
                cashout, session
            )
            if not is_valid:
                logger.warning(
                    f"SECURITY: Pattern analysis failed for cashout {cashout.id} - {message}"
                )
                return False, message

            # 5. Risk Assessment
            risk_level, risk_message = await cls._assess_cashout_risk(
                cashout, session
            )
            if risk_level == "HIGH":
                logger.warning(
                    f"SECURITY: High risk cashout detected {cashout.id} - {risk_message}"
                )
                return (
                    False,
                    f"❌ High Risk Transaction - Manual Review Required\n\n{risk_message}",
                )

            # All validations passed
            logger.info(
                f"SECURITY: All security checks passed for cashout {cashout.id} by admin {admin_id}"
            )
            return True, "Security validation passed"

        except Exception as e:
            logger.error(
                f"SECURITY: Error during security validation for cashout {cashout.id}: {e}"
            )
            return (
                False,
                "❌ Security Check Failed\n\nSystem error during validation. Approval blocked.",
            )

    @classmethod
    async def _validate_sufficient_balance(
        cls, cashout: Cashout
    ) -> Tuple[bool, str]:
        """Validate user has sufficient balance (including locked balance for existing cashouts)"""
        try:
            # Get total wallet balance including locked funds
            from database import SessionLocal
            from models import Wallet
            
            session = SessionLocal()
            try:
                wallet = session.query(Wallet).filter_by(
                    user_id=cashout.user_id, currency="USD"
                ).first()
                
                if not wallet:
                    return False, "❌ USD wallet not found"
                
                # For existing cashouts, funds are already locked - check total balance
                total_balance = float(wallet.available_balance) + float(wallet.locked_balance)
                
                # The cashout amount should be covered by total balance (including locked)
                if total_balance < cashout.amount:
                    error_msg = "❌ Insufficient Balance - Approval BLOCKED\n\n"
                    error_msg += "Security Check Failed:\n"
                    error_msg += f"├ Request: ${cashout.amount:.2f} USD\n"
                    error_msg += f"├ Total Balance: ${total_balance:.2f} USD\n"
                    error_msg += f"├ Available: ${float(wallet.available_balance):.2f} USD\n"
                    error_msg += f"├ Locked: ${float(wallet.locked_balance):.2f} USD\n"
                    error_msg += (
                        f"├ Shortfall: ${cashout.amount - total_balance:.2f} USD\n"
                    )
                    error_msg += "└ Action: CANNOT APPROVE\n\n"
                    error_msg += "⚠️ User must add funds before approval can proceed."
                    return False, error_msg
                
                return True, ""
                
            finally:
                session.close()
                
        except Exception as e:
            return False, f"Could not verify user balance: {str(e)}"

    @classmethod
    def _validate_cashout_state(cls, cashout: Cashout) -> Tuple[bool, str]:
        """Validate cashout is in correct state for approval"""
        if cashout.status != "pending":
            error_msg = "❌ Invalid State - Approval BLOCKED\n\n"
            error_msg += (
                f"Cashout #{cashout.id} status: {cashout.status.upper()}\n"
            )
            error_msg += "Only PENDING cashouts can be approved."
            return False, error_msg
        return True, ""

    @classmethod
    def _validate_address_security(cls, cashout: Cashout) -> Tuple[bool, str]:
        """Validate cashout address security"""
        address = cashout.to_address

        # Basic length validation
        if (
            not address
            or len(address) < cls.MIN_ADDRESS_LENGTH
            or len(address) > cls.MAX_ADDRESS_LENGTH
        ):
            return (
                False,
                "❌ Invalid Address - Approval BLOCKED\n\nAddress length validation failed for security.",
            )

        # Network-specific validation
        if cashout.network in ["TRC20", "TRON"]:
            if not address.startswith("T") or len(address) != 34:
                return (
                    False,
                    "❌ Invalid TRC20 Address - Approval BLOCKED\n\nAddress format validation failed.",
                )
        elif cashout.network in ["ERC20", "ETH", "ETHEREUM"]:
            if not address.startswith("0x") or len(address) != 42:
                return (
                    False,
                    "❌ Invalid ERC20 Address - Approval BLOCKED\n\nAddress format validation failed.",
                )

        # Check for obviously invalid patterns
        if address.count("0") > len(address) * 0.7:  # Too many zeros
            return (
                False,
                "❌ Suspicious Address Pattern - Approval BLOCKED\n\nAddress appears invalid.",
            )

        return True, ""

    @classmethod
    async def _analyze_cashout_patterns(
        cls, cashout: Cashout, session: Session
    ) -> Tuple[bool, str]:
        """Analyze cashout patterns for suspicious activity"""
        try:
            # Check recent cashout volume
            recent_cashouts = (
                session.query(func.sum(Cashout.amount))
                .filter(
                    Cashout.user_id == cashout.user_id,
                    Cashout.status.in_(["completed", "processing"]),
                    Cashout.created_at
                    >= cashout.created_at - timedelta(hours=24),
                )
                .scalar()
                or 0
            )

            current_balance = CryptoServiceAtomic.get_user_balance_atomic(
                cashout.user_id, "USD"
            )
            total_attempted = recent_cashouts + cashout.amount

            if total_attempted > current_balance * cls.MAX_DAILY_CASHOUT_RATIO:
                error_msg = "❌ Suspicious Activity - Approval BLOCKED\n\n"
                error_msg += (
                    f"User attempting to withdraw ${total_attempted:.2f} USD in 24h\n"
                )
                error_msg += f"against balance of ${current_balance:.2f} USD.\n"
                error_msg += f"Exceeds security threshold of {cls.MAX_DAILY_CASHOUT_RATIO * 100}%.\n\n"
                error_msg += "Manual security review required."
                return False, error_msg

            # Check for rapid successive cashouts
            recent_count = (
                session.query(Cashout)
                .filter(
                    Cashout.user_id == cashout.user_id,
                    Cashout.created_at >= cashout.created_at - timedelta(hours=1),
                )
                .count()
            )

            if recent_count > 3:  # More than 3 cashouts in 1 hour
                error_msg = "❌ Rapid Cashout Pattern - Approval BLOCKED\n\n"
                error_msg += (
                    f"User has {recent_count} cashout requests in past hour.\n"
                )
                error_msg += "Automated protection triggered. Manual review required."
                return False, error_msg

            return True, ""

        except Exception as e:
            logger.error(f"Error analyzing cashout patterns: {e}")
            return False, f"Pattern analysis failed: {str(e)}"

    @classmethod
    async def _assess_cashout_risk(
        cls, cashout: Cashout, session: Session
    ) -> Tuple[str, str]:
        """Assess overall risk level of cashout"""
        try:
            risk_factors = []
            risk_score = 0

            current_balance = CryptoServiceAtomic.get_user_balance_atomic(
                cashout.user_id, "USD"
            )

            # Risk Factor 1: Large percentage of balance
            if (
                cashout.amount
                > current_balance * cls.SUSPICIOUS_CASHOUT_THRESHOLD
            ):
                risk_factors.append(
                    f"Large cashout: {(cashout.amount/current_balance)*100:.1f}% of balance"
                )
                risk_score += 3

            # Risk Factor 2: New user (first cashout)
            user_cashouts = (
                session.query(Cashout)
                .filter(
                    Cashout.user_id == cashout.user_id,
                    Cashout.status == "completed",
                )
                .count()
            )

            if user_cashouts == 0:
                risk_factors.append("First-time cashout")
                risk_score += 2

            # Risk Factor 3: High amount
            if cashout.amount > 1000:
                risk_factors.append(f"High amount: ${cashout.amount:.2f}")
                risk_score += 2

            # Risk Factor 4: Recent account activity
            user = session.query(User).filter(User.id == cashout.user_id).first()
            if (
                user
                and user.created_at
                and (datetime.utcnow() - user.created_at).days < 7
            ):
                risk_factors.append("Account less than 7 days old")
                risk_score += 2

            # Determine risk level
            if risk_score >= 7:
                risk_level = "HIGH"
            elif risk_score >= 4:
                risk_level = "MEDIUM"
            else:
                risk_level = "LOW"

            risk_message = f"Risk Level: {risk_level} (Score: {risk_score})\n"
            if risk_factors:
                risk_message += "Risk Factors:\n" + "\n".join(
                    [f"• {factor}" for factor in risk_factors]
                )

            return risk_level, risk_message

        except Exception as e:
            logger.error(f"Error assessing cashout risk: {e}")
            return "HIGH", f"Risk assessment failed: {str(e)}"

    @classmethod
    async def log_security_event(
        cls, event_type: str, cashout_id: int, admin_id: int, details: str
    ):
        """Log security events for audit trail"""
        timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        logger.info(
            f"SECURITY EVENT [{event_type}] - Cashout: {cashout_id}, Admin: {admin_id}, Time: {timestamp}, Details: {details}"
        )
