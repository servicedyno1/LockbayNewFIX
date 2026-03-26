"""
Simplified Escrow Fund Management Service
Handles escrow fund operations using simplified balance system
Uses only available_balance/frozen_balance for all fund tracking
"""
import asyncio
import logging
from decimal import Decimal
from models import Wallet, PlatformRevenue

logger = logging.getLogger(__name__)


class EscrowFundManager:
    """Simplified escrow fund manager using proper balance system"""
    
    @classmethod
    async def cleanup_failed_escrow_holdings(cls, escrow_id: str, session=None) -> dict:
        """
        Release holdings and unfreeze wallet balance when escrow payment fails
        
        This is a safety mechanism to prevent orphaned frozen balances.
        Call this when setting escrow status to PAYMENT_FAILED.
        
        Args:
            escrow_id: The escrow ID to clean up
            session: Optional existing AsyncSession from webhook handler
            
        Returns:
            dict with success status and cleanup details
        """
        from models import EscrowHolding, Escrow, Wallet
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession
        from datetime import datetime
        
        logger.info(f"🧹 CLEANUP_FAILED_ESCROW: Starting cleanup for {escrow_id}")
        
        try:
            # Use provided AsyncSession (webhooks provide this)
            if session and isinstance(session, AsyncSession):
                # 1. Find active escrow holdings (async query)
                result = await session.execute(
                    select(EscrowHolding).filter(
                        EscrowHolding.escrow_id == escrow_id,
                        EscrowHolding.status == "active"
                    )
                )
                holdings = result.scalars().all()
                
                if not holdings:
                    logger.info(f"✅ CLEANUP_COMPLETE: No active holdings found for {escrow_id}")
                    return {"success": True, "holdings_released": 0, "amount_released": 0}
                
                # 2. Get escrow to find buyer_id (async query)
                result = await session.execute(
                    select(Escrow).filter(Escrow.escrow_id == escrow_id)
                )
                escrow = result.scalars().first()
                if not escrow:
                    logger.warning(f"⚠️ CLEANUP_WARNING: Escrow {escrow_id} not found, but holdings exist")
                    return {"success": False, "error": "Escrow not found"}
                
                # 3. Get buyer wallet for unfreezing (async query with lock)
                result = await session.execute(
                    select(Wallet).filter(
                        Wallet.user_id == escrow.buyer_id,
                        Wallet.currency == "USD"
                    ).with_for_update()
                )
                buyer_wallet = result.scalars().first()
                
                total_released = Decimal("0")
                holdings_count = 0
                
                # 4. Process each holding
                for holding in holdings:
                    amount = Decimal(str(holding.amount_held))
                    
                    # Release holding record
                    holding.status = "released"
                    holding.released_at = datetime.utcnow()
                    
                    # Unfreeze wallet balance (only if wallet exists and has frozen balance)
                    if buyer_wallet and buyer_wallet.frozen_balance >= amount:
                        buyer_wallet.frozen_balance -= amount
                        buyer_wallet.available_balance += amount
                        logger.info(f"💰 WALLET_UNFROZEN: Released ${amount} from frozen to available for user {escrow.buyer_id}")
                    elif buyer_wallet:
                        logger.warning(
                            f"⚠️ WALLET_MISMATCH: Frozen balance ${buyer_wallet.frozen_balance} < holding ${amount} "
                            f"for user {escrow.buyer_id} - releasing holding anyway"
                        )
                        # Still mark holding as released even if wallet doesn't match
                    
                    total_released += amount
                    holdings_count += 1
                    logger.info(f"✅ HOLDING_RELEASED: {escrow_id} holding ID {holding.id} - ${amount}")
                
                # Flush changes to session (caller commits the transaction)
                await session.flush()
                
                logger.info(
                    f"✅ CLEANUP_SUCCESS: Released {holdings_count} holdings totaling ${total_released} for {escrow_id}"
                )
                
                return {
                    "success": True,
                    "holdings_released": holdings_count,
                    "amount_released": total_released,
                    "buyer_id": escrow.buyer_id
                }
            else:
                # No session provided - cleanup not possible (webhooks always provide session)
                logger.warning(f"⚠️ CLEANUP_SKIPPED: No AsyncSession provided for {escrow_id}")
                return {"success": False, "error": "No session provided for cleanup"}
                    
        except Exception as e:
            logger.error(f"❌ CLEANUP_FAILED: Error cleaning up escrow {escrow_id}: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod  
    async def process_escrow_payment(
        cls,
        escrow_id: str,
        total_received_usd: Decimal,
        expected_total_usd: Decimal,
        crypto_amount: Decimal,
        crypto_currency: str,
        tx_hash: str,
        session=None,
        funds_source: str = "wallet"
    ) -> dict:
        """
        CRITICAL: Process escrow payment with ACTUAL fund movement and proper verification
        
        Args:
            funds_source: "wallet" (freeze wallet balance) or "external_crypto" (direct deposit, no wallet freeze)
        
        This method:
        1. For wallet payments: Moves funds from buyer wallet to escrow holding
        2. For external crypto: Records payment without touching wallet balance
        3. Creates EscrowHolding record
        4. Creates Transaction record for ledger
        5. Verifies holding creation
        6. Returns proper success/failure based on verification
        """
        from database import async_managed_session
        
        logger.info(
            f"🔄 PROCESSING_ESCROW_PAYMENT: {escrow_id} - "
            f"Received: ${total_received_usd}, Expected: ${expected_total_usd}"
        )
        
        try:
            # Use provided async session or create new one
            if session:
                try:
                    result = await asyncio.wait_for(
                        cls._process_payment_with_session(
                            session, escrow_id, total_received_usd, expected_total_usd,
                            crypto_amount, crypto_currency, tx_hash, funds_source
                        ),
                        timeout=60.0  # 60 second overall timeout for payment processing
                    )
                    return result
                except asyncio.TimeoutError:
                    logger.error(f"❌ PAYMENT_TIMEOUT: Payment processing timed out after 60s for {escrow_id}")
                    return {
                        "success": False,
                        "error": "Payment processing timeout",
                        "critical_error": "Payment processing exceeded timeout limit"
                    }
            else:
                async with async_managed_session() as new_session:
                    try:
                        result = await asyncio.wait_for(
                            cls._process_payment_with_session(
                                new_session, escrow_id, total_received_usd, expected_total_usd,
                                crypto_amount, crypto_currency, tx_hash, funds_source
                            ),
                            timeout=60.0  # 60 second overall timeout for payment processing
                        )
                        return result
                    except asyncio.TimeoutError:
                        logger.error(f"❌ PAYMENT_TIMEOUT: Payment processing timed out after 60s for {escrow_id}")
                        return {
                            "success": False,
                            "error": "Payment processing timeout",
                            "critical_error": "Payment processing exceeded timeout limit"
                        }
                    
        except Exception as e:
            logger.error(f"❌ ESCROW_PAYMENT_PROCESSING_FAILED: {escrow_id} - {e}")
            return {
                "success": False,
                "error": str(e),
                "critical_error": f"Escrow payment processing failed: {e}"
            }
    
    @classmethod
    async def _process_payment_with_session(
        cls,
        session,
        escrow_id: str,
        total_received_usd: Decimal,
        expected_total_usd: Decimal,
        crypto_amount: Decimal,
        crypto_currency: str,
        tx_hash: str,
        funds_source: str
    ) -> dict:
        """Internal method to process payment with async session"""
        from models import EscrowHolding, Escrow, Transaction, TransactionType, Wallet
        from services.escrow_holding_verifier import EscrowHoldingVerifier
        from sqlalchemy import select
        from datetime import datetime
        
        logger.info(f"🚀 PAYMENT_SESSION_START: {escrow_id} - Starting payment processing with session")
        
        # CRITICAL IDEMPOTENCY CHECK FIRST: Check for existing transaction BEFORE any wallet operations
        deterministic_tx_id = f"ESC_{escrow_id}_{tx_hash[:8]}"
        skip_wallet_ops = False  # Initialize flag
        
        logger.info(f"🔍 IDEMPOTENCY_CHECK: {escrow_id} - Checking for existing transaction {deterministic_tx_id}")
        
        try:
            existing_tx_result = await asyncio.wait_for(
                session.execute(
                    select(Transaction).filter(Transaction.transaction_id == deterministic_tx_id)
                ),
                timeout=10.0  # 10 second timeout for idempotency check
            )
            existing_transaction = existing_tx_result.scalars().first()
        except asyncio.TimeoutError:
            logger.error(f"❌ QUERY_TIMEOUT: Idempotency check timed out after 10s for {escrow_id}")
            return {"success": False, "error": "Database query timeout - idempotency check"}
        
        if existing_transaction:
            # Webhook retry detected - verify holding exists before skipping wallet operations
            logger.info(f"✅ TRANSACTION_EXISTS: {escrow_id} - Found existing transaction {deterministic_tx_id}")
            logger.info(
                f"🔄 IDEMPOTENT_RETRY: Transaction {deterministic_tx_id} already exists "
                f"(checking holding state)"
            )
            
            # CRITICAL: Verify holding was also created (could have crashed after transaction but before holding)
            holding_result = await session.execute(
                select(EscrowHolding).filter(EscrowHolding.escrow_id == escrow_id)
            )
            existing_holding = holding_result.scalars().first()
            
            if existing_holding and existing_holding.status == "active":
                # Complete success - both transaction and holding exist
                logger.info(
                    f"✅ IDEMPOTENT_SUCCESS: Transaction and holding both exist for {escrow_id} "
                    f"(skipping all operations)"
                )
                return {
                    "success": True,
                    "idempotent_retry": True,
                    "escrow_held": total_received_usd,
                    "platform_fee_collected": expected_total_usd - total_received_usd,
                    "fund_breakdown": {
                        "base_amount": total_received_usd,
                        "platform_fee": expected_total_usd - total_received_usd, 
                        "total_processed": total_received_usd,
                        "transaction_id": existing_transaction.transaction_id,
                        "holding_id": existing_holding.id
                    }
                }
            else:
                # Partial failure - transaction exists but holding missing/invalid, complete the work
                logger.warning(
                    f"⚠️ PARTIAL_RETRY: Transaction exists but holding missing/invalid for {escrow_id} "
                    f"(will recreate holding without touching wallet)"
                )
                # Fall through to complete holding creation but skip wallet operations
                skip_wallet_ops = True
        else:
            logger.info(f"➡️ NEW_TRANSACTION: {escrow_id} - No existing transaction, proceeding with new")
        
        # Get escrow details
        logger.info(f"🔍 ESCROW_LOOKUP: {escrow_id} - Querying escrow details")
        
        try:
            escrow_result = await asyncio.wait_for(
                session.execute(
                    select(Escrow).filter(Escrow.escrow_id == escrow_id)
                ),
                timeout=10.0  # 10 second timeout for escrow query
            )
            escrow = escrow_result.scalars().first()
        except asyncio.TimeoutError:
            logger.error(f"❌ QUERY_TIMEOUT: Escrow query timed out after 10s for {escrow_id}")
            return {"success": False, "error": "Database query timeout - escrow lookup"}
        
        if escrow:
            logger.info(f"✅ ESCROW_FOUND: {escrow_id} - DB ID: {escrow.id}, Buyer: {escrow.buyer_id}, Status: {escrow.status}")
        else:
            logger.error(f"❌ ESCROW_NOT_FOUND: {escrow_id} - Escrow not found in database")
            return {"success": False, "error": f"Escrow {escrow_id} not found"}
        
        # Get buyer wallet for fund movement (ALWAYS with lock to prevent race conditions)
        logger.info(f"🔍 WALLET_LOOKUP: {escrow_id} - Querying buyer wallet for user {escrow.buyer_id}")
        
        wallet_query = select(Wallet).filter(
            Wallet.user_id == escrow.buyer_id,
            Wallet.currency == "USD"
        ).with_for_update()  # FIXED: Always lock to prevent race conditions
        
        try:
            wallet_result = await asyncio.wait_for(
                session.execute(wallet_query),
                timeout=10.0  # 10 second timeout for wallet query
            )
            buyer_wallet = wallet_result.scalars().first()
        except asyncio.TimeoutError:
            logger.error(f"❌ QUERY_TIMEOUT: Wallet query timed out after 10s for {escrow_id}")
            return {"success": False, "error": "Database query timeout - wallet lookup"}
        
        if buyer_wallet:
            logger.info(f"✅ WALLET_FOUND: {escrow_id} - Buyer wallet found, Available: ${buyer_wallet.available_balance}, Frozen: ${buyer_wallet.frozen_balance}")
        else:
            logger.error(f"❌ WALLET_NOT_FOUND: {escrow_id} - Buyer USD wallet not found for user {escrow.buyer_id}")
            return {"success": False, "error": f"Buyer USD wallet not found for user {escrow.buyer_id}"}
        
        # 1. MOVE FUNDS: Only freeze wallet balance if payment source is wallet (skip if retry with existing transaction)
        if not skip_wallet_ops:
            if funds_source == "wallet":
                # CRITICAL FIX: Check TOTAL wallet balance (trading_credit + available_balance)
                total_wallet_balance = buyer_wallet.available_balance + buyer_wallet.trading_credit
                if total_wallet_balance < total_received_usd:
                    logger.error(f"❌ INSUFFICIENT_WALLET_BALANCE: User {escrow.buyer_id} has ${total_wallet_balance} total (${buyer_wallet.available_balance} available + ${buyer_wallet.trading_credit} credit), needs ${total_received_usd}")
                    return {"success": False, "error": "Insufficient wallet balance"}
                
                logger.info(f"💳 WALLET_DEBIT_START: {escrow_id} - Debiting ${total_received_usd} from wallet (trading_credit first, then available)")
                
                # DUAL-BALANCE DEDUCTION: Use trading_credit first (non-withdrawable), then available_balance
                trading_credit_used = min(buyer_wallet.trading_credit, total_received_usd)
                available_used = total_received_usd - trading_credit_used
                
                buyer_wallet.trading_credit -= trading_credit_used
                buyer_wallet.available_balance -= available_used
                buyer_wallet.frozen_balance += total_received_usd
                
                logger.info(f"✅ WALLET_DEBITED: {escrow_id} - Trading credit used: ${trading_credit_used}, Available used: ${available_used}")
                logger.info(f"✅ NEW_BALANCES: {escrow_id} - Available: ${buyer_wallet.available_balance}, Trading credit: ${buyer_wallet.trading_credit}, Frozen: ${buyer_wallet.frozen_balance}")
                logger.info(
                    f"💰 WALLET_PAYMENT: Moved ${total_received_usd} to frozen (${trading_credit_used} from credit, ${available_used} from available) for user {escrow.buyer_id}"
                )
            else:
                # External crypto payment: No wallet freeze needed
                logger.info(
                    f"💰 EXTERNAL_CRYPTO_PAYMENT: Recording ${total_received_usd} from external source (no wallet freeze) for user {escrow.buyer_id}"
                )
        else:
            logger.info("⏭️ SKIP_WALLET: Skipping wallet operations on retry (transaction already exists)")
        
        # 2. CREATE/REUSE TRANSACTION RECORD for ledger
        if existing_transaction:
            transaction = existing_transaction
            logger.info(f"🔄 TRANSACTION_REUSED: {transaction.transaction_id}")
        else:
            logger.info(f"📝 TRANSACTION_CREATE_START: {escrow_id} - Creating transaction record {deterministic_tx_id}")
            
            # TRANSACTION AMOUNT FIX: Use expected_total_usd (escrow amount) not total_received_usd
            # This ensures transaction history shows the escrow amount, not total payment with overpayment
            # Overpayment is handled separately as escrow_overpayment transaction
            transaction = Transaction(
                transaction_id=deterministic_tx_id,
                user_id=escrow.buyer_id,
                escrow_id=escrow.id,
                transaction_type=TransactionType.ESCROW_PAYMENT.value,
                amount=expected_total_usd,
                currency="USD",
                status="confirmed",
                description=f"Escrow deposit for {escrow_id}",
                blockchain_tx_hash=tx_hash,
                confirmed_at=datetime.utcnow()
            )
            session.add(transaction)
            await session.flush()
            
            logger.info(f"✅ TRANSACTION_FLUSHED: {escrow_id} - Transaction record created and flushed")
            logger.info(f"📝 TRANSACTION_CREATED: {transaction.transaction_id} for ${expected_total_usd}")
            
            # CRITICAL: Record platform fee as revenue if there's a fee to collect
            platform_fee = Decimal(str(escrow.buyer_fee_amount)) + Decimal(str(escrow.seller_fee_amount))
            if platform_fee > Decimal("0"):
                platform_revenue = PlatformRevenue(
                    escrow_id=escrow_id,
                    fee_amount=platform_fee,
                    fee_currency="USD",
                    fee_type="escrow_fee",
                    source_transaction_id=transaction.transaction_id
                )
                session.add(platform_revenue)
                await session.flush()
                
                logger.info(
                    f"💰 PLATFORM_FEE_RECORDED: ${platform_fee} escrow fee for {escrow_id} "
                    f"(Buyer: ${escrow.buyer_fee_amount}, Seller: ${escrow.seller_fee_amount})"
                )
        
        # 3. CREATE ESCROW HOLDING RECORD
        logger.info(f"🏦 HOLDING_CREATE_START: {escrow_id} - Creating holding record for ${total_received_usd}")
        
        try:
            holding_result = await asyncio.wait_for(
                session.execute(
                    select(EscrowHolding).filter(EscrowHolding.escrow_id == escrow_id)
                ),
                timeout=10.0  # 10 second timeout for holding query
            )
            existing_holding = holding_result.scalars().first()
        except asyncio.TimeoutError:
            logger.error(f"❌ QUERY_TIMEOUT: Holding query timed out after 10s for {escrow_id}")
            return {"success": False, "error": "Database query timeout - holding lookup"}
        
        if existing_holding:
            # Update existing holding (reactivate if needed for retry scenarios)
            existing_holding.amount_held = total_received_usd
            existing_holding.status = "active"  # CRITICAL: Reactivate holding on retry
            existing_holding.updated_at = datetime.utcnow()  # Refresh timestamp
            logger.info(f"📋 HOLDING_UPDATED: {escrow_id} holding ID {existing_holding.id} (reactivated)")
            holding_id = existing_holding.id
        else:
            # Create new holding record
            holding = EscrowHolding(
                escrow_id=escrow_id,
                amount_held=total_received_usd,
                currency="USD",
                created_at=datetime.utcnow(),
                status="active"
            )
            session.add(holding)
            await session.flush()
            holding_id = holding.id
            
            logger.info(f"✅ HOLDING_FLUSHED: {escrow_id} - Holding record ID {holding_id} created and flushed")
            logger.info(f"✅ HOLDING_CREATED: {escrow_id} holding ID {holding_id} for ${total_received_usd}")
        
        # 4. VERIFY HOLDING CREATION
        logger.info(f"🔍 VERIFICATION_START: {escrow_id} - Starting holding verification with same session")
        
        verification_result = await EscrowHoldingVerifier.verify_holding_created(
            escrow_id=escrow_id,
            expected_amount=total_received_usd,
            payment_source=funds_source,
            tx_hash=tx_hash,
            auto_recover=True,
            verification_context={
                "crypto_amount": str(crypto_amount),
                "crypto_currency": crypto_currency,
                "funds_source": funds_source
            },
            session=session  # PASS THE EXISTING SESSION TO FIX RACE CONDITION
        )
        
        logger.info(f"✅ VERIFICATION_COMPLETE: {escrow_id} - Success: {verification_result.success}, Auto-recovered: {verification_result.auto_recovered}")
        
        # 5. PREPARE RESULTS - include actual verification results
        # Success if verification passed OR if auto-recovered (recovery counts as success)
        payment_success = verification_result.success or verification_result.auto_recovered
        
        result = {
            "success": payment_success,
            "escrow_held": total_received_usd,
            "platform_fee_collected": expected_total_usd - total_received_usd,
            "holding_verification": {
                "success": verification_result.success,
                "holding_exists": verification_result.holding_exists,
                "holding_id": verification_result.holding_id or holding_id,
                "auto_recovered": verification_result.auto_recovered
            },
            "fund_breakdown": {
                "base_amount": total_received_usd,
                "platform_fee": expected_total_usd - total_received_usd, 
                "total_processed": total_received_usd,
                "transaction_id": transaction.transaction_id,
                "holding_id": holding_id
            }
        }
        
        logger.info(
            f"✅ ESCROW_PAYMENT_COMPLETE: {escrow_id} - "
            f"Transaction: {transaction.transaction_id}, Holding ID: {holding_id}, "
            f"Holding Verified: {verification_result.success}, Auto-recovered: {verification_result.auto_recovered}, "
            f"Overall Success: {payment_success}"
        )
        
        return result

    @classmethod
    def complete_escrow_atomic(cls, escrow_id: str, buyer_id: int, seller_id: int, amount: Decimal | float) -> dict:
        """
        FULLY ATOMIC escrow completion: lock escrow + both wallets, transfer funds, update escrow status
        FIXED: Single transaction with idempotency protection and full atomicity
        """
        from utils.atomic_transactions import atomic_transaction
        from models import Escrow, EscrowStatus
        from datetime import datetime
        
        try:
            with atomic_transaction() as session:
                # FIXED: Let atomic_transaction manage the transaction lifecycle
                
                # Lock escrow first for idempotency check
                escrow = (
                    session.query(Escrow)
                    .filter(Escrow.escrow_id == escrow_id)
                    .with_for_update()
                    .first()
                )
                
                if not escrow:
                    logger.error(f"Escrow {escrow_id} not found")
                    return {"success": False, "error": "Escrow not found"}
                
                # IDEMPOTENCY: Check if already completed
                if escrow.status == EscrowStatus.COMPLETED.value:
                    logger.warning(f"Escrow {escrow_id} already completed - idempotent return")
                    return {"success": True, "already_completed": True}
                
                # Validate escrow is in completable state
                if escrow.status not in [EscrowStatus.ACTIVE.value, EscrowStatus.AWAITING_SELLER.value]:
                    logger.error(f"Escrow {escrow_id} not in completable state: {escrow.status}")
                    return {"success": False, "error": f"Escrow in invalid state: {escrow.status}"}
                
                # Lock buyer wallet (consistent ordering to prevent deadlocks)
                buyer_wallet = (
                    session.query(Wallet)
                    .filter(Wallet.user_id == buyer_id, Wallet.currency == "USD")
                    .with_for_update()
                    .first()
                )
                
                # Lock seller wallet
                seller_wallet = (
                    session.query(Wallet)
                    .filter(Wallet.user_id == seller_id, Wallet.currency == "USD")
                    .with_for_update()
                    .first()
                )
                
                if not buyer_wallet:
                    logger.error(f"Buyer wallet not found for user {buyer_id}")
                    return {"success": False, "error": "Buyer wallet not found"}
                    
                if not seller_wallet:
                    logger.error(f"Seller wallet not found for user {seller_id}")
                    return {"success": False, "error": "Seller wallet not found"}
                
                # Convert to Decimal for precise calculations
                amount_decimal = Decimal(str(amount))
                buyer_frozen = Decimal(str(buyer_wallet.frozen_balance or 0))
                seller_available = Decimal(str(seller_wallet.available_balance or 0))
                
                # Validate buyer has enough frozen funds
                if buyer_frozen < amount_decimal:
                    # CRITICAL: Format Decimal without implicit float conversion
                    frozen_fmt = buyer_frozen.quantize(Decimal("0.01"))
                    needed_fmt = amount_decimal.quantize(Decimal("0.01"))
                    logger.error(f"Insufficient frozen funds: Buyer {buyer_id}, Frozen: ${frozen_fmt}, Needed: ${needed_fmt}")
                    return {"success": False, "error": "Insufficient frozen funds"}
                
                # ATOMIC OPERATIONS:
                # 1. Consume buyer's frozen funds
                buyer_wallet.frozen_balance = buyer_frozen - amount_decimal
                
                # 2. Credit seller's available balance
                seller_wallet.available_balance = seller_available + amount_decimal
                
                # 3. Update escrow status atomically
                escrow.status = EscrowStatus.COMPLETED.value
                escrow.completed_at = datetime.utcnow()
                
                # 4. Check and reward referrer if applicable
                referral_reward_result = None
                try:
                    from models import User
                    from utils.referral import ReferralSystem
                    
                    buyer_user = session.query(User).filter(User.id == buyer_id).first()
                    if buyer_user:
                        referral_reward_result = ReferralSystem.check_and_reward_referrer(buyer_user, session)
                        if referral_reward_result:
                            logger.info(
                                f"🎁 REFERRAL_REWARD: Referrer {referral_reward_result['referrer'].id} "
                                f"earned ${referral_reward_result['reward_amount']} for user {buyer_id}"
                            )
                    
                    seller_user = session.query(User).filter(User.id == seller_id).first()
                    if seller_user:
                        seller_referral_result = ReferralSystem.check_and_reward_referrer(seller_user, session)
                        if seller_referral_result:
                            logger.info(
                                f"🎁 REFERRAL_REWARD: Referrer {seller_referral_result['referrer'].id} "
                                f"earned ${seller_referral_result['reward_amount']} for user {seller_id}"
                            )
                except Exception as referral_error:
                    logger.warning(f"Non-critical referral reward error for {escrow_id}: {referral_error}")
                
                # All operations committed together by atomic_transaction context manager
                # CRITICAL: Format Decimal without implicit float conversion
                amount_fmt = amount_decimal.quantize(Decimal("0.01"))
                logger.info(
                    f"ESCROW_FULLY_ATOMIC_COMPLETE: {escrow_id} - "
                    f"${amount_fmt} transferred from buyer {buyer_id} to seller {seller_id}, status updated"
                )
                
                return {
                    "success": True, 
                    "amount_transferred": amount,
                    "buyer_frozen_consumed": amount,
                    "seller_credited": amount,
                    "escrow_status": "completed",
                    "referral_reward_processed": referral_reward_result is not None
                }
                
        except Exception as e:
            logger.error(f"Error in fully atomic escrow completion for {escrow_id}: {e}")
            return {"success": False, "error": str(e)}