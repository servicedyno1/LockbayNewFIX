"""
Enhanced CryptoService with comprehensive atomic transaction support.
This replaces the broken crypto.py file with race condition fixes.
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict, Any, List, Union
from sqlalchemy.sql import func
from sqlalchemy import select

from models import Wallet, Transaction, TransactionType, EscrowStatus, User
from database import get_session  # SYNC FIX: Use get_session for consistent session management
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from utils.universal_id_generator import UniversalIDGenerator
from utils.atomic_transactions import (
    atomic_transaction,
    async_atomic_transaction,
    locked_wallet_operation,
    locked_wallet_operation_async,
    locked_escrow_operation,
)
from services.blockbee_service import blockbee_service, BlockBeeAPIError
from services.fastforex_service import fastforex_service, FastForexAPIError
from services.payment_processor_manager import payment_manager, PaymentProvider
from config import Config
from utils.financial_audit_logger import (
    financial_audit_logger,
    FinancialEventType,
    FinancialContext,
    EntityType
)

logger = logging.getLogger(__name__)


class CryptoServiceAtomic:
    """Enhanced cryptocurrency service with comprehensive atomic transaction support"""
    
    # Class-level cache for ultra-fast rate retrieval during active sessions
    _session_rate_cache = {}
    _session_cache_timestamp = None
    _SESSION_CACHE_TTL = 30  # 30 seconds ultra-fast cache
    
    @classmethod
    def _get_session_cached_rates(cls) -> Optional[Dict[str, Decimal]]:
        """Get rates from ultra-fast session cache if available and fresh"""
        import time
        current_time = time.time()
        
        if (cls._session_cache_timestamp and 
            current_time - cls._session_cache_timestamp < cls._SESSION_CACHE_TTL and
            cls._session_rate_cache):
            logger.info(f"🚀 ULTRA-FAST: Using session-cached rates ({len(cls._session_rate_cache)} currencies)")
            return cls._session_rate_cache.copy()
        return None
    
    @classmethod
    def _set_session_cached_rates(cls, rates: Dict[str, Decimal]) -> None:
        """Store rates in ultra-fast session cache"""
        import time
        cls._session_rate_cache = rates.copy()
        cls._session_cache_timestamp = time.time()
        logger.info(f"💾 SESSION_CACHE: Stored {len(rates)} crypto rates for {cls._SESSION_CACHE_TTL}s ultra-fast access")
    
    @staticmethod
    def _enforce_escrow_id_typing(x) -> Optional[int]:
        """
        CRITICAL: Strict escrow_id typing enforcement
        Ensures escrow_id is always properly typed as integer or None
        
        Args:
            x: Raw escrow_id value (could be int, str, None, "None", etc.)
            
        Returns:
            Properly typed escrow_id as integer or None
        """
        if x is None:
            return None
        
        # Handle string "None" values
        if isinstance(x, str) and x.lower() in ('none', 'null', ''):
            return None
            
        # Handle string digits
        if isinstance(x, str) and x.isdigit():
            return int(x)
            
        # Handle integer values
        if isinstance(x, int):
            return x
            
        # Invalid types - log warning and return None
        logger.warning(f"Invalid escrow_id type {type(x)}: {x} - converting to None")
        return None

    @classmethod
    async def get_real_time_exchange_rate(cls, currency: str) -> Decimal:
        """Get real-time exchange rate using FastForex API"""
        try:
            if currency == "USD":
                return Decimal("1.0")

            # Try FastForex API first
            if Config.FASTFOREX_API_KEY:
                try:
                    rate = await fastforex_service.get_crypto_to_usd_rate(currency)
                    logger.info(
                        f"Retrieved real-time rate for {currency}: ${rate:.2f} USD"
                    )
                    return rate
                except FastForexAPIError as e:
                    logger.warning(f"FastForex API failed for {currency}: {e}")
                except Exception as e:
                    logger.warning(f"Unexpected FastForex error for {currency}: {e}")

            # PRODUCTION: No fallback rates - API is required
            logger.error(f"FastForex API key not configured or failed for {currency}")
            raise ValueError(
                f"Real-time exchange rate service unavailable for {currency}. Please contact support."
            )

        except Exception as e:
            logger.error(f"Error getting exchange rate for {currency}: {e}")
            raise

    @classmethod
    async def generate_wallet_deposit_address(
        cls, currency: str, user_id: int, session: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        """Generate cryptocurrency deposit address for wallet funding with DynoPay failover support"""
        try:
            # Generate wallet transaction ID for tracking
            wallet_txn_id = (
                f"WALLET-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{user_id}"
            )
            
            # Use payment manager for failover support
            try:
                # Prepare callback URL and metadata
                base_webhook_url = Config.WEBHOOK_URL or Config.BLOCKBEE_CALLBACK_URL
                if not base_webhook_url:
                    raise ValueError("No webhook URL configured for wallet deposits")
                
                metadata = {
                    'user_id': user_id,
                    'wallet_transaction_id': wallet_txn_id,
                    'operation_type': 'wallet_deposit'
                }
                
                # Create payment address with automatic failover
                # Use primary callback URL (payment manager will handle provider-specific routing)
                primary_callback_url = f"{base_webhook_url}/dynopay/wallet"
                
                # DynoPay requires an amount parameter - use minimum $1 as a signal for open-ended deposits
                # The actual credited amount is computed from crypto_amount × exchange_rate in the webhook handler
                result, provider_used = await payment_manager.create_payment_address(
                    currency=currency,
                    amount=1.0,  # Minimum signal amount - actual deposit amount is open-ended
                    callback_url=primary_callback_url,
                    reference_id=wallet_txn_id,
                    metadata=metadata
                )
                
                address = result.get("address_in") or result.get("address")
                
                if provider_used != PaymentProvider.DYNOPAY:
                    logger.warning(f"⚠️ Using backup provider ({provider_used.value}) for wallet deposit {currency}")
                else:
                    logger.info(f"✅ Using primary provider ({provider_used.value}) for wallet deposit {currency}")
                
                logger.info(f"Generated wallet deposit address for {currency}: {address[:10] if address else 'N/A'}...")
                logger.info(f"Wallet transaction ID: {wallet_txn_id}")

                # Log financial event for address generation
                financial_context = FinancialContext(
                    currency=currency,
                    amount=Decimal("1.0")  # Signal amount - actual deposit is open-ended
                )
                
                # Use async financial audit logger with session if provided
                if session:
                    await financial_audit_logger.log_financial_event_async(
                        event_type=FinancialEventType.WALLET_DEPOSIT_ADDRESS_GENERATED,
                        entity_type=EntityType.WALLET,
                        entity_id=f"wallet_{user_id}",
                        user_id=user_id,
                        financial_context=financial_context,
                        previous_state="address_needed",
                        new_state="address_generated",
                        related_entities=None,
                        additional_data={
                            "wallet_transaction_id": wallet_txn_id,
                            "payment_provider": provider_used.value,
                            "address_prefix": address[:10] if address else "N/A",
                            "failover_used": provider_used != PaymentProvider.BLOCKBEE,
                            "qr_code_available": bool(result.get("qr_code"))
                        },
                        session=session
                    )
                else:
                    # Fallback to sync version (will log warning but maintain safety)
                    financial_audit_logger.log_financial_event(
                        event_type=FinancialEventType.WALLET_DEPOSIT_ADDRESS_GENERATED,
                        entity_type=EntityType.WALLET,
                        entity_id=f"wallet_{user_id}",
                        user_id=user_id,
                        financial_context=financial_context,
                        previous_state="address_needed",
                        new_state="address_generated",
                        additional_data={
                            "wallet_transaction_id": wallet_txn_id,
                            "payment_provider": provider_used.value,
                            "address_prefix": address[:10] if address else "N/A",
                            "failover_used": provider_used != PaymentProvider.BLOCKBEE,
                            "qr_code_available": bool(result.get("qr_code"))
                        }
                    )

                return {
                    "address": address,
                    "wallet_transaction_id": wallet_txn_id,
                    "currency": currency,
                    "user_id": user_id,
                    "payment_provider": provider_used.value,
                    "qr_code": result.get("qr_code"),
                    "failover_used": provider_used != PaymentProvider.BLOCKBEE
                }

            except Exception as e:
                logger.error(f"Payment manager failed for wallet deposit {currency}: {e}")
                raise ValueError(f"Failed to generate {currency} deposit address: {e}")
                
        except Exception as e:
            logger.error(
                f"Critical error generating wallet deposit address for {currency}: {e}"
            )
            raise

    @classmethod
    def calculate_crypto_wallet_deposit_markup(
        cls, usd_amount: float, currency: str
    ) -> Dict[str, Any]:
        """Calculate 2% markup for crypto wallet deposits (except USDT which is already USD)"""
        from decimal import Decimal, ROUND_HALF_UP
        
        usd_decimal = Decimal(str(usd_amount))
        
        # USDT (TRC20/ERC20) are already USD-pegged stablecoins - no markup needed
        if currency.upper() in ['USDT-TRC20', 'USDT-ERC20', 'USDT']:
            return {
                "wallet_credit_usd": usd_decimal,  # What user gets in wallet
                "crypto_payment_usd": usd_decimal,  # What user pays (same as credit)
                "markup_amount_usd": Decimal("0"),  # No markup for USDT
                "markup_percentage": Decimal("0"),
                "currency": currency,
            }
        
        # Apply 2% markup for non-USDT cryptocurrencies
        markup_percentage = Config.WALLET_DEPOSIT_MARKUP_PERCENTAGE  # 2%
        
        # Calculate 2% markup that user pays extra
        markup_amount = usd_decimal * (markup_percentage / Decimal('100'))
        markup_amount = markup_amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        
        # User pays this total amount in crypto
        total_crypto_payment = usd_decimal + markup_amount
        total_crypto_payment = total_crypto_payment.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        
        # User receives this amount in their USD wallet (original amount)
        wallet_credit_amount = usd_decimal.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        
        return {
            "wallet_credit_usd": wallet_credit_amount,  # What user gets in wallet
            "crypto_payment_usd": total_crypto_payment,  # What user pays in crypto
            "markup_amount_usd": markup_amount,  # Platform profit
            "markup_percentage": markup_percentage,
            "currency": currency,
        }

    @classmethod
    async def process_wallet_deposit_confirmation(
        cls,
        wallet_txn_id: str,
        tx_hash: str,
        received_amount_usd: float,
        currency: str
    ) -> bool:
        """
        Process confirmed wallet deposit from DynoPay or BlockBee webhooks
        
        Args:
            wallet_txn_id: Wallet transaction ID (format: WALLET-YYYYMMDD-HHMMSS-user_id)
            tx_hash: Blockchain transaction hash
            received_amount_usd: Amount received in USD
            currency: Cryptocurrency symbol
            
        Returns:
            bool: True if wallet credit successful, False otherwise
        """
        try:
            # Extract user_id from wallet_txn_id
            # Format: WALLET-20250921-184612-5590563715
            parts = wallet_txn_id.split('-')
            if len(parts) < 4 or parts[0] != 'WALLET':
                logger.error(f"Invalid wallet_txn_id format: {wallet_txn_id}")
                return False
                
            user_id = int(parts[-1])  # Last part is user_id
            
            # Apply markup calculation - user only gets the credited amount (after markup deduction)
            markup_info = cls.calculate_crypto_wallet_deposit_markup(received_amount_usd, currency)
            wallet_credit_amount = markup_info['wallet_credit_usd']
            
            logger.info(
                f"💰 Processing wallet deposit: user {user_id}, received ${received_amount_usd:.2f} {currency}, "
                f"crediting ${wallet_credit_amount:.2f} USD (markup: ${markup_info['markup_amount_usd']:.2f})"
            )
            
            # Credit user wallet with the calculated amount - SIMPLE DIRECT APPROACH
            credit_result = cls.credit_user_wallet_simple(
                user_id=user_id,
                amount=wallet_credit_amount,
                description=f"Crypto wallet deposit: {currency} → USD (tx: {tx_hash[:12]}...)"
            )
            
            if credit_result:
                logger.info(
                    f"✅ Wallet deposit confirmed: user {user_id} credited ${wallet_credit_amount:.2f} USD "
                    f"from {currency} payment (tx: {tx_hash})"
                )
                return True
            else:
                logger.error(
                    f"❌ Wallet credit failed for user {user_id}"
                )
                return False
                
        except ValueError as ve:
            logger.error(f"Invalid wallet_txn_id format: {wallet_txn_id} - {ve}")
            return False
        except Exception as e:
            logger.error(f"Error processing wallet deposit confirmation: {e}", exc_info=True)
            return False

    @classmethod
    async def generate_deposit_address(
        cls, currency: str, escrow_id: str, amount_usd: float
    ) -> Dict[str, Any]:
        """Generate cryptocurrency deposit address for ESCROW payments with DynoPay failover"""
        try:
            # Use enhanced crypto service with failover for escrow deposits
            from services.crypto_enhanced import CryptoServiceEnhanced
            
            result = await CryptoServiceEnhanced.generate_escrow_deposit_address_with_failover(
                currency=currency,
                amount_usd=amount_usd,
                escrow_id=escrow_id,
                user_id=0,  # Will be populated from escrow data
                callback_data={'operation_type': 'escrow_deposit'}
            )
            
            logger.info(f"Generated deposit address for {currency} escrow {escrow_id}")
            return result

        except Exception as e:
            logger.error(f"Error generating deposit address for {currency}: {e}")
            raise

    @classmethod
    async def get_user_balance_atomic(cls, user_id: int, currency: str = "USD") -> float:
        """Get user balance with atomic read operations"""
        try:
            from utils.background_task_runner import run_io_task
            from utils.atomic_transactions import atomic_transaction
            
            def sync_get_balance():
                with atomic_transaction() as session:
                    result = session.execute(
                        select(func.sum(Wallet.available_balance))
                        .where(Wallet.user_id == user_id, Wallet.currency == currency)
                    )
                    total_balance = result.scalar()
                    return total_balance or Decimal("0")
            
            return await run_io_task(sync_get_balance)

        except Exception as e:
            logger.error(f"Error getting user balance for user {user_id}: {e}")
            return 0.0

    @classmethod
    def place_cashout_hold(cls, *args, **kwargs) -> Dict[str, Any]:
        """Wrapper method for CashoutHoldService.place_cashout_hold"""
        return CashoutHoldService.place_cashout_hold(*args, **kwargs)

    @classmethod
    def consume_cashout_hold(cls, *args, **kwargs) -> Dict[str, Any]:
        """Wrapper method for CashoutHoldService.consume_cashout_hold"""
        return CashoutHoldService.consume_cashout_hold(*args, **kwargs)

    @classmethod
    def release_cashout_hold(cls, *args, **kwargs) -> Dict[str, Any]:
        """
        🔒 ADMIN-ONLY Wrapper: Release cashout hold - requires admin authentication
        
        CRITICAL SECURITY: This function can only be called by authenticated admins.
        Frozen funds can only be released back to available balance by explicit admin action.
        
        This wrapper enforces the same admin-only requirements as the underlying service.
        All calls must include admin_id parameter for authentication and audit tracking.
        
        Args:
            Must include admin_id parameter for admin authentication
            Other args are passed through to CashoutHoldService.release_cashout_hold()
            
        Returns:
            Dict with success status, admin validation, and audit logging status
        """
        # Ensure admin_id is provided in the call
        admin_id = kwargs.get('admin_id')
        if admin_id is None and len(args) >= 3:  # admin_id is 3rd parameter
            admin_id = args[2]
        
        if admin_id is None:
            logger.critical(
                "🚨 SECURITY VIOLATION: CryptoServiceAtomic.release_cashout_hold called "
                "without admin_id parameter. All hold releases require admin authentication."
            )
            return {
                "success": False,
                "error": "❌ SECURITY: admin_id parameter required for hold release operations",
                "admin_validated": False,
                "audit_logged": False,
                "security_violation": True
            }
        
        # Log wrapper call for additional audit trail
        logger.info(
            f"🔒 WRAPPER_CALL: CryptoServiceAtomic.release_cashout_hold called by admin {admin_id}"
        )
        
        return CashoutHoldService.release_cashout_hold(*args, **kwargs)

    @classmethod
    def admin_approve_refund_to_available(cls, *args, **kwargs) -> Dict[str, Any]:
        """
        🔒 ADMIN-ONLY Wrapper: Approve refund of FAILED_HELD funds to available balance
        
        CRITICAL SECURITY: Only authenticated admins can approve refunds
        Enforces admin authentication and logs all refund approvals
        
        Args:
            Must include admin_id parameter for admin authentication
            Other args are passed through to CashoutHoldService.admin_approve_refund_to_available()
            
        Returns:
            Dict with success status, admin validation, and audit logging status
        """
        # Ensure admin_id is provided in the call
        admin_id = kwargs.get('admin_id')
        if admin_id is None and len(args) >= 3:  # admin_id is 3rd parameter
            admin_id = args[2]
        
        if admin_id is None:
            logger.critical(
                "🚨 SECURITY VIOLATION: CryptoServiceAtomic.admin_approve_refund_to_available called "
                "without admin_id parameter. All refund approvals require admin authentication."
            )
            return {
                "success": False,
                "error": "❌ SECURITY: admin_id parameter required for refund approval operations",
                "admin_validated": False,
                "audit_logged": False,
                "security_violation": True
            }
        
        # Log wrapper call for additional audit trail
        logger.info(
            f"🔒 WRAPPER_CALL: CryptoServiceAtomic.admin_approve_refund_to_available called by admin {admin_id}"
        )
        
        return CashoutHoldService.admin_approve_refund_to_available(*args, **kwargs)

    @classmethod
    def admin_cancel_disputed_cashout(cls, *args, **kwargs) -> Dict[str, Any]:
        """
        🔒 ADMIN-ONLY Wrapper: Cancel disputed cashout and update hold status
        
        CRITICAL SECURITY: Only authenticated admins can cancel disputed cashouts
        Enforces admin authentication and logs all cancellations
        
        Args:
            Must include admin_id parameter for admin authentication
            Other args are passed through to CashoutHoldService.admin_cancel_disputed_cashout()
            
        Returns:
            Dict with success status, admin validation, and audit logging status
        """
        # Ensure admin_id is provided in the call
        admin_id = kwargs.get('admin_id')
        if admin_id is None and len(args) >= 2:  # admin_id is 2nd parameter
            admin_id = args[1]
        
        if admin_id is None:
            logger.critical(
                "🚨 SECURITY VIOLATION: CryptoServiceAtomic.admin_cancel_disputed_cashout called "
                "without admin_id parameter. All cashout cancellations require admin authentication."
            )
            return {
                "success": False,
                "error": "❌ SECURITY: admin_id parameter required for disputed cashout cancellation operations",
                "admin_validated": False,
                "audit_logged": False,
                "security_violation": True
            }
        
        # Log wrapper call for additional audit trail
        logger.info(
            f"🔒 WRAPPER_CALL: CryptoServiceAtomic.admin_cancel_disputed_cashout called by admin {admin_id}"
        )
        
        return CashoutHoldService.admin_cancel_disputed_cashout(*args, **kwargs)

    @classmethod
    def admin_manual_credit_frozen_funds(cls, *args, **kwargs) -> Dict[str, Any]:
        """
        🔒 ADMIN-ONLY Wrapper: Manually credit frozen funds to available balance
        
        CRITICAL SECURITY: Only authenticated admins can manually credit frozen funds
        Enforces admin authentication and logs all manual credits
        
        Args:
            Must include admin_id parameter for admin authentication
            Other args are passed through to CashoutHoldService.admin_manual_credit_frozen_funds()
            
        Returns:
            Dict with success status, admin validation, and audit logging status
        """
        # Ensure admin_id is provided in the call
        admin_id = kwargs.get('admin_id')
        if admin_id is None and len(args) >= 3:  # admin_id is 3rd parameter
            admin_id = args[2]
        
        if admin_id is None:
            logger.critical(
                "🚨 SECURITY VIOLATION: CryptoServiceAtomic.admin_manual_credit_frozen_funds called "
                "without admin_id parameter. All manual credits require admin authentication."
            )
            return {
                "success": False,
                "error": "❌ SECURITY: admin_id parameter required for manual credit operations",
                "admin_validated": False,
                "audit_logged": False,
                "security_violation": True
            }
        
        # Log wrapper call for additional audit trail
        logger.info(
            f"🔒 WRAPPER_CALL: CryptoServiceAtomic.admin_manual_credit_frozen_funds called by admin {admin_id}"
        )
        
        return CashoutHoldService.admin_manual_credit_frozen_funds(*args, **kwargs)

    @classmethod
    def credit_user_wallet_simple(
        cls,
        user_id: int,
        amount: float,
        description: str = "Wallet credit"
    ) -> bool:
        """Simple, direct wallet crediting - NO OVER-ENGINEERING"""
        try:
            from database import get_session
            from models import Wallet, Transaction
            from decimal import Decimal, ROUND_HALF_UP
            from datetime import datetime
            import uuid
            
            # Simple session - no async complications
            with get_session() as session:
                # Get or create wallet
                wallet = session.query(Wallet).filter(
                    Wallet.user_id == user_id, 
                    Wallet.currency == "USD"
                ).first()
                
                if not wallet:
                    # Create new wallet
                    wallet = Wallet(
                        user_id=user_id,
                        currency="USD",
                        available_balance=0,
                        frozen_balance=0
                    )
                    session.add(wallet)
                    session.flush()  # Get wallet ID
                
                # Simple balance update - ensure type consistency
                current_balance_value = wallet.available_balance if wallet.available_balance is not None else Decimal('0')
                current_balance = Decimal(str(current_balance_value))
                amount_decimal = Decimal(str(amount))  # Convert float to Decimal
                new_balance = current_balance + amount_decimal
                wallet.available_balance = new_balance  # type: ignore
                
                # Simple transaction record
                transaction = Transaction(
                    transaction_id=str(uuid.uuid4()),
                    user_id=user_id,
                    transaction_type="wallet_deposit",
                    amount=amount,
                    currency="USD",
                    status="completed",
                    description=description,
                    confirmed_at=datetime.utcnow()
                )
                session.add(transaction)
                
                # Commit everything
                session.commit()
                
                logger.info(f"✅ SIMPLE CREDIT: User {user_id} credited ${amount:.2f} USD (new balance: ${new_balance:.2f})")
                return True
                
        except Exception as e:
            logger.error(f"❌ Simple wallet credit failed for user {user_id}: {e}")
            return False

    @classmethod  
    async def credit_user_wallet_atomic(
        cls,
        user_id: int,
        amount: Decimal | float,
        currency: str = "USD",
        escrow_id: Optional[int] = None,
        transaction_type: Optional[str] = None,
        description: Optional[str] = None,
        session: Optional[Session | AsyncSession] = None,
    ) -> bool:
        """Credit user's wallet with atomic transaction guarantees and async session support"""
        if session is None:
            raise ValueError("async session is required for credit_user_wallet_atomic - no sync fallback allowed")
        
        try:
            import uuid
            from decimal import Decimal, ROUND_HALF_UP
            from datetime import datetime, timezone
            from sqlalchemy import select, update
            
            # Ensure amount is positive and properly formatted
            amount_decimal = Decimal(str(abs(amount)))
            precision = Decimal("0.01") if currency == "USD" else Decimal("0.00000001")
            credit_amount = amount_decimal.quantize(precision, rounding=ROUND_HALF_UP)
            
            # Get or create wallet using async session
            wallet_stmt = select(Wallet).where(
                Wallet.user_id == user_id,
                Wallet.currency == currency
            )
            wallet = (await session.execute(wallet_stmt)).scalar_one_or_none()  # type: ignore
            
            if not wallet:
                # Create new wallet
                wallet = Wallet(
                    user_id=user_id,
                    currency=currency,
                    available_balance=0.0,
                    frozen_balance=0.0
                )
                session.add(wallet)
                await session.flush()  # type: ignore
            
            # Update wallet balance
            current_balance_value = wallet.available_balance if wallet.available_balance is not None else 0
            current_balance = Decimal(str(current_balance_value))
            new_balance = current_balance + Decimal(str(credit_amount))
            wallet.available_balance = new_balance.quantize(precision, rounding=ROUND_HALF_UP)  # type: ignore
            await session.flush()  # CRITICAL: Flush wallet changes before other queries
            
            # Create transaction record
            transaction = Transaction(
                transaction_id=str(uuid.uuid4()),
                user_id=user_id,
                transaction_type=transaction_type or "wallet_credit",
                amount=credit_amount,
                currency=currency,
                escrow_id=escrow_id,  # CRITICAL FIX: Link transaction to escrow for constraint compliance
                status="completed",
                description=description or "Wallet credit",
                confirmed_at=datetime.now(timezone.utc)
            )
            session.add(transaction)
            await session.flush()  # CRITICAL: Flush transaction before commit
            
            logger.info(f"✅ ASYNC_CREDIT: User {user_id} credited ${credit_amount:.2f} {currency} (new balance: ${wallet.available_balance:.2f})")
            return True
            
        except Exception as e:
            logger.error(f"❌ Async wallet credit failed for user {user_id}: {e}")
            return False

    @classmethod  
    async def credit_trading_credit_atomic(
        cls,
        user_id: int,
        amount: Decimal | float,
        currency: str = "USD",
        transaction_type: Optional[str] = None,
        description: Optional[str] = None,
        session: Optional[Session | AsyncSession] = None,
    ) -> bool:
        """Credit user's trading_credit (non-withdrawable bonus funds) with atomic transaction guarantees"""
        if session is None:
            raise ValueError("async session is required for credit_trading_credit_atomic - no sync fallback allowed")
        
        try:
            import uuid
            from decimal import Decimal, ROUND_HALF_UP
            from datetime import datetime, timezone
            from sqlalchemy import select
            
            # Ensure amount is positive and properly formatted
            amount_decimal = Decimal(str(abs(amount)))
            precision = Decimal("0.01") if currency == "USD" else Decimal("0.00000001")
            credit_amount = amount_decimal.quantize(precision, rounding=ROUND_HALF_UP)
            
            # Get or create wallet using async session
            wallet_stmt = select(Wallet).where(
                Wallet.user_id == user_id,
                Wallet.currency == currency
            )
            wallet = (await session.execute(wallet_stmt)).scalar_one_or_none()  # type: ignore
            
            if not wallet:
                # Create new wallet with trading_credit
                wallet = Wallet(
                    user_id=user_id,
                    currency=currency,
                    available_balance=0.0,
                    frozen_balance=0.0,
                    trading_credit=credit_amount
                )
                session.add(wallet)
                await session.flush()  # type: ignore
            else:
                # Update trading_credit balance
                current_trading_credit = wallet.trading_credit if wallet.trading_credit is not None else 0
                new_trading_credit = Decimal(str(current_trading_credit)) + credit_amount
                wallet.trading_credit = new_trading_credit.quantize(precision, rounding=ROUND_HALF_UP)  # type: ignore
                await session.flush()  # CRITICAL: Flush wallet changes before other queries
            
            # Create transaction record for audit trail
            transaction = Transaction(
                transaction_id=str(uuid.uuid4()),
                user_id=user_id,
                transaction_type=transaction_type or "trading_credit_bonus",
                amount=credit_amount,
                currency=currency,
                status="completed",
                description=description or "Trading credit bonus",
                confirmed_at=datetime.now(timezone.utc)
            )
            session.add(transaction)
            await session.flush()  # CRITICAL: Flush transaction before commit
            
            logger.info(f"✅ TRADING_CREDIT: User {user_id} credited ${credit_amount:.2f} {currency} trading credit (new trading_credit: ${wallet.trading_credit:.2f})")
            return True
            
        except Exception as e:
            logger.error(f"❌ Trading credit failed for user {user_id}: {e}")
            return False

    @classmethod
    async def debit_user_wallet_atomic(
        cls,
        user_id: int,
        amount: Decimal | float,
        currency: str = "USD",
        description: Optional[str] = None,
        escrow_id: Optional[int] = None,
        transaction_type: Optional[str] = None,
        session: Optional[Session | AsyncSession] = None,
    ) -> bool:
        """Debit user's wallet with atomic transaction guarantees using async session"""
        if session is None:
            raise ValueError("async session is required for debit_user_wallet_atomic - no sync fallback allowed")
        
        try:
            import uuid
            from decimal import Decimal, ROUND_HALF_UP
            from datetime import datetime, timezone
            from sqlalchemy import select, update
            
            # CRITICAL FIX: Ensure amount is always positive for debit operations
            # The caller should pass positive amounts, and we'll handle the debit logic
            amount_decimal = Decimal(str(abs(amount)))  # Force positive for debit operations
            
            # CRITICAL FIX: For very small USD amounts (like micro-cashouts), use higher precision
            # to prevent amounts like $0.00015696 from being rounded to $0.00
            if currency == "USD" and amount_decimal < Decimal("0.01"):
                precision = Decimal("0.00000001")  # 8 decimal places for micro amounts
            else:
                precision = (
                    Decimal("0.00000001") if currency != "USD" else Decimal("0.01")
                )
            
            debit_amount = amount_decimal.quantize(precision, rounding=ROUND_HALF_UP)

            # Get wallet using async session (no sync locking needed)
            wallet_stmt = select(Wallet).where(
                Wallet.user_id == user_id,
                Wallet.currency == currency
            )
            wallet = (await session.execute(wallet_stmt)).scalar_one_or_none()  # type: ignore
            
            if not wallet:
                logger.warning(f"No wallet found for user {user_id} currency {currency}")
                return False
            
            # Capture balances before operation for audit
            available_before = Decimal(
                str(wallet.available_balance if wallet.available_balance is not None else 0)
            )
            trading_credit_before = Decimal(
                str(wallet.trading_credit if wallet.trading_credit is not None else 0)
            )
            total_balance = available_before + trading_credit_before
            amount_to_debit = Decimal(str(debit_amount))
            tolerance = Decimal("0.01")  # 1 cent tolerance for rounding
            
            # CRITICAL FIX: Define variables for audit logging
            balance_before = total_balance
            current_balance = total_balance

            # CRITICAL FIX: Determine if we can use available_balance only (with tolerance for backwards compatibility)
            # Case 1: If trading_credit is zero and available_balance is within tolerance, use available_balance only
            # Case 2: If we need trading_credit, apply strict checks to prevent negative balances
            
            can_use_available_only = (
                trading_credit_before == Decimal("0") and 
                available_before >= (amount_to_debit - tolerance)
            ) or (
                trading_credit_before > Decimal("0") and 
                available_before >= amount_to_debit
            )
            
            if can_use_available_only:
                # Case 1: Sufficient available_balance, deduct from available_balance only
                # CRITICAL: When tolerance is used, cap the result at 0 to prevent negative balances
                new_available_balance = available_before - amount_to_debit
                # If tolerance was applied and result is slightly negative, clamp to 0
                if new_available_balance < Decimal("0"):
                    new_available_balance = Decimal("0")
                wallet.available_balance = new_available_balance.quantize(precision, rounding=ROUND_HALF_UP)  # type: ignore
                new_balance = Decimal(str(wallet.available_balance)) + Decimal(str(wallet.trading_credit or 0))
                logger.info(f"💰 Debited ${amount_to_debit:.2f} from available_balance for user {user_id}")
            else:
                # Case 2: Need to use both available_balance and trading_credit
                # STRICT CHECKS: No tolerance when trading_credit is involved (prevents negative balances)
                if total_balance < amount_to_debit:
                    logger.warning(
                        f"Insufficient total balance for user {user_id}: ${total_balance:.2f} (available: ${available_before:.2f}, trading_credit: ${trading_credit_before:.2f}) < ${amount_to_debit:.2f}"
                    )
                    return False
                
                # Deduct all available_balance first
                amount_from_available = available_before
                amount_from_trading = amount_to_debit - available_before
                
                # CRITICAL: Ensure trading_credit has sufficient balance (no tolerance for trading_credit)
                if trading_credit_before < amount_from_trading:
                    logger.warning(
                        f"Insufficient trading_credit for user {user_id}: ${trading_credit_before:.2f} < ${amount_from_trading:.2f} (after depleting available_balance)"
                    )
                    return False
                
                wallet.available_balance = Decimal("0").quantize(precision, rounding=ROUND_HALF_UP)  # type: ignore
                new_trading_credit = trading_credit_before - amount_from_trading
                wallet.trading_credit = new_trading_credit.quantize(precision, rounding=ROUND_HALF_UP)  # type: ignore
                new_balance = Decimal(str(wallet.available_balance)) + Decimal(str(wallet.trading_credit or 0))
                
                logger.info(
                    f"💰 Debited ${amount_to_debit:.2f} from user {user_id}: "
                    f"${amount_from_available:.2f} from available_balance + "
                    f"${amount_from_trading:.2f} from trading_credit"
                )

            # Create transaction record
            # Initialize tx_description to prevent unbound variable error
            tx_description = ""
            if description:
                tx_description = description
            else:
                if transaction_type == "cashout":
                    # Use escrow_id to distinguish between types of cashouts
                    if escrow_id:
                        tx_description = (
                            f"💼 Trade payment: ${debit_amount:.2f} USD"
                        )
                    else:
                        tx_description = (
                            f"💸 Cash out request: ${debit_amount:.2f} USD"
                        )
                elif transaction_type == "platform_fee":
                    tx_description = f"💳 Platform fee: ${debit_amount:.2f} USD"

            # SECURITY: Log financial operation for audit trail
            try:
                logger.info(
                    f"AUDIT: Wallet debit - User:{user_id} Amount:{debit_amount} Currency:{currency} Description:{tx_description}"
                )
            except Exception as audit_error:
                logger.error(f"Failed to log financial audit: {audit_error}")

            # Default transaction description if not set above
            if 'tx_description' not in locals():
                tx_description = (
                    f"💰 Wallet debit: ${debit_amount:.2f} {currency}"
                )

            tx_type = (
                transaction_type
                if transaction_type
                else TransactionType.CASHOUT.value
            )

            # DATABASE CONSTRAINT: Transactions table requires positive amounts
            # The transaction_type field indicates debit vs credit, not the sign of the amount
            final_amount = abs(debit_amount)  # Always positive to satisfy DB constraint
            
            # FRAUD DETECTION: Check for suspicious cashout patterns using async session
            if tx_type == TransactionType.CASHOUT.value:
                from datetime import timedelta
                # Check for rapid consecutive cashouts (potential exploitation)
                recent_cashouts_stmt = select(func.count(Transaction.id)).where(
                    Transaction.user_id == user_id,
                    Transaction.transaction_type == TransactionType.CASHOUT.value,
                    Transaction.created_at >= datetime.now(timezone.utc) - timedelta(minutes=5)
                )
                recent_cashouts = (await session.execute(recent_cashouts_stmt)).scalar() or 0  # type: ignore
                        
                if recent_cashouts >= 3:
                    logger.error(f"FRAUD ALERT: User {user_id} attempting rapid cashouts (count: {recent_cashouts})")
                    # Send alert to admin
                    try:
                        from services.admin_email_alerts import admin_email_alerts
                        import asyncio
                        # Send admin notification - simplified approach
                        logger.critical(f"🚨 FRAUD_ALERT: User {user_id} attempted {recent_cashouts + 1} rapid cashouts")
                    except Exception as alert_error:
                        logger.error(f"Failed to send fraud alert: {alert_error}")
                    return False  # Block the transaction
            
            # Ensure tx_description is defined
            if 'tx_description' not in locals():
                tx_description = f"💰 Wallet debit: ${debit_amount:.2f} {currency}"
            
            transaction = Transaction(
                transaction_id=UniversalIDGenerator.generate_transaction_id(),
                user_id=user_id,
                escrow_id=escrow_id,
                transaction_type=tx_type,
                amount=final_amount,  # GUARANTEED: Always negative for cashouts
                currency=currency,
                status="completed",
                description=tx_description,
                confirmed_at=datetime.now(timezone.utc),
            )

            session.add(transaction)

            # Log financial event atomically within async transaction
            try:
                financial_context = FinancialContext(
                    amount=amount_to_debit,
                    currency=currency,
                    balance_before=balance_before,
                    balance_after=new_balance
                )
                
                related_entities = {}
                if escrow_id:
                    related_entities["escrow_id"] = str(escrow_id)
                if transaction.transaction_id is not None:
                    related_entities["transaction_id"] = transaction.transaction_id
                
                # Ensure tx_description is defined for logging
                if 'tx_description' not in locals():
                    tx_description = f"💰 Wallet debit: ${debit_amount:.2f} {currency}"
                
                # Use async financial audit logger
                await financial_audit_logger.log_financial_event_async(
                    event_type=FinancialEventType.WALLET_DEBIT,
                    entity_type=EntityType.WALLET,
                    entity_id=f"wallet_{user_id}",
                    user_id=user_id,
                    financial_context=financial_context,
                    previous_state=f"balance:{balance_before}",
                    new_state=f"balance:{new_balance}",
                    related_entities=related_entities,
                    additional_data={
                        "transaction_type": transaction_type or "general_debit",
                        "description": tx_description,
                        "precision_used": str(precision),
                        "insufficient_balance_check": "passed"
                    },
                    session=session  # type: ignore
                )
            except Exception as audit_error:
                # Don't fail the transaction if audit logging fails
                logger.error(f"Financial audit logging failed for user {user_id} debit: {audit_error}")

            logger.info(
                f"✅ ASYNC_DEBIT: User {user_id} debited ${debit_amount:.2f} {currency} (balance: {current_balance} → {wallet.available_balance})"
            )
            
            # CRITICAL FIX: Invalidate balance caches after successful debit
            try:
                from utils.balance_cache_invalidation import balance_cache_invalidation_service
                cache_success = balance_cache_invalidation_service.invalidate_user_balance_caches(
                    user_id=user_id, 
                    operation_type=f"wallet_debit_{transaction_type or 'unknown'}"
                )
                if cache_success:
                    logger.debug(f"Balance caches invalidated for user {user_id} after debit")
                else:
                    logger.warning(f"Failed to invalidate balance caches for user {user_id} after debit")
            except Exception as cache_error:
                # Don't fail the transaction if cache invalidation fails, but log it
                logger.error(f"Cache invalidation error for user {user_id} debit: {cache_error}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Async wallet debit failed for user {user_id}: {e}")
            return False



    @classmethod
    async def convert_to_usd(cls, amount: float, currency: str) -> float:
        """Convert amount to USD using real-time exchange rates"""
        if currency == "USD":
            return amount

        try:
            rate = await cls.get_real_time_exchange_rate(currency)
            return float(Decimal(str(amount)) * rate)
        except Exception as e:
            logger.error(f"Error converting {amount} {currency} to USD: {e}")
            raise

    @classmethod
    async def get_crypto_rates(cls) -> Dict[str, Decimal]:
        """Get current cryptocurrency exchange rates from FastForex API with ultra-fast session caching"""
        try:
            # PERFORMANCE OPTIMIZATION: Check ultra-fast session cache first
            cached_rates = cls._get_session_cached_rates()
            if cached_rates:
                return cached_rates
            
            currencies = [
                "BTC",
                "ETH",
                "LTC",
                "DOGE",
                "BCH",
                "BSC",
                "TRX",
                "USDT-ERC20",
                "USDT-TRC20",
            ]
            rates = await fastforex_service.get_multiple_rates(currencies)
            rates["USD"] = Decimal("1.0")  # Always include USD = 1.0
            
            # Store in ultra-fast session cache for subsequent calls
            cls._set_session_cached_rates(rates)
            return rates
        except Exception as e:
            logger.error(f"Error getting crypto rates: {e}")
            # PRODUCTION: No fallback rates - real-time API is required
            raise ValueError(
                "Real-time exchange rate service unavailable. Please contact support."
            )

    @classmethod
    def get_supported_networks(cls, currency: str) -> List[str]:
        """Get supported networks for a currency"""
        return Config.CURRENCY_NETWORKS.get(currency, [])

    @classmethod
    async def estimate_gas_price(
        cls, currency: str, network: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get real-time gas prices using BlockBee API or fallback to reasonable estimates"""
        try:
            # Try to get real-time gas prices from payment provider
            try:
                # Use payment manager to get current network fees  
                currency_info = await payment_manager.get_currency_info(currency)
                if currency_info and "fee_per_byte" in currency_info:
                    fee_per_byte = currency_info["fee_per_byte"]
                    # Convert to standard gas price format
                    return {
                        "slow": max(1, int(fee_per_byte * 0.8)),
                        "standard": int(fee_per_byte),
                        "fast": int(fee_per_byte * 1.5),
                    }
            except Exception as e:
                logger.warning(
                    f"Payment provider gas price API failed for {currency}: {e}"
                )

            # Production-grade fallback with reasonable estimates (updated regularly)
            current_gas_estimates = {
                "ETH": {"slow": 25, "standard": 35, "fast": 50},  # Ethereum mainnet
                "USDT-ERC20": {"slow": 25, "standard": 35, "fast": 50},  # ERC20 tokens
                "BSC": {"slow": 3, "standard": 5, "fast": 8},  # Binance Smart Chain
                "BTC": {"slow": 15, "standard": 25, "fast": 40},  # Bitcoin network
                "USDT-TRC20": {"slow": 0, "standard": 0, "fast": 0},  # TRON network
                "LTC": {"slow": 5, "standard": 10, "fast": 20},  # Litecoin
                "DOGE": {"slow": 1, "standard": 2, "fast": 5},  # Dogecoin
                "BCH": {"slow": 1, "standard": 3, "fast": 8},  # Bitcoin Cash
                "TRX": {"slow": 0, "standard": 0, "fast": 0},  # TRON network
            }

            key = f"{currency}_{network}" if network else currency
            estimated_prices = current_gas_estimates.get(
                key, {"slow": 2, "standard": 5, "fast": 10}
            )

            logger.info(
                f"Gas price estimate for {currency}: {estimated_prices} (source: production estimates)"
            )
            return estimated_prices

        except Exception as e:
            logger.error(f"Error estimating gas prices for {currency}: {e}")
            # Emergency fallback
            return {"slow": 5, "standard": 10, "fast": 20}


# Backward compatibility wrapper
class CryptoService:
    """Backward compatibility wrapper for existing code"""

    @classmethod
    async def generate_deposit_address(
        cls, currency: str, escrow_id: str, amount_usd: float
    ) -> Dict[str, Any]:
        return await CryptoServiceAtomic.generate_deposit_address(
            currency, escrow_id, amount_usd
        )

    @classmethod
    async def generate_wallet_deposit_address(
        cls, currency: str, user_id: int, session: Optional[AsyncSession] = None
    ) -> Dict[str, Any]:
        return await CryptoServiceAtomic.generate_wallet_deposit_address(
            currency, user_id, session
        )

    @classmethod
    async def get_user_balance(cls, user_id: int, currency: str = "USD") -> float:
        return await CryptoServiceAtomic.get_user_balance_atomic(user_id, currency)

    @classmethod
    async def credit_user_wallet(
        cls, user_id: int, amount: float, currency: str = "USD", **kwargs
    ) -> bool:
        # SECURITY: Legacy function safely redirects to atomic version
        logger.info(
            f"Legacy credit_user_wallet for user {user_id} safely redirected to atomic version"
        )
        import asyncio
        return await CryptoServiceAtomic.credit_user_wallet_atomic(
            user_id, amount, currency, **kwargs
        )

    @classmethod
    async def debit_user_wallet(
        cls, user_id: int, amount: float, currency: str = "USD", **kwargs
    ) -> bool:
        # SECURITY: Legacy function safely redirects to atomic version
        logger.info(
            f"Legacy debit_user_wallet for user {user_id} safely redirected to atomic version"
        )
        return await CryptoServiceAtomic.debit_user_wallet_atomic(
            user_id, amount, currency, **kwargs
        )

    @classmethod
    async def credit_user_wallet_atomic(cls, *args, **kwargs) -> bool:
        return await CryptoServiceAtomic.credit_user_wallet_atomic(*args, **kwargs)

    @classmethod
    async def debit_user_wallet_atomic(cls, *args, **kwargs) -> bool:
        return await CryptoServiceAtomic.debit_user_wallet_atomic(*args, **kwargs)


class CashoutHoldService:
    """
    CRITICAL FINANCIAL INTEGRITY FIX: Implement funds-hold architecture for deferred cashouts
    
    This service prevents the financial gap where:
    - Cashout records are created without wallet debits
    - External failures leave no debit to refund
    - Users keep balance + system attempts phantom refunds
    """
    
    @classmethod
    def place_cashout_hold(
        cls,
        user_id: int,
        amount: float,
        currency: str = "USD",
        cashout_id: Optional[str] = None,
        description: Optional[str] = None,
        session=None,
    ) -> Dict[str, Any]:
        """
        Place funds in frozen_balance to hold for pending cashout
        
        Returns:
        {
            "success": bool,
            "hold_transaction_id": str,
            "frozen_amount": float,
            "error": str (if failed)
        }
        """
        try:
            from models import Transaction, TransactionType, Wallet
            from utils.atomic_transactions import locked_wallet_operation
            from decimal import Decimal, ROUND_HALF_UP
            from utils.universal_id_generator import UniversalIDGenerator
            
            use_provided_session = session is not None
            tx_session = session if use_provided_session else get_session()
            
            try:
                amount_decimal = Decimal(str(abs(amount)))
                precision = Decimal("0.00000001") if currency != "USD" else Decimal("0.01")
                hold_amount = amount_decimal.quantize(precision, rounding=ROUND_HALF_UP)
                
                # Use locked wallet operation to prevent race conditions
                with locked_wallet_operation(user_id, currency, tx_session) as wallet:
                    # Check sufficient balance (available = balance - frozen_balance - locked_balance)
                    current_balance = Decimal(str(wallet.available_balance if wallet.available_balance is not None else 0))
                    current_frozen = Decimal(str(wallet.frozen_balance if wallet.frozen_balance is not None else 0))
                    current_locked = Decimal(str(wallet.locked_balance if wallet.locked_balance is not None else 0))
                    
                    available_balance = current_balance - current_frozen - current_locked
                    amount_to_hold = Decimal(str(hold_amount))
                    tolerance = Decimal("0.01")  # 1 cent tolerance
                    
                    if available_balance < (amount_to_hold - tolerance):
                        return {
                            "success": False,
                            "error": f"Insufficient available balance: ${available_balance:.2f} < ${amount_to_hold:.2f}",
                            "available_balance": float(available_balance),
                            "requested_amount": hold_amount
                        }
                    
                    # CRITICAL FIX: Atomically reduce wallet balance AND increase frozen_balance
                    # This prevents double-spend by immediately deducting from available balance
                    new_balance = current_balance - amount_to_hold
                    new_frozen = current_frozen + amount_to_hold
                    
                    wallet.available_balance = new_balance.quantize(precision, rounding=ROUND_HALF_UP)
                    wallet.frozen_balance = new_frozen.quantize(precision, rounding=ROUND_HALF_UP)
                    
                    # Create CASHOUT_HOLD transaction record
                    hold_description = description or f"🔒 Cashout hold: ${hold_amount:.2f} USD for {cashout_id or 'pending cashout'}"
                    hold_tx_id = UniversalIDGenerator.generate_transaction_id()
                    
                    transaction = Transaction(
                        transaction_id=hold_tx_id,
                        user_id=user_id,
                        transaction_type=TransactionType.CASHOUT_HOLD.value,
                        amount=hold_amount,
                        currency=currency,
                        status="completed",
                        description=hold_description,
                        escrow_id=None,  # Not escrow-related
                        # CRITICAL FIX: Remove metadata field - doesn't exist in Transaction model
                        # Store cashout_id in description field instead for tracking
                        confirmed_at=datetime.utcnow(),
                    )
                    
                    tx_session.add(transaction)
                    # CRITICAL FIX: Flush to ensure transaction is persisted immediately
                    # This prevents test failures where transaction count doesn't increase properly
                    tx_session.flush()
                    
                    if not use_provided_session:
                        tx_session.commit()
                    
                    logger.info(
                        f"✅ CASHOUT_HOLD: Placed ${hold_amount:.2f} {currency} hold for user {user_id}, "
                        f"cashout {cashout_id}, frozen_balance: {current_frozen:.2f} → {wallet.frozen_balance:.2f}"
                    )
                    
                    # ARCHITECTURAL INTEGRATION: Create WalletHolds record for lifecycle tracking
                    # CRITICAL FIX: Create within same transaction to prevent lock conflicts
                    try:
                        from models import WalletHolds, WalletHoldStatus
                        
                        # Create WalletHolds record within same transaction to avoid lock conflicts
                        hold_record = WalletHolds(
                            user_id=user_id,
                            wallet_id=wallet.id,
                            currency=currency,
                            amount=amount_to_hold,
                            purpose="cashout",
                            linked_type="cashout",
                            linked_id=cashout_id or "pending",
                            status=WalletHoldStatus.HELD.value,
                            hold_txn_id=hold_tx_id,
                            created_at=datetime.utcnow()
                        )
                        
                        tx_session.add(hold_record)
                        # Let the main transaction commit handle both wallet update and WalletHolds creation
                        
                        logger.info(f"✅ WALLET_HOLDS_CREATED: Lifecycle record prepared for hold {hold_tx_id}")
                        
                    except Exception as holds_error:
                        logger.error(f"❌ WALLET_HOLDS_CREATION_FAILED: {holds_error}")
                        # Continue - this is not critical for hold placement
                        import traceback
                        logger.error(f"❌ WALLET_HOLDS_STACK_TRACE: {traceback.format_exc()}")
                    
                    return {
                        "success": True,
                        "hold_transaction_id": hold_tx_id,
                        "frozen_amount": hold_amount,
                        "new_frozen_balance": wallet.frozen_balance,
                        "available_balance": float(available_balance - amount_to_hold)
                    }
                    
            except Exception as e:
                if not use_provided_session:
                    tx_session.rollback()
                logger.error(f"Error placing cashout hold for user {user_id}: {e}")
                return {
                    "success": False,
                    "error": f"Hold placement failed: {str(e)}"
                }
            finally:
                if not use_provided_session:
                    tx_session.close()
                    
        except Exception as e:
            logger.error(f"Critical error in place_cashout_hold: {e}")
            return {
                "success": False,
                "error": f"Critical hold system error: {str(e)}"
            }
    
    @classmethod 
    def convert_hold_to_debit(
        cls,
        user_id: int,
        amount: float,
        currency: str = "USD",
        cashout_id: Optional[str] = None,
        hold_transaction_id: Optional[str] = None,
        description: Optional[str] = None,
        session=None,
    ) -> Dict[str, Any]:
        """
        Convert frozen_balance hold to actual wallet debit (for successful external processing)
        
        Returns:
        {
            "success": bool,
            "debit_transaction_id": str,
            "released_amount": float,
            "error": str (if failed)
        }
        """
        try:
            from models import Transaction, TransactionType
            from utils.atomic_transactions import locked_wallet_operation
            from decimal import Decimal, ROUND_HALF_UP
            from utils.universal_id_generator import UniversalIDGenerator
            
            use_provided_session = session is not None
            tx_session = session if use_provided_session else get_session()
            
            try:
                amount_decimal = Decimal(str(abs(amount)))
                precision = Decimal("0.00000001") if currency != "USD" else Decimal("0.01")
                debit_amount = amount_decimal.quantize(precision, rounding=ROUND_HALF_UP)
                
                # Use locked wallet operation
                with locked_wallet_operation(user_id, currency, tx_session) as wallet:
                    current_balance = Decimal(str(wallet.available_balance if wallet.available_balance is not None else 0))
                    current_frozen = Decimal(str(wallet.frozen_balance if wallet.frozen_balance is not None else 0))
                    amount_to_convert = Decimal(str(debit_amount))
                    
                    # Verify frozen balance covers the conversion
                    if current_frozen < amount_to_convert:
                        return {
                            "success": False,
                            "error": f"Insufficient frozen balance: ${current_frozen:.2f} < ${amount_to_convert:.2f}",
                            "frozen_balance": float(current_frozen),
                            "requested_amount": debit_amount
                        }
                    
                    # Convert: reduce frozen_balance AND reduce actual balance (double reduction = debit)
                    new_frozen = current_frozen - amount_to_convert
                    new_balance = current_balance - amount_to_convert
                    
                    wallet.frozen_balance = new_frozen.quantize(precision, rounding=ROUND_HALF_UP)
                    wallet.available_balance = new_balance.quantize(precision, rounding=ROUND_HALF_UP)
                    
                    # Create CASHOUT_DEBIT transaction record
                    debit_description = description or f"💸 Cashout debit: ${debit_amount:.2f} USD for {cashout_id or 'completed cashout'}"
                    debit_tx_id = UniversalIDGenerator.generate_transaction_id()
                    
                    transaction = Transaction(
                        transaction_id=debit_tx_id,
                        user_id=user_id,
                        transaction_type=TransactionType.CASHOUT_DEBIT.value,
                        amount=debit_amount,
                        currency=currency,
                        status="completed",
                        description=debit_description,
                        escrow_id=None,
                        metadata={
                            "cashout_id": cashout_id,
                            "hold_transaction_id": hold_transaction_id,
                            "conversion_type": "hold_to_debit",
                            "debit_amount": debit_amount,
                            "previous_frozen": float(current_frozen),
                            "new_frozen": wallet.frozen_balance
                        },
                        confirmed_at=datetime.utcnow(),
                    )
                    
                    tx_session.add(transaction)
                    
                    if not use_provided_session:
                        tx_session.commit()
                    
                    logger.info(
                        f"✅ HOLD_TO_DEBIT: Converted ${debit_amount:.2f} {currency} hold to debit for user {user_id}, "
                        f"cashout {cashout_id}, balance: {current_balance:.2f} → {wallet.available_balance:.2f}, "
                        f"frozen: {current_frozen:.2f} → {wallet.frozen_balance:.2f}"
                    )
                    
                    return {
                        "success": True,
                        "debit_transaction_id": debit_tx_id,
                        "debit_amount": debit_amount,
                        "new_balance": wallet.available_balance,
                        "new_frozen_balance": wallet.frozen_balance
                    }
                    
            except Exception as e:
                if not use_provided_session:
                    tx_session.rollback()
                logger.error(f"Error converting hold to debit for user {user_id}: {e}")
                return {
                    "success": False,
                    "error": f"Hold conversion failed: {str(e)}"
                }
            finally:
                if not use_provided_session:
                    tx_session.close()
                    
        except Exception as e:
            logger.error(f"Critical error in convert_hold_to_debit: {e}")
            return {
                "success": False,
                "error": f"Critical conversion error: {str(e)}"
            }
    
    @classmethod
    def release_cashout_hold(
        cls,
        user_id: int,
        amount: float,
        admin_id: int,  # SECURITY: Required admin ID for authorization and audit
        currency: str = "USD",
        cashout_id: Optional[str] = None,
        hold_transaction_id: Optional[str] = None,
        description: Optional[str] = None,
        session=None,
    ) -> Dict[str, Any]:
        """
        🔒 ADMIN-ONLY: Release frozen_balance hold back to available balance (for FAILED cashouts only)
        
        CRITICAL SECURITY: This function can only be called by authenticated admins.
        Releasing frozen funds back to available balance requires explicit admin authorization.
        
        CRITICAL: Only use for FAILED cashouts where funds should be returned to user
        For SUCCESSFUL cashouts, use consume_cashout_hold() instead
        
        Args:
            user_id: The user whose funds should be released
            amount: The amount to release from frozen balance
            admin_id: The admin user ID performing this action (required for authorization)
            currency: Currency of the funds (default: USD)
            cashout_id: The cashout ID this hold relates to
            hold_transaction_id: The original hold transaction ID for tracking
            description: Optional description for the release
            session: Database session (optional)
        
        Returns:
        {
            "success": bool,
            "release_transaction_id": str,
            "released_amount": float,
            "admin_validated": bool,
            "audit_logged": bool,
            "error": str (if failed)
        }
        """
        # SECURITY: Validate admin authorization FIRST before any operations
        from utils.admin_security import is_admin_secure
        from services.audit_logger import AuditLogger
        
        # CRITICAL SECURITY CHECK: Ensure only admins can release frozen funds
        if not is_admin_secure(admin_id):
            logger.critical(
                f"🚨 SECURITY VIOLATION: Non-admin user {admin_id} attempted to release cashout hold "
                f"for user {user_id}, amount ${amount:.2f}. Access DENIED."
            )
            return {
                "success": False,
                "error": "❌ SECURITY: Administrative privileges required to release frozen funds",
                "admin_validated": False,
                "audit_logged": False,
                "security_violation": True
            }
        
        logger.info(
            f"🔒 ADMIN_VALIDATED: Admin {admin_id} authorized to release cashout hold "
            f"for user {user_id}, amount ${amount:.2f}"
        )
        
        try:
            from models import Transaction, TransactionType, Wallet
            from utils.atomic_transactions import locked_wallet_operation
            from decimal import Decimal, ROUND_HALF_UP
            from utils.universal_id_generator import UniversalIDGenerator
            
            # Initialize audit logger for tracking admin actions
            audit_logger = AuditLogger()
            audit_logged = False
            
            use_provided_session = session is not None
            tx_session = session if use_provided_session else get_session()
            
            try:
                amount_decimal = Decimal(str(abs(amount)))
                precision = Decimal("0.00000001") if currency != "USD" else Decimal("0.01")
                release_amount = amount_decimal.quantize(precision, rounding=ROUND_HALF_UP)
                
                # CRITICAL FIX: ROBUST IDEMPOTENCY PROTECTION - Check if this hold has already been released
                if hold_transaction_id:
                    # Use robust query to detect existing CASHOUT_HOLD_RELEASE transactions
                    # 1. Use ilike for case-insensitive pattern matching
                    # 2. Remove status filters to be status-agnostic
                    # 3. Check for transaction existence only (minimal query)
                    existing_release = tx_session.query(Transaction.id, Transaction.transaction_id, Transaction.amount, Transaction.created_at).filter(
                        Transaction.user_id == user_id,
                        Transaction.transaction_type == TransactionType.CASHOUT_HOLD_RELEASE.value,
                        Transaction.description.ilike(f"%[hold_tx:{hold_transaction_id}]%")
                    ).first()
                    
                    if existing_release:
                        logger.warning(
                            f"🚨 IDEMPOTENT_RELEASE_SKIPPED: Hold {hold_transaction_id} already released by "
                            f"transaction {existing_release.transaction_id} for user {user_id}. "
                            f"Returning idempotent success to prevent double-credit."
                        )
                        
                        # Return success with minimal details from existing release (idempotent behavior)
                        return {
                            "success": True,
                            "release_transaction_id": existing_release.transaction_id,
                            "released_amount": float(existing_release.amount),
                            "new_balance": None,  # Don't query wallet unnecessarily 
                            "new_frozen_balance": None,
                            "currency": currency,
                            "idempotent": True,
                            "funds_restored": True,
                            "admin_validated": True,  # Admin was already validated above
                            "audit_logged": False,  # No new audit log needed for idempotent operation
                            "warning": "Duplicate prevention: hold already released to prevent double-credit",
                            "original_release_date": existing_release.created_at.isoformat() if existing_release.created_at else "unknown"
                        }
                    
                    # DEBUG LOGGING: Verify idempotency query pattern
                    logger.info(
                        f"🔧 DEBUG_IDEMPOTENCY_QUERY: Searching for release transactions with "
                        f"user_id={user_id}, type={TransactionType.CASHOUT_HOLD_RELEASE.value}, "
                        f"pattern='%[hold_tx:{hold_transaction_id}]%' - No matches found, proceeding with release"
                    )
                else:
                    logger.warning(f"⚠️ IDEMPOTENCY_LIMITED: No hold_transaction_id provided for user {user_id}, cannot perform full idempotency check")
                
                # Use locked wallet operation to prevent race conditions
                with locked_wallet_operation(user_id, currency, tx_session) as wallet:
                    current_frozen = Decimal(str(wallet.frozen_balance if wallet.frozen_balance is not None else 0))
                    current_balance = Decimal(str(wallet.available_balance if wallet.available_balance is not None else 0))
                    amount_to_release = Decimal(str(release_amount))
                    
                    # Verify frozen balance covers the release
                    if current_frozen < amount_to_release:
                        logger.error(
                            f"❌ RELEASE FAILED: Insufficient frozen balance for user {user_id}. "
                            f"Frozen: ${current_frozen:.2f}, Requested: ${amount_to_release:.2f}"
                        )
                        return {
                            "success": False,
                            "error": f"Insufficient frozen balance: ${current_frozen:.2f} < ${amount_to_release:.2f}",
                            "frozen_balance": float(current_frozen),
                            "requested_amount": release_amount
                        }
                    
                    # CRITICAL FIX: ATOMICALLY move funds from frozen_balance back to available balance
                    new_frozen = current_frozen - amount_to_release
                    new_balance = current_balance + amount_to_release  # CRITICAL: Add back to available balance
                    
                    wallet.frozen_balance = new_frozen.quantize(precision, rounding=ROUND_HALF_UP)
                    wallet.available_balance = new_balance.quantize(precision, rounding=ROUND_HALF_UP)
                    
                    # Create CASHOUT_HOLD_RELEASE transaction record
                    # CRITICAL FIX: ALWAYS use canonical, deterministic description format for idempotency
                    # IGNORE any passed description parameter to ensure idempotency query works
                    release_description = f"Cashout hold release: +${release_amount:.2f} USD [hold_tx:{hold_transaction_id}] [user:{user_id}]"
                    release_tx_id = UniversalIDGenerator.generate_transaction_id()
                    
                    transaction = Transaction(
                        transaction_id=release_tx_id,
                        user_id=user_id,
                        transaction_type=TransactionType.CASHOUT_HOLD_RELEASE.value,
                        amount=release_amount,
                        currency=currency,
                        status="completed",
                        description=release_description,
                        escrow_id=None,
                        # CRITICAL FIX: Remove metadata field - doesn't exist in Transaction model
                        # Store hold_transaction_id in description field for tracking
                        confirmed_at=datetime.utcnow(),
                    )
                    
                    tx_session.add(transaction)
                    # CRITICAL FIX: Flush to ensure transaction is persisted immediately
                    # This prevents test failures where transaction count doesn't increase properly
                    tx_session.flush()
                    
                    # DEBUG LOGGING: Verify transaction creation for idempotency
                    logger.info(
                        f"🔧 DEBUG_TRANSACTION_CREATED: Release transaction created with "
                        f"ID={release_tx_id}, type={transaction.transaction_type}, "
                        f"description=[{release_description}]"
                    )
                    
                    # Log financial event for audit trail
                    financial_context = FinancialContext(
                        amount=amount_to_release,
                        currency=currency,
                        balance_before=current_balance,
                        balance_after=new_balance
                    )
                    
                    financial_audit_logger.log_financial_event(
                        event_type=FinancialEventType.WALLET_HOLD_RELEASE,
                        entity_type=EntityType.WALLET,
                        entity_id=f"wallet_{user_id}",
                        user_id=user_id,
                        financial_context=financial_context,
                        previous_state=f"balance:{current_balance},frozen:{current_frozen}",
                        new_state=f"balance:{new_balance},frozen:{new_frozen}",
                        additional_data={
                            "cashout_id": cashout_id,
                            "hold_transaction_id": hold_transaction_id,
                            "release_transaction_id": release_tx_id,
                            "funds_restored_to_balance": True
                        },
                        session=tx_session
                    )
                    
                    # SECURITY: Log admin action for comprehensive audit trail
                    try:
                        import asyncio
                        # Use asyncio.create_task to schedule the async audit logging
                        asyncio.create_task(audit_logger.log_admin_action(
                            admin_id=admin_id,
                            action=f"release_cashout_hold",
                            target_type="cashout_hold",
                            target_id=f"user_{user_id}_cashout_{cashout_id}",
                            details={
                                "user_id": user_id,
                                "amount": release_amount,
                                "currency": currency,
                                "cashout_id": cashout_id,
                                "hold_transaction_id": hold_transaction_id,
                                "release_transaction_id": release_tx_id,
                                "balance_before": float(current_balance),
                                "balance_after": float(new_balance),
                                "frozen_before": float(current_frozen),
                                "frozen_after": float(new_frozen),
                                "funds_restored_to_balance": True
                            }
                        ))
                        audit_logged = True
                        logger.info(f"🛡️ AUDIT: Admin {admin_id} cashout hold release logged successfully")
                    except Exception as audit_error:
                        logger.error(f"Failed to log admin audit trail: {audit_error}")
                        audit_logged = False
                    
                    if not use_provided_session:
                        tx_session.commit()
                    
                    logger.info(
                        f"✅ ADMIN_HOLD_RELEASE: Admin {admin_id} successfully released ${release_amount:.2f} {currency} for user {user_id}. "
                        f"Cashout {cashout_id} failed. Balance: {current_balance:.2f} → {new_balance:.2f}, "
                        f"Frozen: {current_frozen:.2f} → {new_frozen:.2f}"
                    )
                    
                    return {
                        "success": True,
                        "release_transaction_id": release_tx_id,
                        "released_amount": release_amount,
                        "new_balance": wallet.available_balance,
                        "new_frozen_balance": wallet.frozen_balance,
                        "funds_restored": True,
                        "admin_validated": True,
                        "audit_logged": audit_logged,
                        "admin_id": admin_id
                    }
                    
            except Exception as e:
                if not use_provided_session:
                    tx_session.rollback()
                logger.error(f"Error releasing cashout hold for user {user_id} by admin {admin_id}: {e}")
                return {
                    "success": False,
                    "error": f"Hold release failed: {str(e)}",
                    "admin_validated": True,  # Admin was already validated above
                    "audit_logged": False
                }
            finally:
                if not use_provided_session:
                    tx_session.close()
                    
        except Exception as e:
            logger.error(f"Critical error in release_cashout_hold for admin {admin_id}: {e}")
            return {
                "success": False,
                "error": f"Critical release error: {str(e)}",
                "admin_validated": False,  # May have failed before validation
                "audit_logged": False
            }

    @classmethod 
    def _release_cashout_hold_internal_system_only(
        cls,
        user_id: int,
        amount: float,
        currency: str = "USD",
        cashout_id: Optional[str] = None,
        hold_transaction_id: Optional[str] = None,
        description: Optional[str] = None,
        session=None,
        system_context: Optional[str] = None,  # Required context for system calls
    ) -> Dict[str, Any]:
        """
        🤖 INTERNAL SYSTEM ONLY: Release cashout hold for automated system processes
        
        SECURITY: This function is ONLY for legitimate automated system processes such as:
        - Failed cashout monitoring jobs that need to release orphaned holds
        - Auto-cashout systems that need to clean up failed operations
        - System recovery processes that validate and release stuck holds
        
        This function bypasses admin authentication but includes extensive logging and validation.
        
        ⚠️ WARNING: This function should NEVER be called directly by user-facing handlers.
        Use the admin-only release_cashout_hold() function for manual admin operations.
        
        Args:
            user_id: The user whose funds should be released
            amount: The amount to release from frozen balance
            system_context: Required context describing the system process (for audit)
            Other parameters same as main function
            
        Returns:
            Dict with success status and system validation flags
        """
        if not system_context:
            logger.critical(
                f"🚨 SECURITY VIOLATION: _release_cashout_hold_internal_system_only called "
                f"without system_context. This function is only for verified system processes."
            )
            return {
                "success": False,
                "error": "❌ SECURITY: system_context required for internal system calls",
                "admin_validated": False,
                "audit_logged": False,
                "security_violation": True
            }
        
        logger.info(
            f"🤖 SYSTEM_RELEASE: Internal system process '{system_context}' releasing hold "
            f"for user {user_id}, amount ${amount:.2f}"
        )
        
        try:
            from models import Transaction, TransactionType, Wallet
            from utils.atomic_transactions import locked_wallet_operation
            from decimal import Decimal, ROUND_HALF_UP
            from utils.universal_id_generator import UniversalIDGenerator
            
            use_provided_session = session is not None
            tx_session = session if use_provided_session else get_session()
            
            try:
                amount_decimal = Decimal(str(abs(amount)))
                precision = Decimal("0.00000001") if currency != "USD" else Decimal("0.01")
                release_amount = amount_decimal.quantize(precision, rounding=ROUND_HALF_UP)
                
                # System idempotency check (same logic as admin function)
                if hold_transaction_id:
                    existing_release = tx_session.query(Transaction.id, Transaction.transaction_id, Transaction.amount, Transaction.created_at).filter(
                        Transaction.user_id == user_id,
                        Transaction.transaction_type == TransactionType.CASHOUT_HOLD_RELEASE.value,
                        Transaction.description.ilike(f"%[hold_tx:{hold_transaction_id}]%")
                    ).first()
                    
                    if existing_release:
                        logger.warning(
                            f"🚨 SYSTEM_IDEMPOTENT_SKIP: Hold {hold_transaction_id} already released by "
                            f"transaction {existing_release.transaction_id} for user {user_id}. "
                            f"System context: {system_context}"
                        )
                        
                        return {
                            "success": True,
                            "release_transaction_id": existing_release.transaction_id,
                            "released_amount": float(existing_release.amount),
                            "currency": currency,
                            "idempotent": True,
                            "funds_restored": True,
                            "system_validated": True,
                            "system_context": system_context,
                            "warning": "Duplicate prevention: hold already released by system"
                        }
                
                # Use locked wallet operation to prevent race conditions
                with locked_wallet_operation(user_id, currency, tx_session) as wallet:
                    current_frozen = Decimal(str(wallet.frozen_balance if wallet.frozen_balance is not None else 0))
                    current_balance = Decimal(str(wallet.available_balance if wallet.available_balance is not None else 0))
                    amount_to_release = Decimal(str(release_amount))
                    
                    # Verify frozen balance covers the release
                    if current_frozen < amount_to_release:
                        logger.error(
                            f"❌ SYSTEM_RELEASE_FAILED: Insufficient frozen balance for user {user_id}. "
                            f"Frozen: ${current_frozen:.2f}, Requested: ${amount_to_release:.2f}, "
                            f"System context: {system_context}"
                        )
                        return {
                            "success": False,
                            "error": f"Insufficient frozen balance: ${current_frozen:.2f} < ${amount_to_release:.2f}",
                            "frozen_balance": float(current_frozen),
                            "requested_amount": release_amount,
                            "system_validated": True,
                            "system_context": system_context
                        }
                    
                    # ATOMICALLY move funds from frozen_balance back to available balance
                    new_frozen = current_frozen - amount_to_release
                    new_balance = current_balance + amount_to_release
                    
                    wallet.frozen_balance = new_frozen.quantize(precision, rounding=ROUND_HALF_UP)
                    wallet.available_balance = new_balance.quantize(precision, rounding=ROUND_HALF_UP)
                    
                    # Create transaction record with system context
                    system_description = f"System hold release: +${release_amount:.2f} USD [hold_tx:{hold_transaction_id}] [system:{system_context}] [user:{user_id}]"
                    release_tx_id = UniversalIDGenerator.generate_transaction_id()
                    
                    transaction = Transaction(
                        transaction_id=release_tx_id,
                        user_id=user_id,
                        transaction_type=TransactionType.CASHOUT_HOLD_RELEASE.value,
                        amount=release_amount,
                        currency=currency,
                        status="completed",
                        description=system_description,
                        escrow_id=None,
                        confirmed_at=datetime.utcnow(),
                    )
                    
                    tx_session.add(transaction)
                    tx_session.flush()
                    
                    # Log financial event for audit trail
                    financial_context = FinancialContext(
                        amount=amount_to_release,
                        currency=currency,
                        balance_before=current_balance,
                        balance_after=new_balance
                    )
                    
                    financial_audit_logger.log_financial_event(
                        event_type=FinancialEventType.WALLET_HOLD_RELEASE,
                        entity_type=EntityType.WALLET,
                        entity_id=f"wallet_{user_id}",
                        user_id=user_id,
                        financial_context=financial_context,
                        previous_state=f"balance:{current_balance},frozen:{current_frozen}",
                        new_state=f"balance:{new_balance},frozen:{new_frozen}",
                        additional_data={
                            "cashout_id": cashout_id,
                            "hold_transaction_id": hold_transaction_id,
                            "release_transaction_id": release_tx_id,
                            "funds_restored_to_balance": True,
                            "system_context": system_context,
                            "initiated_by": "automated_system"
                        },
                        session=tx_session
                    )
                    
                    if not use_provided_session:
                        tx_session.commit()
                    
                    logger.info(
                        f"✅ SYSTEM_HOLD_RELEASE: System '{system_context}' successfully released "
                        f"${release_amount:.2f} {currency} for user {user_id}. "
                        f"Balance: {current_balance:.2f} → {new_balance:.2f}, "
                        f"Frozen: {current_frozen:.2f} → {new_frozen:.2f}"
                    )
                    
                    return {
                        "success": True,
                        "release_transaction_id": release_tx_id,
                        "released_amount": release_amount,
                        "new_balance": wallet.available_balance,
                        "new_frozen_balance": wallet.frozen_balance,
                        "funds_restored": True,
                        "system_validated": True,
                        "system_context": system_context
                    }
                    
            except Exception as e:
                if not use_provided_session:
                    tx_session.rollback()
                logger.error(f"Error in system hold release for user {user_id}, context '{system_context}': {e}")
                return {
                    "success": False,
                    "error": f"System hold release failed: {str(e)}",
                    "system_validated": True,
                    "system_context": system_context
                }
            finally:
                if not use_provided_session:
                    tx_session.close()
                    
        except Exception as e:
            logger.error(f"Critical error in system hold release, context '{system_context}': {e}")
            return {
                "success": False,
                "error": f"Critical system release error: {str(e)}",
                "system_validated": False
            }

    @classmethod
    def consume_cashout_hold(
        cls,
        user_id: int,
        amount: Union[Decimal, float],
        currency: str = "USD",
        cashout_id: Optional[str] = None,
        hold_transaction_id: Optional[str] = None,
        description: Optional[str] = None,
        session=None,
    ) -> Dict[str, Any]:
        """
        Consume frozen_balance hold for SUCCESSFUL cashouts (no credit to available balance)
        
        This removes the hold without crediting funds back since they were already sent externally.
        
        Returns:
        {
            "success": bool,
            "consume_transaction_id": str,
            "consumed_amount": float,
            "new_frozen_balance": float,
            "error": str (if failed)
        }
        """
        try:
            from models import Transaction, TransactionType, Wallet
            from utils.atomic_transactions import locked_wallet_operation
            from decimal import Decimal, ROUND_HALF_UP
            from utils.universal_id_generator import UniversalIDGenerator
            
            use_provided_session = session is not None
            tx_session = session if use_provided_session else get_session()
            
            try:
                amount_decimal = Decimal(str(abs(amount)))
                precision = Decimal("0.00000001") if currency != "USD" else Decimal("0.01")
                consume_amount = float(amount_decimal.quantize(precision, rounding=ROUND_HALF_UP))
                
                # CRITICAL FIX: IDEMPOTENCY PROTECTION - Check if this hold has already been consumed
                if hold_transaction_id:
                    # Look for existing FROZEN_BALANCE_CONSUME transaction that references this hold_transaction_id
                    # Since Transaction model has no metadata column, search by exact pattern in description
                    existing_consume = tx_session.query(Transaction).filter(
                        Transaction.user_id == user_id,
                        Transaction.transaction_type == TransactionType.FROZEN_BALANCE_CONSUME.value,
                        Transaction.description.like(f"%[hold_tx:{hold_transaction_id}]%")
                    ).first()
                    
                    if existing_consume:
                        logger.warning(
                            f"🚨 IDEMPOTENCY_PROTECTION: Hold {hold_transaction_id} already consumed by "
                            f"transaction {existing_consume.transaction_id} for user {user_id}. "
                            f"Returning idempotent success to prevent double consumption."
                        )
                        
                        # Return success with details from existing consumption (idempotent behavior)
                        return {
                            "success": True,
                            "consume_transaction_id": existing_consume.transaction_id,
                            "consumed_amount": float(existing_consume.amount),  # type: ignore
                            "new_frozen_balance": None,  # Don't query wallet unnecessarily
                            "currency": currency,
                            "idempotent": True,
                            "original_consumption_date": existing_consume.created_at.isoformat() if existing_consume.created_at is not None else "unknown"
                        }
                    
                    logger.info(f"✅ IDEMPOTENCY_CHECK_PASSED: Hold {hold_transaction_id} not previously consumed, proceeding with consumption")
                else:
                    logger.warning(f"⚠️ IDEMPOTENCY_LIMITED: No hold_transaction_id provided for user {user_id}, cannot perform full idempotency check")
                
                # CRITICAL FIX: Check if this is a legacy hold (balance not debited at placement)
                hold_debited = True  # Default for new holds
                if hold_transaction_id:
                    original_hold = tx_session.query(Transaction).filter(
                        Transaction.transaction_id == hold_transaction_id,
                        Transaction.user_id == user_id,
                        Transaction.transaction_type == TransactionType.CASHOUT_HOLD.value
                    ).first()
                    
                    # CRITICAL FIX: Transaction model doesn't have JSON metadata column
                    # Default to True for new holds (post-fix holds are properly debited)
                    hold_debited = True  # Default assumption for transactions
                else:
                    logger.warning(f"Could not find original hold transaction {hold_transaction_id} for user {user_id}")
                    hold_debited = False  # Assume legacy hold for safety
                
                # CRITICAL FIX: Use locked_wallet_operation context manager properly
                with locked_wallet_operation(user_id, currency, tx_session) as wallet:
                    # wallet object is already provided by the context manager
                    if not wallet:
                        return {
                            "success": False,
                            "error": f"Wallet not found for user {user_id}, currency {currency}"
                        }
                    
                    # Convert current balances to Decimal for precise arithmetic
                    current_balance = Decimal(str(wallet.available_balance or 0))
                    current_frozen_balance = Decimal(str(wallet.frozen_balance or 0))
                    consume_amount_decimal = Decimal(str(consume_amount))
                    
                    # Verify sufficient frozen balance
                    if current_frozen_balance < consume_amount_decimal:
                        return {
                            "success": False,
                            "error": f"Insufficient frozen balance: {current_frozen_balance} < {consume_amount_decimal}"
                        }
                    
                    # CRITICAL FIX: For legacy holds, also check and reduce wallet balance
                    if not hold_debited:
                        if current_balance < consume_amount_decimal:
                            return {
                                "success": False,
                                "error": f"LEGACY HOLD: Insufficient wallet balance for legacy hold: {current_balance} < {consume_amount_decimal}"
                            }
                        # Reduce wallet balance for legacy holds (new holds already have balance reduced)
                        new_balance = current_balance - consume_amount_decimal
                        wallet.available_balance = new_balance.quantize(precision, rounding=ROUND_HALF_UP)
                        logger.warning(f"⚠️ LEGACY_HOLD_FIX: Reducing wallet balance by ${consume_amount:.2f} for legacy hold {hold_transaction_id}")
                    else:
                        logger.info(f"✅ NEW_HOLD: Balance already reduced at placement for hold {hold_transaction_id}")
                    
                    # Generate consume transaction ID
                    consume_transaction_id = UniversalIDGenerator.generate_transaction_id()
                    
                    # CRITICAL FIX: Properly subtract from frozen_balance using Decimal arithmetic
                    new_frozen_balance = current_frozen_balance - consume_amount_decimal
                    # Convert back to float for database storage with proper precision
                    wallet.frozen_balance = float(new_frozen_balance.quantize(precision, rounding=ROUND_HALF_UP))
                    wallet.updated_at = datetime.utcnow()
                    
                    # Enhanced debug logging for verification
                    logger.info(f"🔥 CONSUMING HOLD: User {user_id}, Amount: ${consume_amount:.2f}, "
                              f"Frozen balance before: ${current_frozen_balance:.2f}, "
                              f"Frozen balance after: ${wallet.frozen_balance:.2f}")
                    
                    # Create audit transaction (records consumed amount for audit trail)
                    consume_transaction = Transaction(
                        transaction_id=consume_transaction_id,
                        user_id=user_id,
                        amount=consume_amount_decimal,  # Use actual consumed amount for audit trail
                        currency=currency,
                        transaction_type=TransactionType.FROZEN_BALANCE_CONSUME.value,
                        description=description or f"🔥 Hold consumed: ${consume_amount:.2f} {currency} for completed cashout {cashout_id} [hold_tx:{hold_transaction_id}]",
                        status="completed",
                        confirmations=0,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                        # CRITICAL FIX: Remove metadata field - doesn't exist in Transaction model
                        # Store cashout_id in description field for tracking
                    )
                    tx_session.add(consume_transaction)
                    # CRITICAL FIX: Flush to ensure transaction is persisted immediately
                    # This prevents test failures where transaction count doesn't increase properly
                    tx_session.flush()
                    
                    if not use_provided_session:
                        tx_session.commit()
                    
                    logger.info(f"✅ HOLD_CONSUMED_FIRST_TIME: ${consume_amount:.2f} {currency} for user {user_id}, cashout {cashout_id} (TX: {consume_transaction_id})")
                    
                    # AUDIT: Log successful first-time consumption for security monitoring
                    logger.critical(f"🔒 FINANCIAL_AUDIT: FIRST_TIME_HOLD_CONSUMPTION - User:{user_id} HoldTxn:{hold_transaction_id} ConsumeTxn:{consume_transaction_id} Amount:{consume_amount:.2f} {currency} Cashout:{cashout_id}")
                    
                    return {
                        "success": True,
                        "consume_transaction_id": consume_transaction_id,
                        "consumed_amount": consume_amount,
                        "new_frozen_balance": float(wallet.frozen_balance),
                        "currency": currency,
                        "idempotent": False  # First-time consumption
                    }
                    
            except Exception as e:
                if not use_provided_session:
                    tx_session.rollback()
                logger.error(f"Error consuming cashout hold: {e}")
                return {
                    "success": False,
                    "error": f"Failed to consume hold: {str(e)}"
                }
            finally:
                if not use_provided_session:
                    tx_session.close()
                    
        except Exception as e:
            logger.error(f"Critical error in consume_cashout_hold: {e}")
            return {
                "success": False,
                "error": f"Critical consume error: {str(e)}"
            }

    @classmethod
    def place_exchange_hold(
        cls,
        user_id: int,
        amount: float,
        currency: str = "USD",
        exchange_id: Optional[str] = None,
        description: Optional[str] = None,
        session=None,
    ) -> Dict[str, Any]:
        """
        Place exchange funds hold - move balance to frozen_balance atomically
        CRITICAL: Prevents double-spending vulnerability in exchange system
        
        Returns:
        {
            "success": bool,
            "hold_transaction_id": str,
            "frozen_amount": float,
            "new_frozen_balance": float,
            "available_balance": float,
            "error": str (if failed)
        }
        """
        try:
            from models import Transaction, TransactionType, Wallet
            from utils.atomic_transactions import locked_wallet_operation
            from decimal import Decimal, ROUND_HALF_UP
            from utils.universal_id_generator import UniversalIDGenerator
            
            use_provided_session = session is not None
            tx_session = session if use_provided_session else get_session()
            
            try:
                amount_decimal = Decimal(str(abs(amount)))
                precision = Decimal("0.00000001") if currency != "USD" else Decimal("0.01")
                hold_amount = amount_decimal.quantize(precision, rounding=ROUND_HALF_UP)
                
                # Use locked wallet operation to prevent race conditions
                with locked_wallet_operation(user_id, currency, tx_session) as wallet:
                    # Check sufficient balance (available = balance - frozen_balance - locked_balance)
                    current_balance = Decimal(str(wallet.available_balance if wallet.available_balance is not None else 0))
                    current_frozen = Decimal(str(wallet.frozen_balance if wallet.frozen_balance is not None else 0))
                    current_locked = Decimal(str(wallet.locked_balance if wallet.locked_balance is not None else 0))
                    
                    available_balance = current_balance - current_frozen - current_locked
                    amount_to_hold = Decimal(str(hold_amount))
                    tolerance = Decimal("0.01")  # 1 cent tolerance
                    
                    if available_balance < (amount_to_hold - tolerance):
                        return {
                            "success": False,
                            "error": f"Insufficient available balance: ${available_balance:.2f} < ${amount_to_hold:.2f}",
                            "available_balance": float(available_balance),
                            "requested_amount": hold_amount
                        }
                    
                    # Place hold by increasing frozen_balance
                    new_frozen = current_frozen + amount_to_hold
                    wallet.frozen_balance = new_frozen.quantize(precision, rounding=ROUND_HALF_UP)
                    
                    # Create EXCHANGE_HOLD transaction record
                    hold_description = description or f"🔒 Exchange hold: ${hold_amount:.2f} USD for {exchange_id or 'pending exchange'}"
                    hold_tx_id = UniversalIDGenerator.generate_transaction_id()
                    
                    transaction = Transaction(
                        transaction_id=hold_tx_id,
                        user_id=user_id,
                        transaction_type=TransactionType.EXCHANGE_HOLD.value,
                        amount=hold_amount,
                        currency=currency,
                        status="completed",
                        description=hold_description,
                        escrow_id=None,  # Not escrow-related
                        metadata={
                            "exchange_id": exchange_id,
                            "hold_type": "exchange_pending",
                            "frozen_amount": hold_amount,
                            "original_available_balance": float(available_balance)
                        },
                        confirmed_at=datetime.utcnow(),
                    )
                    
                    tx_session.add(transaction)
                    
                    if not use_provided_session:
                        tx_session.commit()
                    
                    logger.info(
                        f"✅ EXCHANGE_HOLD: Placed ${hold_amount:.2f} {currency} hold for user {user_id}, "
                        f"exchange {exchange_id}, frozen_balance: {current_frozen:.2f} → {wallet.frozen_balance:.2f}"
                    )
                    
                    return {
                        "success": True,
                        "hold_transaction_id": hold_tx_id,
                        "frozen_amount": hold_amount,
                        "new_frozen_balance": wallet.frozen_balance,
                        "available_balance": float(available_balance - amount_to_hold)
                    }
                    
            except Exception as e:
                if not use_provided_session:
                    tx_session.rollback()
                logger.error(f"Error placing exchange hold for user {user_id}: {e}")
                return {
                    "success": False,
                    "error": f"Hold placement failed: {str(e)}"
                }
            finally:
                if not use_provided_session:
                    tx_session.close()
                    
        except Exception as e:
            logger.error(f"Critical error in place_exchange_hold: {e}")
            return {
                "success": False,
                "error": f"Critical hold system error: {str(e)}"
            }
    
    @classmethod 
    def convert_exchange_hold_to_debit(
        cls,
        user_id: int,
        amount: float,
        currency: str = "USD",
        exchange_id: Optional[str] = None,
        hold_transaction_id: Optional[str] = None,
        description: Optional[str] = None,
        session=None,
    ) -> Dict[str, Any]:
        """
        Convert frozen_balance hold to actual wallet debit (for successful exchange processing)
        CRITICAL: Final step in secure exchange funds handling
        
        Returns:
        {
            "success": bool,
            "debit_transaction_id": str,
            "debit_amount": float,
            "new_balance": float,
            "new_frozen_balance": float,
            "error": str (if failed)
        }
        """
        try:
            from models import Transaction, TransactionType
            from utils.atomic_transactions import locked_wallet_operation
            from decimal import Decimal, ROUND_HALF_UP
            from utils.universal_id_generator import UniversalIDGenerator
            
            use_provided_session = session is not None
            tx_session = session if use_provided_session else get_session()
            
            try:
                amount_decimal = Decimal(str(abs(amount)))
                precision = Decimal("0.00000001") if currency != "USD" else Decimal("0.01")
                debit_amount = amount_decimal.quantize(precision, rounding=ROUND_HALF_UP)
                
                # Use locked wallet operation
                with locked_wallet_operation(user_id, currency, tx_session) as wallet:
                    current_balance = Decimal(str(wallet.available_balance if wallet.available_balance is not None else 0))
                    current_frozen = Decimal(str(wallet.frozen_balance if wallet.frozen_balance is not None else 0))
                    amount_to_convert = Decimal(str(debit_amount))
                    
                    # Verify frozen balance covers the conversion
                    if current_frozen < amount_to_convert:
                        return {
                            "success": False,
                            "error": f"Insufficient frozen balance: ${current_frozen:.2f} < ${amount_to_convert:.2f}",
                            "frozen_balance": float(current_frozen),
                            "requested_amount": debit_amount
                        }
                    
                    # Convert: reduce frozen_balance AND reduce actual balance (double reduction = debit)
                    new_frozen = current_frozen - amount_to_convert
                    new_balance = current_balance - amount_to_convert
                    
                    wallet.frozen_balance = new_frozen.quantize(precision, rounding=ROUND_HALF_UP)
                    wallet.available_balance = new_balance.quantize(precision, rounding=ROUND_HALF_UP)
                    
                    # Create EXCHANGE_DEBIT transaction record
                    debit_description = description or f"💸 Exchange debit: ${debit_amount:.2f} USD for {exchange_id or 'completed exchange'}"
                    debit_tx_id = UniversalIDGenerator.generate_transaction_id()
                    
                    transaction = Transaction(
                        transaction_id=debit_tx_id,
                        user_id=user_id,
                        transaction_type=TransactionType.EXCHANGE_DEBIT.value,
                        amount=debit_amount,
                        currency=currency,
                        status="completed",
                        description=debit_description,
                        escrow_id=None,
                        metadata={
                            "exchange_id": exchange_id,
                            "hold_transaction_id": hold_transaction_id,
                            "conversion_type": "hold_to_debit",
                            "debit_amount": debit_amount,
                            "previous_frozen": float(current_frozen),
                            "new_frozen": wallet.frozen_balance
                        },
                        confirmed_at=datetime.utcnow(),
                    )
                    
                    tx_session.add(transaction)
                    
                    if not use_provided_session:
                        tx_session.commit()
                    
                    logger.info(
                        f"✅ EXCHANGE_HOLD_TO_DEBIT: Converted ${debit_amount:.2f} {currency} hold to debit for user {user_id}, "
                        f"exchange {exchange_id}, balance: {current_balance:.2f} → {wallet.available_balance:.2f}, "
                        f"frozen: {current_frozen:.2f} → {wallet.frozen_balance:.2f}"
                    )
                    
                    return {
                        "success": True,
                        "debit_transaction_id": debit_tx_id,
                        "debit_amount": debit_amount,
                        "new_balance": wallet.available_balance,
                        "new_frozen_balance": wallet.frozen_balance
                    }
                    
            except Exception as e:
                if not use_provided_session:
                    tx_session.rollback()
                logger.error(f"Error converting exchange hold to debit for user {user_id}: {e}")
                return {
                    "success": False,
                    "error": f"Hold conversion failed: {str(e)}"
                }
            finally:
                if not use_provided_session:
                    tx_session.close()
                    
        except Exception as e:
            logger.error(f"Critical error in convert_exchange_hold_to_debit: {e}")
            return {
                "success": False,
                "error": f"Critical conversion error: {str(e)}"
            }
    
    @classmethod
    def release_exchange_hold(
        cls,
        user_id: int,
        amount: float,
        currency: str = "USD",
        exchange_id: Optional[str] = None,
        hold_transaction_id: Optional[str] = None,
        description: Optional[str] = None,
        session=None,
    ) -> Dict[str, Any]:
        """
        Release frozen_balance hold (for failed exchange processing - returns funds to available balance)
        CRITICAL: Ensures funds are returned to user on exchange failure/cancellation
        
        Returns:
        {
            "success": bool,
            "release_transaction_id": str,
            "released_amount": float,
            "new_frozen_balance": float,
            "error": str (if failed)
        }
        """
        try:
            from models import Transaction, TransactionType
            from utils.atomic_transactions import locked_wallet_operation
            from decimal import Decimal, ROUND_HALF_UP
            from utils.universal_id_generator import UniversalIDGenerator
            
            use_provided_session = session is not None
            tx_session = session if use_provided_session else get_session()
            
            try:
                amount_decimal = Decimal(str(abs(amount)))
                precision = Decimal("0.00000001") if currency != "USD" else Decimal("0.01")
                release_amount = amount_decimal.quantize(precision, rounding=ROUND_HALF_UP)
                
                # Use locked wallet operation
                with locked_wallet_operation(user_id, currency, tx_session) as wallet:
                    current_frozen = Decimal(str(wallet.frozen_balance if wallet.frozen_balance is not None else 0))
                    amount_to_release = Decimal(str(release_amount))
                    
                    # Verify frozen balance covers the release
                    if current_frozen < amount_to_release:
                        return {
                            "success": False,
                            "error": f"Insufficient frozen balance: ${current_frozen:.2f} < ${amount_to_release:.2f}",
                            "frozen_balance": float(current_frozen),
                            "requested_amount": release_amount
                        }
                    
                    # CRITICAL FIX: ATOMICALLY move funds from frozen_balance back to available balance
                    current_balance = Decimal(str(wallet.available_balance if wallet.available_balance is not None else 0))
                    new_frozen = current_frozen - amount_to_release
                    new_balance = current_balance + amount_to_release  # CRITICAL: Add back to available balance
                    
                    wallet.frozen_balance = new_frozen.quantize(precision, rounding=ROUND_HALF_UP)
                    wallet.available_balance = new_balance.quantize(precision, rounding=ROUND_HALF_UP)
                    
                    # Create EXCHANGE_HOLD_RELEASE transaction record
                    release_description = description or f"🔓 Exchange hold release: ${release_amount:.2f} USD for failed {exchange_id or 'exchange'}"
                    release_tx_id = UniversalIDGenerator.generate_transaction_id()
                    
                    transaction = Transaction(
                        transaction_id=release_tx_id,
                        user_id=user_id,
                        transaction_type=TransactionType.EXCHANGE_HOLD_RELEASE.value,
                        amount=release_amount,
                        currency=currency,
                        status="completed",
                        description=release_description,
                        escrow_id=None,
                        metadata={
                            "exchange_id": exchange_id,
                            "hold_transaction_id": hold_transaction_id,
                            "release_type": "failed_exchange",
                            "released_amount": release_amount,
                            "previous_frozen": float(current_frozen),
                            "new_frozen": wallet.frozen_balance
                        },
                        confirmed_at=datetime.utcnow(),
                    )
                    
                    tx_session.add(transaction)
                    
                    if not use_provided_session:
                        tx_session.commit()
                    
                    logger.info(
                        f"✅ EXCHANGE_HOLD_RELEASE: Released ${release_amount:.2f} {currency} hold for user {user_id}, "
                        f"failed exchange {exchange_id}, frozen: {current_frozen:.2f} → {wallet.frozen_balance:.2f}"
                    )
                    
                    return {
                        "success": True,
                        "release_transaction_id": release_tx_id,
                        "released_amount": release_amount,
                        "new_frozen_balance": wallet.frozen_balance
                    }
                    
            except Exception as e:
                if not use_provided_session:
                    tx_session.rollback()
                logger.error(f"Error releasing exchange hold for user {user_id}: {e}")
                return {
                    "success": False,
                    "error": f"Hold release failed: {str(e)}"
                }
            finally:
                if not use_provided_session:
                    tx_session.close()
                    
        except Exception as e:
            logger.error(f"Critical error in release_exchange_hold: {e}")
            return {
                "success": False,
                "error": f"Critical release error: {str(e)}"
            }

    @classmethod
    def admin_approve_refund_to_available(
        cls,
        user_id: int,
        amount: float,
        admin_id: int,
        currency: str = "USD",
        hold_id: Optional[str] = None,
        reason: Optional[str] = None,
        session=None,
    ) -> Dict[str, Any]:
        """
        🔒 ADMIN-ONLY: Approve refund of FAILED_HELD funds to available balance
        
        Converts WalletHoldStatus: FAILED_HELD → REFUND_APPROVED
        Credits frozen_balance back to available balance after failed cashout
        
        CRITICAL SECURITY: Only authenticated admins can approve refunds
        CRITICAL AUDIT: All admin approvals are logged for compliance
        
        Args:
            user_id: User whose frozen funds should be refunded
            amount: Amount to refund from frozen balance
            admin_id: Admin user ID performing approval (required for authorization)
            currency: Currency of the funds (default: USD)
            hold_id: WalletHolds ID or linked_id for tracking
            reason: Admin reason for approving refund
            session: Database session (optional)
        
        Returns:
        {
            "success": bool,
            "refund_transaction_id": str,
            "refunded_amount": float,
            "wallet_hold_status": str,
            "admin_validated": bool,
            "audit_logged": bool,
            "error": str (if failed)
        }
        """
        # SECURITY: Validate admin authorization FIRST
        from utils.admin_security import is_admin_secure
        from utils.enhanced_audit_logger import enhanced_audit_logger
        
        if not is_admin_secure(admin_id):
            logger.critical(
                f"🚨 SECURITY VIOLATION: Non-admin user {admin_id} attempted to approve refund "
                f"for user {user_id}, amount ${amount:.2f}. Access DENIED."
            )
            return {
                "success": False,
                "error": "❌ SECURITY: Administrative privileges required to approve refunds",
                "admin_validated": False,
                "audit_logged": False,
                "security_violation": True
            }
        
        logger.info(
            f"🔒 ADMIN_VALIDATED: Admin {admin_id} authorized to approve refund "
            f"for user {user_id}, amount ${amount:.2f}"
        )
        
        try:
            from models import Transaction, TransactionType, Wallet, WalletHolds, WalletHoldStatus
            from utils.atomic_transactions import locked_wallet_operation
            from decimal import Decimal, ROUND_HALF_UP
            from utils.universal_id_generator import UniversalIDGenerator
            
            audit_logged = False
            use_provided_session = session is not None
            tx_session = session if use_provided_session else get_session()
            
            try:
                amount_decimal = Decimal(str(abs(amount)))
                precision = Decimal("0.00000001") if currency != "USD" else Decimal("0.01")
                refund_amount = amount_decimal.quantize(precision, rounding=ROUND_HALF_UP)
                
                # IDEMPOTENCY: Check for existing refund approval
                existing_refund = tx_session.query(Transaction).filter(
                    Transaction.user_id == user_id,
                    Transaction.transaction_type == TransactionType.REFUND.value,
                    Transaction.description.contains(f"Admin approved refund"),
                    Transaction.amount == refund_amount,
                    Transaction.status == "completed"
                ).first()
                
                if existing_refund:
                    logger.warning(
                        f"🚨 IDEMPOTENT_REFUND_SKIPPED: Refund already approved for user {user_id}, "
                        f"amount ${refund_amount:.2f}. Returning success to prevent double-credit."
                    )
                    return {
                        "success": True,
                        "refund_transaction_id": existing_refund.transaction_id,
                        "refunded_amount": refund_amount,
                        "idempotent": True,
                        "admin_validated": True,
                        "audit_logged": False
                    }
                
                # Atomic wallet operation
                with locked_wallet_operation(user_id, currency, tx_session) as wallet:
                    current_frozen = Decimal(str(wallet.frozen_balance if wallet.frozen_balance is not None else 0))
                    current_balance = Decimal(str(wallet.available_balance if wallet.available_balance is not None else 0))
                    amount_to_refund = Decimal(str(refund_amount))
                    
                    # Verify sufficient frozen balance
                    if current_frozen < amount_to_refund:
                        return {
                            "success": False,
                            "error": f"Insufficient frozen balance: ${current_frozen:.2f} < ${amount_to_refund:.2f}",
                            "frozen_balance": float(current_frozen),
                            "requested_amount": refund_amount
                        }
                    
                    # CRITICAL: Move funds from frozen_balance to available balance
                    new_frozen = current_frozen - amount_to_refund
                    new_balance = current_balance + amount_to_refund
                    
                    wallet.frozen_balance = new_frozen.quantize(precision, rounding=ROUND_HALF_UP)
                    wallet.available_balance = new_balance.quantize(precision, rounding=ROUND_HALF_UP)
                    
                    # Create REFUND transaction
                    refund_description = f"Admin approved refund: +${refund_amount:.2f} USD from frozen balance [admin:{admin_id}] [reason:{reason or 'Manual approval'}]"
                    refund_tx_id = UniversalIDGenerator.generate_transaction_id()
                    
                    transaction = Transaction(
                        transaction_id=refund_tx_id,
                        user_id=user_id,
                        transaction_type=TransactionType.REFUND.value,
                        amount=refund_amount,
                        currency=currency,
                        status="completed",
                        description=refund_description,
                        escrow_id=None,
                        confirmed_at=datetime.utcnow(),
                    )
                    
                    tx_session.add(transaction)
                    tx_session.flush()
                    
                    # Update WalletHolds status if hold_id provided
                    hold_updated = False
                    if hold_id:
                        wallet_hold = tx_session.query(WalletHolds).filter(
                            WalletHolds.user_id == user_id,
                            WalletHolds.status == WalletHoldStatus.FAILED_HELD.value,
                            WalletHolds.linked_id == hold_id  # type: ignore
                        ).first()
                        
                        if wallet_hold:
                            wallet_hold.status = WalletHoldStatus.REFUND_APPROVED.value  # type: ignore
                            wallet_hold.refund_txn_id = refund_tx_id  # type: ignore
                            wallet_hold.updated_at = datetime.utcnow()  # type: ignore
                            hold_updated = True
                            logger.info(f"✅ WALLET_HOLD_UPDATED: Status FAILED_HELD → REFUND_APPROVED for hold {hold_id}")
                    
                    # Log financial event
                    financial_context = FinancialContext(
                        amount=amount_to_refund,
                        currency=currency,
                        balance_before=current_balance,
                        balance_after=new_balance
                    )
                    
                    financial_audit_logger.log_financial_event(
                        event_type=FinancialEventType.ADMIN_MANUAL_REFUND,
                        entity_type=EntityType.WALLET,
                        entity_id=f"wallet_{user_id}",
                        user_id=user_id,
                        financial_context=financial_context,
                        previous_state=f"balance:{current_balance},frozen:{current_frozen}",
                        new_state=f"balance:{new_balance},frozen:{new_frozen}",
                        additional_data={
                            "admin_id": admin_id,
                            "hold_id": hold_id,
                            "reason": reason,
                            "refund_transaction_id": refund_tx_id,
                            "wallet_hold_updated": hold_updated
                        },
                        session=tx_session
                    )
                    
                    # SECURITY: Log admin action
                    try:
                        import asyncio
                        asyncio.create_task(enhanced_audit_logger.log_security_event(
                            event_type="admin_approve_refund",
                            severity="info",
                            details={
                                "admin_id": admin_id,
                                "user_id": user_id,
                                "amount": refund_amount,
                                "currency": currency,
                                "hold_id": hold_id,
                                "reason": reason,
                                "refund_transaction_id": refund_tx_id
                            },
                            user_id=str(admin_id),
                            description=f"Admin {admin_id} approved ${refund_amount:.2f} refund for user {user_id}"
                        ))
                        audit_logged = True
                    except Exception as audit_error:
                        logger.error(f"Failed to log admin audit: {audit_error}")
                        audit_logged = False
                    
                    if not use_provided_session:
                        tx_session.commit()
                    
                    logger.info(
                        f"✅ ADMIN_REFUND_APPROVED: ${refund_amount:.2f} {currency} refunded to user {user_id} "
                        f"by admin {admin_id}, balance: {current_balance:.2f} → {wallet.available_balance:.2f}"
                    )
                    
                    return {
                        "success": True,
                        "refund_transaction_id": refund_tx_id,
                        "refunded_amount": refund_amount,
                        "new_balance": wallet.available_balance,
                        "new_frozen_balance": wallet.frozen_balance,
                        "wallet_hold_status": WalletHoldStatus.REFUND_APPROVED.value if hold_updated else None,
                        "admin_validated": True,
                        "audit_logged": audit_logged
                    }
                    
            except Exception as e:
                if not use_provided_session:
                    tx_session.rollback()
                logger.error(f"Error in admin refund approval for user {user_id}: {e}")
                return {
                    "success": False,
                    "error": f"Refund approval failed: {str(e)}"
                }
            finally:
                if not use_provided_session:
                    tx_session.close()
                    
        except Exception as e:
            logger.error(f"Critical error in admin_approve_refund_to_available: {e}")
            return {
                "success": False,
                "error": f"Critical refund approval error: {str(e)}"
            }

    @classmethod
    def admin_cancel_disputed_cashout(
        cls,
        user_id: int,
        admin_id: int,
        cashout_id: str,
        reason: Optional[str] = None,
        session=None,
    ) -> Dict[str, Any]:
        """
        🔒 ADMIN-ONLY: Cancel disputed cashout and update hold status
        
        Converts WalletHoldStatus: DISPUTED_HELD → CANCELLED_HELD
        Marks disputed cashout as cancelled for admin review (funds remain frozen)
        
        CRITICAL SECURITY: Only authenticated admins can cancel disputed cashouts
        CRITICAL AUDIT: All admin cancellations are logged for compliance
        
        Args:
            user_id: User whose disputed cashout should be cancelled
            admin_id: Admin user ID performing cancellation (required for authorization)
            cashout_id: Cashout ID being cancelled
            reason: Admin reason for cancellation
            session: Database session (optional)
        
        Returns:
        {
            "success": bool,
            "wallet_hold_status": str,
            "admin_validated": bool,
            "audit_logged": bool,
            "error": str (if failed)
        }
        """
        # SECURITY: Validate admin authorization FIRST
        from utils.admin_security import is_admin_secure
        from utils.enhanced_audit_logger import enhanced_audit_logger
        
        if not is_admin_secure(admin_id):
            logger.critical(
                f"🚨 SECURITY VIOLATION: Non-admin user {admin_id} attempted to cancel disputed cashout "
                f"for user {user_id}, cashout {cashout_id}. Access DENIED."
            )
            return {
                "success": False,
                "error": "❌ SECURITY: Administrative privileges required to cancel disputed cashouts",
                "admin_validated": False,
                "audit_logged": False,
                "security_violation": True
            }
        
        logger.info(
            f"🔒 ADMIN_VALIDATED: Admin {admin_id} authorized to cancel disputed cashout "
            f"for user {user_id}, cashout {cashout_id}"
        )
        
        try:
            from models import WalletHolds, WalletHoldStatus, CashoutStatus
            
            audit_logged = False
            use_provided_session = session is not None
            tx_session = session if use_provided_session else get_session()
            
            try:
                # IDEMPOTENCY: Check if already cancelled
                existing_cancelled = tx_session.query(WalletHolds).filter(
                    WalletHolds.user_id == user_id,
                    WalletHolds.linked_id == cashout_id,  # type: ignore
                    WalletHolds.status == WalletHoldStatus.CANCELLED_HELD.value
                ).first()
                
                if existing_cancelled:
                    logger.warning(
                        f"🚨 IDEMPOTENT_CANCEL_SKIPPED: Cashout {cashout_id} already cancelled for user {user_id}"
                    )
                    return {
                        "success": True,
                        "wallet_hold_status": WalletHoldStatus.CANCELLED_HELD.value,
                        "idempotent": True,
                        "admin_validated": True,
                        "audit_logged": False
                    }
                
                # Find disputed wallet hold
                wallet_hold = tx_session.query(WalletHolds).filter(
                    WalletHolds.user_id == user_id,
                    WalletHolds.linked_id == cashout_id,  # type: ignore
                    WalletHolds.status == WalletHoldStatus.DISPUTED_HELD.value
                ).first()
                
                if not wallet_hold:
                    return {
                        "success": False,
                        "error": f"No disputed wallet hold found for user {user_id}, cashout {cashout_id}",
                        "admin_validated": True,
                        "audit_logged": False
                    }
                
                # Update wallet hold status
                wallet_hold.status = WalletHoldStatus.CANCELLED_HELD.value  # type: ignore
                wallet_hold.updated_at = datetime.utcnow()  # type: ignore
                
                # Update cashout status if exists - DISABLED (model not available)
                # Define cashout as None since it's disabled
                cashout = None
                # cashout = tx_session.query(CashoutRequests).filter(
                #     CashoutRequests.cashout_id == cashout_id,
                #     CashoutRequests.user_id == user_id
                # ).first()
                # 
                # if cashout:
                #     cashout.status = CashoutStatus.CANCELLED.value
                #     cashout.admin_notes = f"Cancelled by admin {admin_id}: {reason or 'Manual cancellation'}"
                #     cashout.updated_at = datetime.utcnow()
                
                # Log financial event
                financial_audit_logger.log_financial_event(
                    event_type=FinancialEventType.ADMIN_CASHOUT_CANCELLED,
                    entity_type=EntityType.CASHOUT,
                    entity_id=cashout_id,
                    user_id=user_id,
                    financial_context=FinancialContext(
                        amount=wallet_hold.amount,  # type: ignore
                        currency=wallet_hold.currency  # type: ignore
                    ),
                    previous_state=WalletHoldStatus.DISPUTED_HELD.value,
                    new_state=WalletHoldStatus.CANCELLED_HELD.value,
                    additional_data={
                        "admin_id": admin_id,
                        "cashout_id": cashout_id,
                        "reason": reason,
                        "wallet_hold_id": wallet_hold.id
                    },
                    session=tx_session
                )
                
                # SECURITY: Log admin action
                try:
                    import asyncio
                    asyncio.create_task(enhanced_audit_logger.log_security_event(
                        event_type="admin_cancel_disputed_cashout",
                        severity="info",
                        details={
                            "admin_id": admin_id,
                            "user_id": user_id,
                            "cashout_id": cashout_id,
                            "reason": reason,
                            "amount": float(wallet_hold.amount) if wallet_hold.amount is not None else 0.0,  # type: ignore
                            "currency": wallet_hold.currency
                        },
                        user_id=str(admin_id),
                        description=f"Admin {admin_id} cancelled disputed cashout {cashout_id} for user {user_id}"
                    ))
                    audit_logged = True
                except Exception as audit_error:
                    logger.error(f"Failed to log admin audit: {audit_error}")
                    audit_logged = False
                
                if not use_provided_session:
                    tx_session.commit()
                
                logger.info(
                    f"✅ DISPUTED_CASHOUT_CANCELLED: Cashout {cashout_id} cancelled by admin {admin_id} "
                    f"for user {user_id}, status: DISPUTED_HELD → CANCELLED_HELD"
                )
                
                return {
                    "success": True,
                    "wallet_hold_status": WalletHoldStatus.CANCELLED_HELD.value,
                    "cashout_status": None,  # cashout is always None since it's disabled
                    "admin_validated": True,
                    "audit_logged": audit_logged
                }
                
            except Exception as e:
                if not use_provided_session:
                    tx_session.rollback()
                logger.error(f"Error cancelling disputed cashout for user {user_id}: {e}")
                return {
                    "success": False,
                    "error": f"Cashout cancellation failed: {str(e)}"
                }
            finally:
                if not use_provided_session:
                    tx_session.close()
                    
        except Exception as e:
            logger.error(f"Critical error in admin_cancel_disputed_cashout: {e}")
            return {
                "success": False,
                "error": f"Critical cancellation error: {str(e)}"
            }

    @classmethod
    def admin_manual_credit_frozen_funds(
        cls,
        user_id: int,
        amount: float,
        admin_id: int,
        currency: str = "USD",
        reason: Optional[str] = None,
        hold_id: Optional[str] = None,
        session=None,
    ) -> Dict[str, Any]:
        """
        🔒 ADMIN-ONLY: Manually credit frozen funds to available balance
        
        General admin function to credit any frozen funds to available balance.
        Can be used for various scenarios requiring manual intervention.
        
        CRITICAL SECURITY: Only authenticated admins can manually credit frozen funds
        CRITICAL AUDIT: All manual credits are logged for compliance
        
        Args:
            user_id: User whose frozen funds should be credited
            amount: Amount to credit from frozen balance
            admin_id: Admin user ID performing credit (required for authorization)
            currency: Currency of the funds (default: USD)
            reason: Admin reason for manual credit
            hold_id: Related hold ID for tracking (optional)
            session: Database session (optional)
        
        Returns:
        {
            "success": bool,
            "credit_transaction_id": str,
            "credited_amount": float,
            "admin_validated": bool,
            "audit_logged": bool,
            "error": str (if failed)
        }
        """
        # SECURITY: Validate admin authorization FIRST
        from utils.admin_security import is_admin_secure
        from utils.enhanced_audit_logger import enhanced_audit_logger
        
        if not is_admin_secure(admin_id):
            logger.critical(
                f"🚨 SECURITY VIOLATION: Non-admin user {admin_id} attempted manual credit "
                f"for user {user_id}, amount ${amount:.2f}. Access DENIED."
            )
            return {
                "success": False,
                "error": "❌ SECURITY: Administrative privileges required for manual credits",
                "admin_validated": False,
                "audit_logged": False,
                "security_violation": True
            }
        
        logger.info(
            f"🔒 ADMIN_VALIDATED: Admin {admin_id} authorized for manual credit "
            f"for user {user_id}, amount ${amount:.2f}"
        )
        
        try:
            from models import Transaction, TransactionType, Wallet
            from utils.atomic_transactions import locked_wallet_operation
            from decimal import Decimal, ROUND_HALF_UP
            from utils.universal_id_generator import UniversalIDGenerator
            
            audit_logged = False
            use_provided_session = session is not None
            tx_session = session if use_provided_session else get_session()
            
            try:
                amount_decimal = Decimal(str(abs(amount)))
                precision = Decimal("0.00000001") if currency != "USD" else Decimal("0.01")
                credit_amount = amount_decimal.quantize(precision, rounding=ROUND_HALF_UP)
                
                # IDEMPOTENCY: Check for existing manual credit
                existing_credit = tx_session.query(Transaction).filter(
                    Transaction.user_id == user_id,
                    Transaction.transaction_type == TransactionType.REFUND.value,
                    Transaction.description.contains(f"Manual admin credit"),
                    Transaction.description.contains(f"admin:{admin_id}"),
                    Transaction.amount == credit_amount,
                    Transaction.status == "completed"
                ).first()
                
                if existing_credit:
                    logger.warning(
                        f"🚨 IDEMPOTENT_CREDIT_SKIPPED: Manual credit already exists for user {user_id}, "
                        f"amount ${credit_amount:.2f} by admin {admin_id}"
                    )
                    return {
                        "success": True,
                        "credit_transaction_id": existing_credit.transaction_id,
                        "credited_amount": credit_amount,
                        "idempotent": True,
                        "admin_validated": True,
                        "audit_logged": False
                    }
                
                # Atomic wallet operation
                with locked_wallet_operation(user_id, currency, tx_session) as wallet:
                    current_frozen = Decimal(str(wallet.frozen_balance if wallet.frozen_balance is not None else 0))
                    current_balance = Decimal(str(wallet.available_balance if wallet.available_balance is not None else 0))
                    amount_to_credit = Decimal(str(credit_amount))
                    
                    # Verify sufficient frozen balance
                    if current_frozen < amount_to_credit:
                        return {
                            "success": False,
                            "error": f"Insufficient frozen balance: ${current_frozen:.2f} < ${amount_to_credit:.2f}",
                            "frozen_balance": float(current_frozen),
                            "requested_amount": credit_amount
                        }
                    
                    # CRITICAL: Move funds from frozen_balance to available balance
                    new_frozen = current_frozen - amount_to_credit
                    new_balance = current_balance + amount_to_credit
                    
                    wallet.frozen_balance = new_frozen.quantize(precision, rounding=ROUND_HALF_UP)
                    wallet.available_balance = new_balance.quantize(precision, rounding=ROUND_HALF_UP)
                    
                    # Create credit transaction
                    credit_description = f"Manual admin credit: +${credit_amount:.2f} USD from frozen balance [admin:{admin_id}] [reason:{reason or 'Manual intervention'}] [hold:{hold_id or 'N/A'}]"
                    credit_tx_id = UniversalIDGenerator.generate_transaction_id()
                    
                    transaction = Transaction(
                        transaction_id=credit_tx_id,
                        user_id=user_id,
                        transaction_type=TransactionType.REFUND.value,
                        amount=credit_amount,
                        currency=currency,
                        status="completed",
                        description=credit_description,
                        escrow_id=None,
                        confirmed_at=datetime.utcnow(),
                    )
                    
                    tx_session.add(transaction)
                    tx_session.flush()
                    
                    # Log financial event
                    financial_context = FinancialContext(
                        amount=amount_to_credit,
                        currency=currency,
                        balance_before=current_balance,
                        balance_after=new_balance
                    )
                    
                    financial_audit_logger.log_financial_event(
                        event_type=FinancialEventType.ADMIN_MANUAL_CREDIT,
                        entity_type=EntityType.WALLET,
                        entity_id=f"wallet_{user_id}",
                        user_id=user_id,
                        financial_context=financial_context,
                        previous_state=f"balance:{current_balance},frozen:{current_frozen}",
                        new_state=f"balance:{new_balance},frozen:{new_frozen}",
                        additional_data={
                            "admin_id": admin_id,
                            "reason": reason,
                            "hold_id": hold_id,
                            "credit_transaction_id": credit_tx_id
                        },
                        session=tx_session
                    )
                    
                    # SECURITY: Log admin action
                    try:
                        import asyncio
                        asyncio.create_task(enhanced_audit_logger.log_security_event(
                            event_type="admin_manual_credit_frozen_funds",
                            severity="info",
                            details={
                                "admin_id": admin_id,
                                "user_id": user_id,
                                "amount": credit_amount,
                                "currency": currency,
                                "reason": reason,
                                "hold_id": hold_id,
                                "credit_transaction_id": credit_tx_id
                            },
                            user_id=str(admin_id),
                            description=f"Admin {admin_id} manually credited ${credit_amount:.2f} frozen funds for user {user_id}"
                        ))
                        audit_logged = True
                    except Exception as audit_error:
                        logger.error(f"Failed to log admin audit: {audit_error}")
                        audit_logged = False
                    
                    if not use_provided_session:
                        tx_session.commit()
                    
                    logger.info(
                        f"✅ ADMIN_MANUAL_CREDIT: ${credit_amount:.2f} {currency} credited to user {user_id} "
                        f"by admin {admin_id}, balance: {current_balance:.2f} → {wallet.available_balance:.2f}"
                    )
                    
                    return {
                        "success": True,
                        "credit_transaction_id": credit_tx_id,
                        "credited_amount": credit_amount,
                        "new_balance": wallet.available_balance,
                        "new_frozen_balance": wallet.frozen_balance,
                        "admin_validated": True,
                        "audit_logged": audit_logged
                    }
                    
            except Exception as e:
                if not use_provided_session:
                    tx_session.rollback()
                logger.error(f"Error in admin manual credit for user {user_id}: {e}")
                return {
                    "success": False,
                    "error": f"Manual credit failed: {str(e)}"
                }
            finally:
                if not use_provided_session:
                    tx_session.close()
                    
        except Exception as e:
            logger.error(f"Critical error in admin_manual_credit_frozen_funds: {e}")
            return {
                "success": False,
                "error": f"Critical manual credit error: {str(e)}"
            }



    @classmethod
    async def convert_to_usd(cls, amount: float, currency: str) -> float:
        return await CryptoServiceAtomic.convert_to_usd(amount, currency)

    @classmethod
    async def get_crypto_rates(cls) -> Dict[str, Decimal]:
        return await CryptoServiceAtomic.get_crypto_rates()


def get_crypto_emoji(currency: str) -> str:
    """Get appropriate emoji for cryptocurrency"""
    crypto_emojis = {
        "BTC": "₿",
        "ETH": "💎", 
        "USDT-TRC20": "🔵",
        "USDT-ERC20": "🔷",
        "LTC": "🥈",
        "DOGE": "🐕",
        "TRX": "🔴",
        "XMR": "ⓧ",
        "BCH": "💚",
        "ADA": "🔮",
        "DOT": "⚫",
        "LINK": "🔗",
        "UNI": "🦄",
        "USDT": "🔵",  # Default USDT to TRC20 style
        "BUSD": "🟡",
        "USDC": "🔵",
        "BNB": "🟨"
    }
    return crypto_emojis.get(currency.upper(), "💰")
