"""
Overpayment Handling Service
Processes excess payments in exchange orders and credits them to user wallets
"""

import logging
from decimal import Decimal
from database import SessionLocal
from models import Transaction, User
from services.crypto import CryptoServiceAtomic
from services.consolidated_notification_service import consolidated_notification_service
from datetime import datetime
from utils.universal_id_generator import UniversalIDGenerator

logger = logging.getLogger(__name__)


class OverpaymentService:
    """Service to handle overpayments and underpayments in exchange orders and escrow trades.
    
    IMPORTANT: This service does NOT apply to wallet deposits.
    Wallet deposits credit the exact amount received without any tolerance logic.
    """
    
    # Class-level tolerance configuration (accessible to all class methods)
    UNDERPAYMENT_TOLERANCE_USD = None
    
    @classmethod
    def _get_tolerance(cls) -> float:
        """Get current underpayment tolerance, loading from config if not set"""
        if cls.UNDERPAYMENT_TOLERANCE_USD is None:
            from config import Config
            cls.UNDERPAYMENT_TOLERANCE_USD = Config.UNDERPAYMENT_TOLERANCE_USD
            logger.info(f"Underpayment tolerance loaded from config: ${cls.UNDERPAYMENT_TOLERANCE_USD}")
        return cls.UNDERPAYMENT_TOLERANCE_USD
    
    @classmethod
    async def handle_exchange_overpayment(
        cls, 
        user_id: int, 
        order_id: int,
        expected_amount: Decimal, 
        received_amount: Decimal, 
        crypto_currency: str,
        usd_rate: Decimal
    ) -> bool:
        """
        Handle overpayment for exchange orders
        
        Args:
            user_id: User ID who made the overpayment
            order_id: Exchange order ID
            expected_amount: Expected crypto amount
            received_amount: Actual crypto amount received
            crypto_currency: Type of cryptocurrency
            usd_rate: USD conversion rate
            
        Returns:
            bool: True if overpayment was successfully processed
        """
        try:
            # Calculate overpayment amount
            overpayment_crypto = received_amount - expected_amount
            
            if overpayment_crypto <= 0:
                logger.info(f"No overpayment detected for order {order_id}")
                return True
            
            # Convert overpayment to USD - KEEP IN DECIMAL FOR PRECISION
            from decimal import ROUND_HALF_UP
            overpayment_usd_raw = overpayment_crypto * usd_rate
            # Quantize to same precision as credit_user_wallet_atomic (0.01 for USD)
            overpayment_usd = overpayment_usd_raw.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            
            # Minimum overpayment threshold ($0.01)
            if overpayment_usd < Decimal('0.01'):
                logger.info(f"Overpayment too small to credit: ${overpayment_usd:.8f}")
                return True
            
            logger.info(
                f"Processing overpayment for order {order_id}: "
                f"{overpayment_crypto} {crypto_currency} = ${overpayment_usd:.2f}"
            )
            
            # CRITICAL FIX: Use async_managed_session for proper async session handling
            from database import async_managed_session
            from sqlalchemy import select
            from models import Transaction
            
            async with async_managed_session() as session:
                # IDEMPOTENCY CHECK: Prevent duplicate exchange overpayment credits
                # overpayment_usd is already Decimal, no conversion needed
                
                existing_overpayment_stmt = select(Transaction).where(
                    Transaction.user_id == user_id,
                    Transaction.transaction_type == "exchange_overpayment",
                    Transaction.amount == overpayment_usd,  # Direct Decimal comparison
                    Transaction.description.contains(f"#{order_id}"),
                    Transaction.status == "completed"
                )
                existing_result = await session.execute(existing_overpayment_stmt)
                existing_overpayment = existing_result.scalar_one_or_none()
                
                if existing_overpayment:
                    logger.warning(
                        f"🔄 IDEMPOTENT_SKIP: Exchange overpayment already processed for order {order_id} "
                        f"(existing transaction: {existing_overpayment.transaction_id}, amount: ${overpayment_usd:.2f}). "
                        f"Skipping duplicate credit."
                    )
                    return True  # Return success - idempotent behavior
                
                logger.info(f"✅ Idempotency check passed for exchange order {order_id}, proceeding with credit")
                
                # Credit user wallet using async service
                # overpayment_usd is already quantized Decimal, pass directly (no float conversion)
                credit_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                    user_id=user_id,
                    amount=overpayment_usd,  # Pass quantized Decimal directly
                    currency="USD",
                    transaction_type="exchange_overpayment",
                    description=f"Exchange overpayment #{order_id}: +{overpayment_crypto:.6f} {crypto_currency}",
                    session=session
                )
                
                if not credit_success:
                    logger.error(f"Failed to credit overpayment to user {user_id} wallet")
                    return False
                
                # Transaction record is already created by credit_user_wallet_atomic
                logger.info(f"Exchange overpayment credited successfully for order {order_id}")
            
            # Send notification to user
            try:
                await cls._notify_user_overpayment(
                    user_id, overpayment_crypto, crypto_currency, overpayment_usd, order_id
                )
            except Exception as e:
                logger.warning(f"Failed to send overpayment notification to user {user_id}: {e}")
            
            logger.info(
                f"✅ Overpayment processed successfully: User {user_id} credited ${overpayment_usd:.2f}"
            )
            return True
            
        except Exception as e:
            logger.error(f"Error processing overpayment for order {order_id}: {e}")
            return False
    
    @classmethod
    async def handle_exchange_underpayment(
        cls,
        user_id: int,
        order_id: int,
        expected_amount: Decimal,
        received_amount: Decimal,
        crypto_currency: str,
        usd_rate: Decimal
    ) -> bool:
        """
        Handle underpayment for exchange orders within tolerance
        
        Args:
            user_id: User ID who made the underpayment
            order_id: Exchange order ID
            expected_amount: Expected crypto amount
            received_amount: Actual crypto amount received
            crypto_currency: Type of cryptocurrency
            usd_rate: USD conversion rate
            
        Returns:
            bool: True if underpayment was within tolerance and processed
        """
        try:
            # Calculate underpayment amount
            underpayment_crypto = expected_amount - received_amount
            
            if underpayment_crypto <= 0:
                logger.info(f"No underpayment detected for order {order_id}")
                return True
            
            # Convert underpayment to USD - ensure Decimal precision
            underpayment_usd = float(underpayment_crypto * Decimal(str(usd_rate)))
            
            # Apply exchange markup to received amount (platform profit protection)
            from config import Config
            markup_percentage = Config.EXCHANGE_MARKUP_PERCENTAGE
            markup_factor = (100 + markup_percentage) / 100
            
            # Calculate raw USD value and apply markup deduction - ensure Decimal precision
            raw_received_usd = float(received_amount * Decimal(str(usd_rate)))
            received_usd = raw_received_usd / markup_factor  # Deduct markup from credited amount
            
            logger.info(
                f"Exchange markup applied to underpayment: Raw ${raw_received_usd:.2f} → "
                f"${received_usd:.2f} after {markup_percentage}% markup deduction"
            )
            
            logger.info(
                f"Underpayment detected for order {order_id}: "
                f"{underpayment_crypto} {crypto_currency} = ${underpayment_usd:.2f} short"
            )
            
            # Check if underpayment is within tolerance
            tolerance = cls._get_tolerance()
            if underpayment_usd > tolerance:
                logger.warning(
                    f"Underpayment ${underpayment_usd:.2f} exceeds tolerance ${tolerance} for order {order_id}"
                )
                return False
            
            # CRITICAL FIX: Use async_managed_session for proper async session handling
            from database import async_managed_session
            
            async with async_managed_session() as session:
                # Credit user wallet with markup-adjusted amount (platform profit protection)
                credit_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                    user_id=user_id,
                    amount=received_usd,
                    currency="USD",
                    transaction_type="exchange_underpayment",
                    description=f"Exchange partial payment: {received_amount} {crypto_currency} (after {markup_percentage}% markup) from Order #{order_id}",
                    session=session
                )
                
                if not credit_success:
                    logger.error(f"Failed to credit crypto underpayment to user {user_id} wallet")
                    return False
                
                logger.info(
                    f"Crypto underpayment credited with markup protection: User {user_id} received "
                    f"${received_usd:.2f} (instead of ${raw_received_usd:.2f}) for order {order_id}"
                )
                
                # Create transaction record for tracking (async)
                try:
                    transaction_utid = UniversalIDGenerator.generate_transaction_id()
                    underpayment_transaction = Transaction(
                        transaction_id=UniversalIDGenerator.generate_transaction_id(),
                        utid=transaction_utid,
                        user_id=user_id,
                        transaction_type="underpayment",
                        amount=Decimal(str(received_usd)),
                        currency="USD",
                        status="completed",
                        description=f"Order #{order_id} partial payment (${underpayment_usd:.2f} short)",
                        confirmed_at=datetime.utcnow(),
                    )
                    session.add(underpayment_transaction)
                    logger.info(f"Underpayment transaction record created: {transaction_utid}")
                    
                except Exception as e:
                    logger.error(f"Failed to create underpayment transaction record: {e}")
                    raise
            
            # Send notifications to user (both Telegram and email)
            try:
                await cls._notify_user_underpayment(
                    user_id, received_amount, underpayment_crypto, crypto_currency, 
                    received_usd, underpayment_usd, order_id
                )
            except Exception as e:
                logger.warning(f"Failed to send underpayment notification to user {user_id}: {e}")
            
            logger.info(
                f"✅ Underpayment processed successfully: User {user_id} credited ${received_usd:.2f} (${underpayment_usd:.2f} short but within tolerance)"
            )
            return True
            
        except Exception as e:
            logger.error(f"Error processing underpayment for order {order_id}: {e}")
            return False
    
    @classmethod
    async def handle_escrow_overpayment(
        cls,
        user_id: int,
        escrow_id: str,
        expected_amount: Decimal,
        received_amount: Decimal,
        crypto_currency: str,
        usd_rate: Decimal
    ) -> bool:
        """
        Handle overpayment for escrow transactions
        
        Args:
            user_id: Buyer ID who made the overpayment
            escrow_id: Escrow transaction ID
            expected_amount: Expected crypto amount
            received_amount: Actual crypto amount received
            crypto_currency: Type of cryptocurrency
            usd_rate: USD conversion rate
            
        Returns:
            bool: True if overpayment was processed successfully
        """
        try:
            # Calculate overpayment amount (only the excess)
            overpayment_crypto = received_amount - expected_amount
            
            if overpayment_crypto <= 0:
                logger.info(f"No overpayment detected for escrow {escrow_id}")
                return True
            
            # Convert ONLY the overpayment (excess) to USD - KEEP IN DECIMAL FOR PRECISION
            from decimal import ROUND_HALF_UP
            overpayment_usd_raw = overpayment_crypto * usd_rate
            # Quantize to same precision as credit_user_wallet_atomic (0.01 for USD)
            overpayment_usd = overpayment_usd_raw.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            
            logger.info(
                f"Overpayment calculation: Received {received_amount}, Expected {expected_amount}, "
                f"Excess: {overpayment_crypto} {crypto_currency} = ${overpayment_usd:.2f}"
            )
            
            # Minimum threshold for processing (avoid dust amounts)
            if overpayment_usd < Decimal('0.01'):
                logger.info(f"Escrow overpayment too small to process: ${overpayment_usd:.4f} for {escrow_id}")
                return True
            
            logger.info(
                f"Processing escrow overpayment: {overpayment_crypto} {crypto_currency} = ${overpayment_usd:.2f} for escrow {escrow_id}"
            )
            
            # CRITICAL FIX: Use async_managed_session for proper async session handling
            from database import async_managed_session
            from sqlalchemy import select
            
            async with async_managed_session() as session:
                # CRITICAL FIX: Look up escrow database ID for transaction linking (async)
                from models import Escrow
                stmt = select(Escrow).where(Escrow.escrow_id == escrow_id)
                result = await session.execute(stmt)
                escrow_record = result.scalar_one_or_none()
                
                if not escrow_record:
                    logger.error(
                        f"❌ ESCROW_LOOKUP_FAILED: Cannot find escrow {escrow_id} for overpayment credit. "
                        f"This will cause constraint violation!"
                    )
                    return False
                
                escrow_db_id = escrow_record.id
                logger.info(f"✅ Escrow lookup success: {escrow_id} → DB ID {escrow_db_id}")
                
                # IDEMPOTENCY CHECK: Prevent duplicate overpayment credits
                from models import Transaction
                # overpayment_usd is already Decimal, no conversion needed
                
                existing_overpayment_stmt = select(Transaction).where(
                    Transaction.user_id == user_id,
                    Transaction.escrow_id == escrow_db_id,
                    Transaction.transaction_type == "escrow_overpayment",
                    Transaction.amount == overpayment_usd,  # Direct Decimal comparison
                    Transaction.status == "completed"
                )
                existing_result = await session.execute(existing_overpayment_stmt)
                existing_overpayment = existing_result.scalar_one_or_none()
                
                if existing_overpayment:
                    logger.warning(
                        f"🔄 IDEMPOTENT_SKIP: Overpayment already processed for escrow {escrow_id} "
                        f"(existing transaction: {existing_overpayment.transaction_id}, amount: ${overpayment_usd:.2f}). "
                        f"Skipping duplicate credit to prevent over-crediting user wallet."
                    )
                    return True  # Return success - this is not an error, just a duplicate call
                
                logger.info("✅ Idempotency check passed: No existing overpayment found, proceeding with credit")
                
                # Credit user wallet with overpayment amount (pass async session for atomicity)
                # overpayment_usd is already quantized Decimal, pass directly (no float conversion)
                credit_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                    user_id=user_id,
                    amount=overpayment_usd,  # Pass quantized Decimal directly
                    currency="USD",
                    escrow_id=escrow_db_id,  # CRITICAL FIX: Pass escrow database ID for constraint compliance
                    transaction_type="escrow_overpayment",
                    description=f"💰 Overpayment Credit: +${overpayment_usd:.2f} (Trade #{escrow_id} - you paid extra)",
                    session=session
                )
                
                if not credit_success:
                    logger.error(f"Failed to credit escrow overpayment to user {user_id} wallet")
                    return False
                
                # Update EscrowHolding record with overpayment audit trail (atomic with wallet credit)
                try:
                    from models import EscrowHolding, Transaction
                    
                    # Find the most recent transaction for this overpayment credit (async)
                    stmt = select(Transaction).where(
                        Transaction.user_id == user_id,
                        Transaction.transaction_type == "escrow_overpayment",
                        Transaction.description.contains(escrow_id)
                    ).order_by(Transaction.created_at.desc())
                    result = await session.execute(stmt)
                    transaction = result.scalar_one_or_none()
                    
                    transaction_id = transaction.transaction_id if transaction else None
                    
                    # Find and update the EscrowHolding record (async)
                    stmt = select(EscrowHolding).where(EscrowHolding.escrow_id == escrow_id)
                    result = await session.execute(stmt)
                    escrow_holding = result.scalar_one_or_none()
                    
                    if escrow_holding:
                        escrow_holding.overpayment_amount = overpayment_crypto
                        escrow_holding.overpayment_currency = crypto_currency
                        escrow_holding.overpayment_usd_value = Decimal(str(overpayment_usd))
                        escrow_holding.overpayment_transaction_id = transaction_id
                        logger.info(f"Updated EscrowHolding audit trail for overpayment: {escrow_id}")
                    else:
                        logger.warning(f"EscrowHolding record not found for escrow {escrow_id}")
                        
                except Exception as e:
                    logger.error(f"Failed to update EscrowHolding overpayment audit trail for {escrow_id}: {e}")
                    # Allow transaction to rollback if audit trail fails
                    raise
            
            logger.info(f"Escrow overpayment credited successfully for {escrow_id}")
            
            # Send notifications to user (both Telegram and email)
            try:
                await cls._notify_user_escrow_overpayment(
                    user_id, overpayment_crypto, crypto_currency, overpayment_usd, escrow_id
                )
            except Exception as e:
                logger.warning(f"Failed to send escrow overpayment notification to user {user_id}: {e}")
            
            logger.info(
                f"✅ Escrow overpayment processed successfully: User {user_id} credited ${overpayment_usd:.2f}"
            )
            return True
            
        except Exception as e:
            logger.error(f"Error processing escrow overpayment for {escrow_id}: {e}")
            return False
    
    @classmethod
    async def handle_escrow_underpayment(
        cls,
        user_id: int,
        escrow_id: str,
        expected_amount: Decimal,
        received_amount: Decimal,
        crypto_currency: str,
        usd_rate: Decimal
    ) -> bool:
        """
        Handle underpayment for escrow transactions within tolerance
        
        Args:
            user_id: Buyer ID who made the underpayment
            escrow_id: Escrow transaction ID
            expected_amount: Expected crypto amount
            received_amount: Actual crypto amount received
            crypto_currency: Type of cryptocurrency
            usd_rate: USD conversion rate
            
        Returns:
            bool: True if underpayment was within tolerance and processed
        """
        try:
            session = SessionLocal()
            
            # Calculate underpayment amount
            underpayment_crypto = expected_amount - received_amount
            
            if underpayment_crypto <= 0:
                logger.info(f"No underpayment detected for escrow {escrow_id}")
                return True
            
            # Convert underpayment to USD - ensure Decimal precision
            underpayment_usd = float(underpayment_crypto * Decimal(str(usd_rate)))
            
            # Apply exchange markup to received amount (platform profit protection)
            from config import Config
            markup_percentage = Config.EXCHANGE_MARKUP_PERCENTAGE
            markup_factor = (100 + markup_percentage) / 100
            
            # Calculate raw USD value and apply markup deduction - ensure Decimal precision
            raw_received_usd = float(received_amount * Decimal(str(usd_rate)))
            received_usd = raw_received_usd / markup_factor  # Deduct markup from credited amount
            
            logger.info(
                f"Escrow exchange markup applied to underpayment: Raw ${raw_received_usd:.2f} → "
                f"${received_usd:.2f} after {markup_percentage}% markup deduction"
            )
            
            logger.info(
                f"Escrow underpayment detected for {escrow_id}: "
                f"{underpayment_crypto} {crypto_currency} = ${underpayment_usd:.2f} short"
            )
            
            # Check if underpayment is within tolerance
            tolerance = cls._get_tolerance()
            if underpayment_usd > tolerance:
                logger.warning(
                    f"Escrow underpayment ${underpayment_usd:.2f} exceeds tolerance ${tolerance} for {escrow_id}"
                )
                return False
            
            # Credit user wallet with markup-adjusted amount (platform profit protection)
            credit_success = CryptoServiceAtomic.credit_user_wallet_atomic(
                user_id=user_id,
                amount=received_usd,
                currency="USD",
                transaction_type="escrow_underpay",
                description=f"Escrow partial payment: {received_amount} {crypto_currency} (after {markup_percentage}% markup) from {escrow_id}"
            )
            
            if not credit_success:
                logger.error(f"Failed to credit escrow underpayment to user {user_id} wallet")
                session.close()
                return False
            
            logger.info(
                f"Escrow underpayment credited with markup protection: User {user_id} received "
                f"${received_usd:.2f} (instead of ${raw_received_usd:.2f}) for {escrow_id}"
            )
            
            # Create transaction record for tracking
            try:
                transaction_utid = UniversalIDGenerator.generate_transaction_id()  # TX = Transaction records
                underpayment_transaction = Transaction(
                    transaction_id=UniversalIDGenerator.generate_transaction_id(),
                    utid=transaction_utid,
                    user_id=user_id,
                    transaction_type="escrow_short",
                    amount=Decimal(str(received_usd)),
                    currency="USD",
                    status="completed",
                    description=f"Escrow {escrow_id} partial (${underpayment_usd:.2f} short)",
                    confirmed_at=datetime.utcnow(),
                )
                session.add(underpayment_transaction)
                session.commit()
                logger.info(f"Escrow underpayment transaction record created: {transaction_utid}")
                
            except Exception as e:
                logger.error(f"Failed to create escrow underpayment transaction record: {e}")
                session.rollback()
            
            finally:
                session.close()
            
            # Send notifications to user (both Telegram and email)
            try:
                await cls._notify_user_escrow_underpayment(
                    user_id, received_amount, underpayment_crypto, crypto_currency, 
                    received_usd, underpayment_usd, escrow_id
                )
            except Exception as e:
                logger.warning(f"Failed to send escrow underpayment notification to user {user_id}: {e}")
            
            logger.info(
                f"✅ Escrow underpayment processed successfully: User {user_id} credited ${received_usd:.2f} (${underpayment_usd:.2f} short but within tolerance)"
            )
            return True
            
        except Exception as e:
            logger.error(f"Error processing escrow underpayment for {escrow_id}: {e}")
            return False
    
    @classmethod
    async def handle_ngn_overpayment(
        cls, 
        user_id: int, 
        order_id: int,
        expected_amount_ngn: Decimal, 
        received_amount_ngn: Decimal, 
        usd_to_ngn_rate: Decimal
    ) -> bool:
        """
        Handle overpayment for NGN payments
        
        Args:
            user_id: User ID who made the overpayment
            order_id: Order ID (exchange or escrow)
            expected_amount_ngn: Expected NGN amount
            received_amount_ngn: Actual NGN amount received
            usd_to_ngn_rate: USD to NGN conversion rate
            
        Returns:
            bool: True if overpayment was successfully processed
        """
        try:
            session = SessionLocal()
            
            # Calculate overpayment amount in NGN
            overpayment_ngn = received_amount_ngn - expected_amount_ngn
            
            if overpayment_ngn <= 0:
                logger.info(f"No NGN overpayment detected for order {order_id}")
                return True
            
            # Convert overpayment to USD
            overpayment_usd = float(overpayment_ngn / usd_to_ngn_rate)
            
            # Minimum overpayment threshold ($0.01)
            if overpayment_usd < 0.01:
                logger.info(f"NGN overpayment too small to credit: ${overpayment_usd:.8f}")
                return True
            
            logger.info(
                f"Processing NGN overpayment for order {order_id}: "
                f"₦{overpayment_ngn} = ${overpayment_usd:.2f}"
            )
            
            # Credit user wallet using existing service
            credit_success = CryptoServiceAtomic.credit_user_wallet_atomic(
                user_id=user_id,
                amount=overpayment_usd,
                currency="USD",
                transaction_type="ngn_overpayment",
                description=f"NGN overpayment #{order_id}: +₦{overpayment_ngn:,.0f}"
            )
            
            if not credit_success:
                logger.error(f"Failed to credit NGN overpayment to user {user_id} wallet")
                session.close()
                return False
            
            logger.info(f"NGN overpayment credited successfully for order {order_id}")
            session.close()
            
            # Send notification to user
            try:
                await cls._notify_user_ngn_overpayment(
                    user_id, overpayment_ngn, overpayment_usd, order_id
                )
            except Exception as e:
                logger.warning(f"Failed to send NGN overpayment notification to user {user_id}: {e}")
            
            logger.info(
                f"✅ NGN overpayment processed successfully: User {user_id} credited ${overpayment_usd:.2f}"
            )
            return True
            
        except Exception as e:
            logger.error(f"Error processing NGN overpayment for order {order_id}: {e}")
            return False
    
    @classmethod
    async def handle_ngn_underpayment(
        cls,
        user_id: int,
        order_id: int,
        expected_amount_ngn: Decimal,
        received_amount_ngn: Decimal,
        usd_to_ngn_rate: Decimal,
        order_type: str = "exchange"
    ) -> tuple[bool, bool]:
        """
        Handle underpayment for NGN payments with tolerance check
        
        Args:
            user_id: User ID who made the underpayment
            order_id: Order ID (exchange or escrow)
            expected_amount_ngn: Expected NGN amount
            received_amount_ngn: Actual NGN amount received
            usd_to_ngn_rate: USD to NGN conversion rate
            order_type: Type of order ("exchange" or "escrow")
            
        Returns:
            tuple[bool, bool]: (should_accept_payment, is_underpayment)
                - should_accept_payment: True if payment should be accepted despite underpayment
                - is_underpayment: True if this is actually an underpayment
        """
        try:
            # Calculate shortfall amount in NGN
            shortfall_ngn = expected_amount_ngn - received_amount_ngn
            
            if shortfall_ngn <= 0:
                logger.info(f"No NGN underpayment detected for order {order_id}")
                return True, False
            
            # Convert shortfall to USD
            shortfall_usd = float(shortfall_ngn / usd_to_ngn_rate)
            
            # Apply exchange markup to received amount (platform profit protection)
            from config import Config
            markup_percentage = Config.EXCHANGE_MARKUP_PERCENTAGE
            markup_factor = (100 + markup_percentage) / 100
            
            # Calculate raw USD value and apply markup deduction for received amount
            raw_received_usd = float(received_amount_ngn / usd_to_ngn_rate)
            received_usd_after_markup = raw_received_usd / markup_factor  # Deduct markup from credited amount
            
            logger.info(
                f"NGN Exchange markup applied to underpayment: Raw ${raw_received_usd:.2f} → "
                f"${received_usd_after_markup:.2f} after {markup_percentage:.1f}% markup deduction"
            )
            
            tolerance_usd = cls._get_tolerance()
            
            logger.info(
                f"NGN underpayment detected for order {order_id}: "
                f"₦{shortfall_ngn} shortfall = ${shortfall_usd:.2f} (tolerance: ${tolerance_usd})"
            )
            
            # Check if underpayment is within tolerance
            if shortfall_usd <= tolerance_usd:
                logger.info(
                    f"NGN underpayment within tolerance for order {order_id}: "
                    f"${shortfall_usd:.2f} <= ${tolerance_usd}"
                )
                
                # Credit user wallet with markup-adjusted amount (platform profit protection)
                # Use already imported CryptoServiceAtomic from top of file
                credit_success = CryptoServiceAtomic.credit_user_wallet_atomic(
                    user_id=user_id,
                    amount=received_usd_after_markup,
                    currency="USD",
                    transaction_type="ngn_underpayment",
                    description=f"NGN underpayment credit: ₦{received_amount_ngn} (after {markup_percentage}% markup) from Order #{order_id}"
                )
                
                if not credit_success:
                    logger.error(f"Failed to credit NGN underpayment to user {user_id} wallet")
                    return False, True
                
                logger.info(
                    f"NGN underpayment credited with markup protection: User {user_id} received "
                    f"${received_usd_after_markup:.2f} (instead of ${raw_received_usd:.2f}) for order {order_id}"
                )
                
                # Send notification about accepted underpayment
                try:
                    await cls._notify_user_ngn_underpayment_accepted(
                        user_id, shortfall_ngn, shortfall_usd, order_id, order_type
                    )
                except Exception as e:
                    logger.warning(f"Failed to send NGN underpayment acceptance notification: {e}")
                
                return True, True  # Accept payment, it's an underpayment
            else:
                logger.warning(
                    f"NGN underpayment exceeds tolerance for order {order_id}: "
                    f"${shortfall_usd:.2f} > ${tolerance_usd}"
                )
                
                # Send notification about rejected underpayment
                try:
                    await cls._notify_user_ngn_underpayment_rejected(
                        user_id, shortfall_ngn, shortfall_usd, order_id, order_type, tolerance_usd
                    )
                except Exception as e:
                    logger.warning(f"Failed to send NGN underpayment rejection notification: {e}")
                
                return False, True  # Reject payment, it's an underpayment
                
        except Exception as e:
            logger.error(f"Error processing NGN underpayment for order {order_id}: {e}")
            return False, False
    
    @classmethod
    async def _notify_user_overpayment(
        cls, 
        user_id: int, 
        overpayment_crypto: Decimal, 
        crypto_currency: str, 
        overpayment_usd: float,
        order_id: int
    ):
        """Send notification to user about overpayment credit"""
        try:
            session = SessionLocal()
            user = session.query(User).filter(User.id == user_id).first()
            
            if not user or not getattr(user, 'telegram_id', None):
                logger.warning(f"Cannot notify user {user_id} - no Telegram ID")
                session.close()
                return
            
            message = (
                f"💰 Overpayment Bonus!\n\n"
                f"Order #{order_id}\n"
                f"+{overpayment_crypto} {crypto_currency} = +${overpayment_usd:.2f}\n\n"
                f"✅ Credited to your wallet\n"
                f"/wallet to view balance"
            )
            
            # Send via Telegram bot directly
            from telegram import Bot
            from config import Config
            bot = Bot(Config.BOT_TOKEN)
            await bot.send_message(
                chat_id=int(getattr(user, 'telegram_id')),
                text=message,
                parse_mode='Markdown'
            )
            
            logger.info(f"Overpayment notification sent to user {user_id}")
            session.close()
            
        except Exception as e:
            logger.error(f"Error sending overpayment notification: {e}")
    
    @classmethod
    async def _notify_user_underpayment(
        cls,
        user_id: int,
        received_amount: Decimal,
        underpayment_crypto: Decimal,
        crypto_currency: str,
        received_usd: float,
        underpayment_usd: float,
        order_id: int
    ):
        """Send notifications to user about underpayment within tolerance"""
        from services.consolidated_notification_service import consolidated_notification_service
        try:
            session = SessionLocal()
            user = session.query(User).filter(User.id == user_id).first()
            
            if not user:
                logger.warning(f"Cannot notify user {user_id} - user not found")
                session.close()
                return
            
            # Telegram notification
            if getattr(user, 'telegram_id', None):
                telegram_message = (
                    f"⚠️ **Partial Payment Received**\n\n"
                    f"Exchange Order #{order_id}:\n"
                    f"• **Received:** {received_amount} {crypto_currency}\n"
                    f"• **Short by:** {underpayment_crypto} {crypto_currency} (${underpayment_usd:.2f})\n"
                    f"• **Credited:** ${received_usd:.2f}\n\n"
                    f"✅ Payment accepted\n"
                    f"/wallet to view"
                )
                
                try:
                    await consolidated_notification_service.send_telegram_notification(
                        user_id=user_id,
                        message=telegram_message,
                        parse_mode="Markdown"
                    )
                    logger.info(f"Underpayment Telegram notification sent to user {user_id}")
                except Exception as e:
                    logger.error(f"Failed to send Telegram underpayment notification: {e}")
            
            # Email notification
            if user.email:
                try:
                    from services.consolidated_notification_service import consolidated_notification_service
                    
                    email_subject = f"Partial Payment Accepted - Order #{order_id}"
                    email_body = f"""
                    <h3>Partial Payment Received</h3>
                    <p>Your exchange order has been processed with a partial payment:</p>
                    <ul>
                        <li><strong>Order:</strong> #{order_id}</li>
                        <li><strong>Received:</strong> {received_amount} {crypto_currency}</li>
                        <li><strong>Amount Short:</strong> {underpayment_crypto} {crypto_currency} (${underpayment_usd:.2f})</li>
                        <li><strong>Wallet Credit:</strong> ${received_usd:.2f}</li>
                    </ul>
                    <p>✅ <strong>Payment accepted</strong> as the shortage was within our tolerance.</p>
                    <p>Your wallet has been credited with the received amount.</p>
                    """
                    
                    await consolidated_notification_service.send_email_notification(
                        user_id=user_id,
                        message="",
                        subject=email_subject,
                        html_content=email_body
                    )
                    logger.info(f"Underpayment email notification sent to user {user_id}")
                    
                except Exception as e:
                    logger.error(f"Failed to send email underpayment notification: {e}")
            
            session.close()
            
        except Exception as e:
            logger.error(f"Error sending underpayment notifications: {e}")
    
    @classmethod
    async def _notify_user_escrow_overpayment(
        cls,
        user_id: int,
        overpayment_crypto: Decimal,
        crypto_currency: str,
        overpayment_usd: float,
        escrow_id: str
    ):
        """Send notifications to user about escrow overpayment bonus"""
        try:
            session = SessionLocal()
            user = session.query(User).filter(User.id == user_id).first()
            
            if not user:
                logger.warning(f"Cannot notify user {user_id} - user not found")
                session.close()
                return
            
            # Telegram notification using consistent direct Bot approach
            if user.telegram_id:
                telegram_message = (
                    f"💰 **Bonus Credit Received!**\n\n"
                    f"Escrow Trade: `{escrow_id}`\n"
                    f"• **Extra paid:** {overpayment_crypto} {crypto_currency}\n"
                    f"• **Bonus:** ${overpayment_usd:.2f}\n\n"
                    f"✨ Added to wallet\n"
                    f"/wallet to view"
                )
                
                try:
                    from telegram import Bot
                    from config import Config
                    
                    if Config.BOT_TOKEN:
                        bot = Bot(Config.BOT_TOKEN)
                        await bot.send_message(
                            chat_id=int(user.telegram_id),
                            text=telegram_message,
                            parse_mode="Markdown"
                        )
                        logger.info(f"Escrow overpayment Telegram notification sent to user {user_id}")
                    else:
                        logger.error("BOT_TOKEN not configured - cannot send Telegram notification")
                except Exception as e:
                    logger.error(f"Failed to send Telegram escrow overpayment notification: {e}")
            
            # Email notification
            if user.email:
                try:
                    email_subject = f"Bonus Credit - Escrow {escrow_id}"
                    email_body = f"""
                    <h3>Bonus Credit Received!</h3>
                    <p>You sent a bit extra for your escrow trade and we've credited the excess to your wallet:</p>
                    <ul>
                        <li><strong>Escrow:</strong> {escrow_id}</li>
                        <li><strong>Extra Amount:</strong> {overpayment_crypto} {crypto_currency}</li>
                        <li><strong>Wallet Credit:</strong> ${overpayment_usd:.2f}</li>
                    </ul>
                    <p>✨ <strong>Bonus credit added automatically!</strong> No action needed from you.</p>
                    <p>Your wallet balance has been updated with the excess payment.</p>
                    """
                    
                    from services.email import EmailService
                    email_service = EmailService()
                    
                    success = email_service.send_email(
                        to_email=user.email,
                        subject=email_subject,
                        html_content=email_body
                    )
                    
                    if success:
                        logger.info(f"Escrow overpayment email notification sent to user {user_id}")
                    else:
                        logger.warning(f"Email service returned failure for user {user_id}")
                    
                except Exception as e:
                    logger.error(f"Failed to send email escrow overpayment notification: {e}")
            
            session.close()
            
        except Exception as e:
            logger.error(f"Error sending escrow overpayment notifications: {e}")
    
    @classmethod
    async def _notify_user_escrow_underpayment(
        cls,
        user_id: int,
        received_amount: Decimal,
        underpayment_crypto: Decimal,
        crypto_currency: str,
        received_usd: float,
        underpayment_usd: float,
        escrow_id: str
    ):
        """Send notifications to user about escrow underpayment within tolerance"""
        try:
            session = SessionLocal()
            user = session.query(User).filter(User.id == user_id).first()
            
            if not user:
                logger.warning(f"Cannot notify user {user_id} - user not found")
                session.close()
                return
            
            # Get tolerance for display
            tolerance = cls._get_tolerance()
            
            # Telegram notification
            if getattr(user, 'telegram_id', None):
                telegram_message = (
                    f"⚠️ **Partial Payment Accepted**\n\n"
                    f"Escrow Trade: `{escrow_id}`\n"
                    f"• **Received:** {received_amount} {crypto_currency}\n"
                    f"• **Short by:** {underpayment_crypto} {crypto_currency} (${underpayment_usd:.2f})\n"
                    f"• **Wallet credit:** ${received_usd:.2f}\n\n"
                    f"✅ **Escrow proceeding** (within ${tolerance} tolerance)\n"
                    f"Your trade will continue as normal!"
                )
                
                try:
                    await consolidated_notification_service.send_telegram_notification(
                        user_id=user_id,
                        message=telegram_message,
                        parse_mode="Markdown"
                    )
                    logger.info(f"Escrow underpayment Telegram notification sent to user {user_id}")
                except Exception as e:
                    logger.error(f"Failed to send Telegram escrow underpayment notification: {e}")
            
            # Email notification
            if user.email:
                try:
                    email_subject = f"Partial Payment Accepted - Escrow {escrow_id}"
                    email_body = f"""
                    <h3>Partial Payment Accepted</h3>
                    <p>Your escrow trade payment was slightly short, but we've accepted it within our tolerance:</p>
                    <ul>
                        <li><strong>Escrow:</strong> {escrow_id}</li>
                        <li><strong>Received:</strong> {received_amount} {crypto_currency}</li>
                        <li><strong>Amount Short:</strong> {underpayment_crypto} {crypto_currency} (${underpayment_usd:.2f})</li>
                        <li><strong>Wallet Credit:</strong> ${received_usd:.2f}</li>
                    </ul>
                    <p>✅ <strong>Escrow proceeding</strong> as the shortage was within our ${tolerance} USD tolerance.</p>
                    <p>Your trade will continue as normal and the received amount has been credited to your wallet.</p>
                    """
                    
                    await consolidated_notification_service.send_email_notification(
                        user_id=user_id,
                        message="",
                        subject=email_subject,
                        html_content=email_body
                    )
                    logger.info(f"Escrow underpayment email notification sent to user {user_id}")
                    
                except Exception as e:
                    logger.error(f"Failed to send email escrow underpayment notification: {e}")
            
            session.close()
            
        except Exception as e:
            logger.error(f"Error sending escrow underpayment notifications: {e}")
    
    @classmethod
    async def _notify_user_ngn_overpayment(
        cls, 
        user_id: int, 
        overpayment_ngn: Decimal, 
        overpayment_usd: float,
        order_id: int
    ):
        """Send notification to user about NGN overpayment credit"""
        try:
            session = SessionLocal()
            user = session.query(User).filter(User.id == user_id).first()
            
            if not user or not getattr(user, 'telegram_id', None):
                logger.warning(f"Cannot notify user {user_id} - no Telegram ID")
                session.close()
                return
            
            message = (
                f"💰 NGN Overpayment Bonus!\n\n"
                f"Order #{order_id}\n"
                f"+₦{overpayment_ngn} = +${overpayment_usd:.2f}\n\n"
                f"✅ Credited to your wallet\n"
                f"/wallet to view balance"
            )
            
            # Send via consolidated notification service
            await consolidated_notification_service.send_telegram_notification(
                user_id=user_id,
                message=message,
                parse_mode='Markdown'
            )
            
            logger.info(f"NGN overpayment notification sent to user {user_id}")
            session.close()
            
        except Exception as e:
            logger.error(f"Error sending NGN overpayment notification: {e}")
    
    @classmethod
    async def _notify_user_ngn_underpayment_accepted(
        cls,
        user_id: int,
        shortfall_ngn: Decimal,
        shortfall_usd: float,
        order_id: int,
        order_type: str
    ):
        """Send notification to user about accepted NGN underpayment"""
        try:
            session = SessionLocal()
            user = session.query(User).filter(User.id == user_id).first()
            
            if not user:
                logger.warning(f"Cannot notify user {user_id} - user not found")
                session.close()
                return
            
            # Telegram notification
            if getattr(user, 'telegram_id', None):
                telegram_message = (
                    f"⚠️ **Partial NGN Payment Accepted**\n\n"
                    f"{order_type.title()} Order #{order_id}:\n"
                    f"• **Short by:** ₦{shortfall_ngn} (${shortfall_usd:.2f})\n"
                    f"• Status: ✅ Payment accepted\n\n"
                    f"Your payment was within our tolerance and has been processed.\n"
                    f"/wallet to view"
                )
                
                try:
                    await consolidated_notification_service.send_telegram_notification(
                        user_id=user_id,
                        message=telegram_message,
                        parse_mode="Markdown"
                    )
                    logger.info(f"NGN underpayment acceptance notification sent to user {user_id}")
                except Exception as e:
                    logger.error(f"Failed to send Telegram NGN underpayment acceptance notification: {e}")
            
            # Email notification
            if user.email:
                try:
                    tolerance_usd = cls._get_tolerance()
                    email_subject = f"Partial NGN Payment Accepted - Order #{order_id}"
                    email_body = f"""
                    <h3>Partial NGN Payment Accepted</h3>
                    <p>Your {order_type} order has been processed with a partial NGN payment:</p>
                    <ul>
                        <li><strong>Order:</strong> #{order_id}</li>
                        <li><strong>Amount Short:</strong> ₦{shortfall_ngn} (${shortfall_usd:.2f})</li>
                        <li><strong>Status:</strong> ✅ Payment Accepted</li>
                    </ul>
                    <p>✅ <strong>Payment accepted</strong> as the shortage was within our ${tolerance_usd} USD tolerance.</p>
                    <p>Your order has been processed successfully.</p>
                    """
                    
                    await consolidated_notification_service.send_email_notification(
                        user_id=user_id,
                        subject=email_subject,
                        html_content=email_body
                    )
                    logger.info(f"NGN underpayment acceptance email sent to user {user_id}")
                    
                except Exception as e:
                    logger.error(f"Failed to send NGN underpayment acceptance email: {e}")
            
            session.close()
            
        except Exception as e:
            logger.error(f"Error sending NGN underpayment acceptance notification: {e}")
    
    @classmethod
    async def _notify_user_ngn_underpayment_rejected(
        cls,
        user_id: int,
        shortfall_ngn: Decimal,
        shortfall_usd: float,
        order_id: int,
        order_type: str,
        tolerance_usd: float
    ):
        """Send notification to user about rejected NGN underpayment"""
        try:
            session = SessionLocal()
            user = session.query(User).filter(User.id == user_id).first()
            
            if not user:
                logger.warning(f"Cannot notify user {user_id} - user not found")
                session.close()
                return
            
            # Telegram notification
            if getattr(user, 'telegram_id', None):
                telegram_message = (
                    f"❌ **NGN Payment Insufficient**\n\n"
                    f"{order_type.title()} Order #{order_id}:\n"
                    f"• **Short by:** ₦{shortfall_ngn} (${shortfall_usd:.2f})\n"
                    f"• **Tolerance:** ${tolerance_usd} USD\n"
                    f"• Status: ❌ Payment rejected\n\n"
                    f"Please send the remaining amount to complete your order.\n"
                    f"/help for support"
                )
                
                try:
                    await consolidated_notification_service.send_telegram_notification(
                        user_id=user_id,
                        message=telegram_message,
                        parse_mode="Markdown"
                    )
                    logger.info(f"NGN underpayment rejection notification sent to user {user_id}")
                except Exception as e:
                    logger.error(f"Failed to send Telegram NGN underpayment rejection notification: {e}")
            
            # Email notification
            if user.email:
                try:
                    email_subject = f"NGN Payment Insufficient - Order #{order_id}"
                    email_body = f"""
                    <h3>NGN Payment Insufficient</h3>
                    <p>Your {order_type} order could not be processed due to insufficient NGN payment:</p>
                    <ul>
                        <li><strong>Order:</strong> #{order_id}</li>
                        <li><strong>Amount Short:</strong> ₦{shortfall_ngn} (${shortfall_usd:.2f})</li>
                        <li><strong>Our Tolerance:</strong> ${tolerance_usd} USD</li>
                        <li><strong>Status:</strong> ❌ Payment Rejected</li>
                    </ul>
                    <p>❌ <strong>Payment rejected</strong> as the shortage exceeded our ${tolerance_usd} USD tolerance.</p>
                    <p>Please send the remaining amount to complete your order.</p>
                    """
                    
                    await consolidated_notification_service.send_email_notification(
                        user_id=user_id,
                        subject=email_subject,
                        html_content=email_body
                    )
                    logger.info(f"NGN underpayment rejection email sent to user {user_id}")
                    
                except Exception as e:
                    logger.error(f"Failed to send NGN underpayment rejection email: {e}")
            
            session.close()
            
        except Exception as e:
            logger.error(f"Error sending NGN underpayment rejection notification: {e}")


    @classmethod
    def set_underpayment_tolerance(cls, tolerance_usd: float):
        """Set configurable underpayment tolerance threshold"""
        cls.UNDERPAYMENT_TOLERANCE_USD = tolerance_usd
        logger.info(f"Underpayment tolerance set to ${tolerance_usd}")
    
    @classmethod
    def get_underpayment_tolerance(cls) -> float:
        """Get current underpayment tolerance threshold"""
        return cls._get_tolerance()


# Global service instance
overpayment_service = OverpaymentService()