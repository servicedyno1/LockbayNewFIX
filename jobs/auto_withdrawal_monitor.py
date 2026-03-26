#!/usr/bin/env python3
"""
Safe Auto-Cashout Monitor - Architect's Strategic Clean Rewrite
Monitors user balances and triggers automatic cashouts with proper async session patterns

Features:
- Use async with managed_session() pattern exclusively
- Operate on AsyncSession only, never pass sessionmaker objects
- Clean session lifecycle management
- Safe explicit destination auto-cashout processing
"""

import logging
from datetime import datetime, timedelta
from database import async_managed_session
from models import User, Wallet, Cashout, CashoutType
from sqlalchemy import select, update
from services.auto_cashout import AutoCashoutService
from decimal import Decimal
from utils.constants import CASHOUT_STATUSES_WITH_HOLDS

logger = logging.getLogger(__name__)


class AutoCashoutMonitor:
    """Monitor user balances and process automatic cashouts - Clean async patterns"""

    @staticmethod
    async def check_and_process_auto_cashouts():
        """
        DUAL-PURPOSE Auto-Cashout Logic with clean async patterns:
        1. Process pending manual cashouts marked for retry by admin
        2. Process automatic user cashouts (if admin settings allow)
        """
        processed_count = 0

        try:
            # Use clean async session management
            async with async_managed_session() as session:
                from config import Config

                # Admin controls override user settings
                if not (
                    Config.AUTO_CASHOUT_ENABLED_NGN
                    or Config.AUTO_CASHOUT_ENABLED_CRYPTO
                ):
                    logger.info(
                        "Admin auto-cashout controls are OFF - all cashouts require manual approval"
                    )
                    return processed_count

                # Admin controls are ON - process user auto-cashout preferences
                logger.info(
                    "Admin auto-cashout controls are ON - processing user auto-cashout settings"
                )

                # Get users with auto-cashout enabled and configured destinations
                result = await session.execute(
                    select(User)
                    .where(
                        User.auto_cashout_enabled == True,
                        (
                            (User.auto_cashout_crypto_address_id.isnot(None))
                            | (User.auto_cashout_bank_account_id.isnot(None))
                        ),
                    )
                )
                eligible_users = list(result.scalars())

                logger.info(
                    f"Checking auto-cashout for {len(eligible_users)} users with auto-cashout enabled"
                )

                for user in eligible_users:
                    try:
                        await AutoCashoutMonitor._process_user_auto_cashout(
                            user, session
                        )
                        processed_count += 1
                    except Exception as e:
                        logger.error(
                            f"Error processing auto-cashout for user {user.id}: {e}"
                        )
                        continue

                # Commit all changes
                await session.commit()

                logger.info(
                    f"Auto-cashout check completed: {processed_count} users processed"
                )
                
                return processed_count

        except Exception as e:
            logger.error(f"Error in auto-cashout monitoring: {e}")
            return 0

    @staticmethod
    async def _process_user_auto_cashout(user: User, session):
        """Process auto-cashout using smart destination detection - Clean async patterns"""
        
        # DUPLICATE PREVENTION: Check for recent auto-cashouts in last 10 minutes
        # CRITICAL: Only check for cashouts with actual holds to prevent skipping due to OTP_PENDING records
        result = await session.execute(
            select(Cashout)
            .where(
                Cashout.user_id == user.id,
                Cashout.created_at >= datetime.utcnow() - timedelta(minutes=10),
                Cashout.status.in_(CASHOUT_STATUSES_WITH_HOLDS)
            )
        )
        recent_cashout = result.scalar_one_or_none()
        
        if recent_cashout:
            logger.info(
                f"User {user.id} has recent cashout {recent_cashout.id} in progress - skipping auto-cashout"
            )
            return  # Skip if recent cashout exists

        # Get user's USD balance
        result = await session.execute(
            select(Wallet)
            .where(Wallet.user_id == user.id, Wallet.currency == "USD")
        )
        wallet = result.scalar_one_or_none()

        min_amount = getattr(user, 'min_auto_cashout_amount', 25.0) or 25.0
        if not wallet or wallet.available_balance < min_amount:
            return  # Balance too low for auto-cashout

        # Check for recent failed attempts - implement cooldown
        result = await session.execute(
            select(Cashout)
            .where(
                Cashout.user_id == user.id,
                Cashout.status == "failed",
                Cashout.created_at >= datetime.utcnow() - timedelta(hours=1),
            )
        )
        recent_failures = len(list(result.scalars()))

        if recent_failures >= 3:
            logger.info(
                f"User {user.id} has {recent_failures} failed cashouts in last hour - skipping auto-cashout"
            )
            return  # Too many recent failures, skip this attempt

        balance = wallet.available_balance

        # Smart destination detection - auto-detect based on configured destinations
        has_bank = getattr(user, 'auto_cashout_bank_account_id', None) is not None
        has_crypto = getattr(user, 'auto_cashout_crypto_address_id', None) is not None

        logger.info(
            f"User {user.id} balance ${balance:.2f}, min amount ${min_amount:.2f}, has_bank: {has_bank}, has_crypto: {has_crypto}"
        )

        if balance < min_amount:
            return

        # Process based on configured destinations (prefer bank if both configured)
        if has_bank:
            await AutoCashoutMonitor._process_ngn_auto_cashout(
                user, balance, session
            )
        elif has_crypto:
            await AutoCashoutMonitor._process_crypto_auto_cashout(
                user, balance, session
            )
        else:
            logger.warning(
                f"User {user.id} has auto-cashout enabled but no destinations configured"
            )

    @staticmethod
    async def _process_ngn_auto_cashout(user: User, balance: float, session):
        """Process NGN auto-cashout using explicit bank account - Clean async patterns"""

        # Verify explicit NGN bank account is configured and valid
        bank_account = getattr(user, 'auto_cashout_bank_account', None)
        if not bank_account:
            logger.warning(
                f"User {user.id} has NGN preference but no explicit bank account configured"
            )
            return

        # ENHANCED: Validate bank account still exists and has required fields
        if not all(
            [
                getattr(bank_account, 'account_number', None),
                getattr(bank_account, 'bank_code', None),
                getattr(bank_account, 'account_name', None),
            ]
        ):
            logger.error(
                f"User {user.id} bank account missing required fields - disabling auto cashout"
            )
            try:
                await session.execute(
                    update(User)
                    .where(User.id == user.id)
                    .values(auto_cashout_enabled=False)
                )
                await session.flush()
            except Exception as e:
                logger.error(f"Failed to disable auto-cashout for user {user.id}: {e}")
            return

        # ENHANCED: Validate account number format (10 digits for Nigerian banks)
        account_number = getattr(bank_account, 'account_number', '')
        if not (account_number.isdigit() and len(account_number) == 10):
            logger.error(
                f"User {user.id} bank account has invalid format - disabling auto cashout"
            )
            try:
                await session.execute(
                    update(User)
                    .where(User.id == user.id)
                    .values(auto_cashout_enabled=False)
                )
                await session.flush()
            except Exception as e:
                logger.error(f"Failed to disable auto-cashout for user {user.id}: {e}")
            return

        # Keep buffer in wallet and withdraw the rest
        balance_decimal = Decimal(str(balance))
        buffer_amount = Decimal("5.0")
        withdraw_amount = float(
            balance_decimal - buffer_amount
        )  # Keep $5 buffer in wallet

        try:
            # Create cashout request  
            # Format: bank_name|account_number|account_name|bank_code (required by AutoCashoutService)
            bank_destination = f"{getattr(bank_account, 'bank_name', 'Unknown Bank')}|{getattr(bank_account, 'account_number', '')}|{getattr(bank_account, 'account_name', 'Account Holder')}|{getattr(bank_account, 'bank_code', '')}"
            
            cashout_result = await AutoCashoutService.create_cashout_request(
                user_id=getattr(user, 'id'),
                amount=Decimal(str(withdraw_amount)),
                currency="USD",
                cashout_type=CashoutType.NGN_BANK.value,
                destination=bank_destination,
            )

            if cashout_result.get("success"):
                cashout_id = cashout_result.get("cashout_id")
                if cashout_id:
                    # Auto-approve and process for background auto-cashout
                    result = await AutoCashoutService.process_approved_cashout(
                        cashout_id=str(cashout_id),
                        admin_approved=True,
                    )
                else:
                    result = {"success": False, "error": "No cashout ID returned"}
            else:
                result = cashout_result

            if result.get("success"):
                logger.info(
                    f"NGN auto-cashout processed for user {user.id}: ${withdraw_amount:.2f}"
                )
            else:
                logger.error(
                    f"NGN auto-cashout failed for user {user.id}: {result.get('error', 'Unknown error')}"
                )

        except Exception as e:
            logger.error(f"Error processing NGN auto-cashout for user {user.id}: {e}")

    @staticmethod
    async def _process_crypto_auto_cashout(user: User, balance: float, session):
        """Process crypto auto-cashout using explicit address - Clean async patterns"""

        # Verify explicit crypto address is configured and valid
        crypto_address = getattr(user, 'auto_cashout_crypto_address', None)
        if not crypto_address:
            logger.warning(
                f"User {user.id} has crypto preference but no explicit address configured"
            )
            return

        # ENHANCED: Validate crypto address still exists and has required fields
        if not all(
            [
                getattr(crypto_address, 'address', None),
                getattr(crypto_address, 'currency', None),
                getattr(crypto_address, 'network', None),
            ]
        ):
            logger.error(
                f"User {user.id} crypto address missing required fields - disabling auto cashout"
            )
            try:
                await session.execute(
                    update(User)
                    .where(User.id == user.id)
                    .values(auto_cashout_enabled=False)
                )
                await session.flush()
            except Exception as e:
                logger.error(f"Failed to disable auto-cashout for user {user.id}: {e}")
            return

        # Keep buffer in wallet and withdraw the rest
        balance_decimal = Decimal(str(balance))
        buffer_amount = Decimal("5.0")
        withdraw_amount = float(
            balance_decimal - buffer_amount
        )  # Keep $5 buffer in wallet

        try:
            # Create cashout request
            cashout_result = await AutoCashoutService.create_cashout_request(
                user_id=getattr(user, 'id'),
                amount=Decimal(str(withdraw_amount)),
                currency=getattr(crypto_address, 'currency', 'USDT'),
                cashout_type=CashoutType.CRYPTO.value,
                destination=getattr(crypto_address, 'address', ''),
            )

            if cashout_result.get("success"):
                cashout_id = cashout_result.get("cashout_id")
                if cashout_id:
                    # Auto-approve and process for background auto-cashout
                    result = await AutoCashoutService.process_approved_cashout(
                        cashout_id=str(cashout_id),
                        admin_approved=True,
                    )
                else:
                    result = {"success": False, "error": "No cashout ID returned"}
            else:
                result = cashout_result

            if result.get("success"):
                logger.info(
                    f"Crypto auto-cashout processed for user {user.id}: ${withdraw_amount:.2f}"
                )
            else:
                logger.error(
                    f"Crypto auto-cashout failed for user {user.id}: {result.get('error', 'Unknown error')}"
                )

        except Exception as e:
            logger.error(f"Error processing crypto auto-cashout for user {user.id}: {e}")


# Export for scheduler integration
__all__ = ["AutoCashoutMonitor"]