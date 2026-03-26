"""
Atomic Cryptocurrency Deposit Processor

This module implements the architect's recommended design for robust deposit processing
with deterministic state machine and single-lock atomic operations.

Key Features:
- Single database transaction per txid 
- SELECT FOR UPDATE locking for race condition protection
- Atomic wallet crediting with financial safety
- Clear state machine transitions: PENDING_UNCONFIRMED → READY_TO_CREDIT → CREDITED
- Prevents double-crediting with unique constraints
"""

import logging
from decimal import Decimal
from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy import select, and_
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from database import get_sync_db_session
from models import CryptoDeposit, CryptoDepositStatus, Wallet, Transaction
from utils.session_manager import SessionManager
from services.consolidated_notification_service import (
    ConsolidatedNotificationService,
    NotificationRequest,
    NotificationCategory,
    NotificationPriority,
    NotificationChannel
)

logger = logging.getLogger(__name__)


class CryptoDepositProcessorError(Exception):
    """Base exception for crypto deposit processing errors"""
    pass


class CryptoDepositAlreadyProcessedError(CryptoDepositProcessorError):
    """Raised when attempting to process an already credited deposit"""
    pass


class InsufficientConfirmationsError(CryptoDepositProcessorError):
    """Raised when deposit doesn't have enough confirmations"""
    pass


class CryptoDepositProcessor:
    """
    Atomic processor for cryptocurrency deposits with deterministic state machine
    
    This processor implements the fresh architectural design to solve the 
    unconfirmed → confirmed transition problem that was causing wallet 
    funding failures.
    """
    
    def __init__(self):
        pass
        
    async def process_deposit(self, txid: str, provider: str = "blockbee") -> Dict[str, Any]:
        """
        Process a cryptocurrency deposit atomically
        
        Args:
            txid: Blockchain transaction ID
            provider: Payment provider (default: blockbee)
            
        Returns:
            Dict with processing result and status
            
        Raises:
            CryptoDepositProcessorError: For various processing errors
        """
        try:
            with SessionManager.atomic_operation() as session:
                return await self._process_deposit_atomic(session, txid, provider)
                
        except Exception as e:
            logger.error(f"🚨 DEPOSIT_PROCESSOR_ERROR: txid={txid}, provider={provider}, error={e}")
            raise CryptoDepositProcessorError(f"Failed to process deposit: {e}")
    
    async def _process_deposit_atomic(self, session: Session, txid: str, provider: str) -> Dict[str, Any]:
        """
        Atomic deposit processing with SELECT FOR UPDATE locking
        
        This is the core of the new architecture - single atomic operation
        that handles the entire deposit → wallet credit flow.
        """
        # Step 1: Lock the deposit record for atomic processing
        deposit = self._lock_deposit_for_processing(session, txid, provider)
        
        if not deposit:
            return {
                "success": False,
                "reason": "deposit_not_found",
                "message": f"No deposit found for txid {txid}"
            }
        
        logger.info(f"🔒 DEPOSIT_LOCKED: txid={txid}, status={deposit.status}, confirmations={deposit.confirmations}")
        
        # Step 2: Check if already processed
        if deposit.status == CryptoDepositStatus.CREDITED.value:
            logger.info(f"✅ ALREADY_CREDITED: txid={txid} already processed")
            return {
                "success": True,
                "reason": "already_credited",
                "message": "Deposit already credited to wallet",
                "credited_at": deposit.credited_at
            }
        
        # Step 3: Check if ready for processing
        if deposit.status != CryptoDepositStatus.READY_TO_CREDIT.value:
            logger.info(f"⏳ NOT_READY: txid={txid}, status={deposit.status}")
            return {
                "success": False,
                "reason": "not_ready",
                "message": f"Deposit not ready for crediting, status: {deposit.status}"
            }
        
        # Step 4: Verify confirmations
        if deposit.confirmations < deposit.required_confirmations:
            logger.warning(f"⏳ INSUFFICIENT_CONFIRMATIONS: txid={txid}, have={deposit.confirmations}, need={deposit.required_confirmations}")
            return {
                "success": False,
                "reason": "insufficient_confirmations",
                "message": f"Need {deposit.required_confirmations} confirmations, have {deposit.confirmations}"
            }
        
        # Step 5: Credit wallet atomically
        result = await self._credit_wallet_atomic(session, deposit)
        
        if result["success"]:
            # Step 6: Mark as credited
            deposit.status = CryptoDepositStatus.CREDITED.value
            deposit.credited_at = datetime.utcnow()
            session.flush()
            
            logger.info(f"✅ DEPOSIT_CREDITED: txid={txid}, amount={deposit.amount} {deposit.coin}, user_id={deposit.user_id}")
            
            # Step 7: Send user notification about successful deposit
            try:
                # Create notification request for queueing
                self._queue_deposit_notification(deposit)
                logger.info(f"📤 NOTIFICATION_QUEUED: deposit notification for user {deposit.user_id}")
            except Exception as e:
                logger.warning(f"⚠️ NOTIFICATION_FAILED: Could not queue deposit notification: {e}")
        
        return result
    
    def _lock_deposit_for_processing(self, session: Session, txid: str, provider: str) -> Optional[CryptoDeposit]:
        """
        Lock deposit record with SELECT FOR UPDATE for atomic processing
        
        This prevents race conditions when multiple webhook calls arrive
        """
        stmt = (
            select(CryptoDeposit)
            .where(and_(
                CryptoDeposit.txid == txid,
                CryptoDeposit.provider == provider
            ))
            .with_for_update()  # CRITICAL: Exclusive lock for atomic processing
        )
        
        result = session.execute(stmt)
        return result.scalar_one_or_none()
    
    async def _credit_wallet_atomic(self, session: Session, deposit: CryptoDeposit) -> Dict[str, Any]:
        """
        Credit user wallet atomically with all safety checks
        
        This creates the Transaction record and updates the Wallet balance
        in a single atomic operation.
        """
        if not deposit.user_id:
            return {
                "success": False,
                "reason": "no_user",
                "message": "No user linked to deposit"
            }
        
        try:
            # Step 1: Get or create wallet for currency
            wallet = self._get_or_create_wallet(session, deposit.user_id, "USD")  # Convert to USD
            
            # Step 2: Calculate USD amount from crypto FIRST
            credit_amount = await self._calculate_usd_amount(deposit)
            
            # Step 3: Update deposit with calculated USD amount for accurate record-keeping
            deposit.amount_fiat = credit_amount
            session.add(deposit)  # Update the deposit record
            
            # Step 4: Create unique transaction record with correct amount
            transaction = self._create_transaction_record(session, deposit, wallet)
            
            # Step 5: Update wallet balance
            self._update_wallet_balance(session, wallet, credit_amount)
            
            # Step 4: Flush to ensure constraints are checked
            session.flush()
            
            logger.info(f"💰 WALLET_CREDITED: user_id={deposit.user_id}, amount=${credit_amount}, txid={deposit.txid}")
            
            return {
                "success": True,
                "reason": "credited",
                "message": f"Credited ${credit_amount} to wallet",
                "transaction_id": transaction.transaction_id,
                "wallet_id": wallet.id,
                "amount": float(credit_amount)
            }
            
        except IntegrityError as e:
            logger.warning(f"🔒 DUPLICATE_TRANSACTION: txid={deposit.txid}, error={e}")
            # Check if it's a duplicate transaction constraint
            if "uq_transaction_txid_direction" in str(e) or "duplicate" in str(e).lower():
                return {
                    "success": True,
                    "reason": "already_credited",
                    "message": "Transaction already processed (duplicate prevention)"
                }
            raise
            
        except Exception as e:
            logger.error(f"❌ WALLET_CREDIT_ERROR: txid={deposit.txid}, error={e}")
            return {
                "success": False,
                "reason": "credit_error", 
                "message": f"Failed to credit wallet: {e}"
            }
    
    async def _calculate_usd_amount(self, deposit: CryptoDeposit) -> Decimal:
        """
        Calculate USD amount from crypto deposit using current exchange rates
        """
        try:
            # If we already have amount_fiat, use it
            if deposit.amount_fiat:
                return Decimal(str(deposit.amount_fiat))
            
            # Get current exchange rate for the crypto currency
            from services.fastforex_service import FastForexService
            
            forex_service = FastForexService()
            
            # Get crypto-to-USD rate
            crypto_symbol = deposit.coin.upper()
            if crypto_symbol == "BTC":
                rate_key = "BTC"
            elif crypto_symbol == "ETH":
                rate_key = "ETH" 
            elif crypto_symbol == "LTC":
                rate_key = "LTC"
            elif crypto_symbol == "USDT":
                # USDT is approximately 1:1 with USD
                return deposit.amount
            else:
                # For other cryptos, try using the symbol directly
                rate_key = crypto_symbol
            
            # Get current rate using proper async method
            rate = await forex_service.get_crypto_to_usd_rate(rate_key)
            
            if rate:
                usd_amount = deposit.amount * Decimal(str(rate))
                logger.info(f"💱 CRYPTO_CONVERSION: {deposit.amount} {crypto_symbol} = ${usd_amount:.2f} USD (rate: ${rate})")
                return usd_amount
            else:
                # FastForex should always be available - this is an error condition
                logger.error(f"❌ RATE_UNAVAILABLE: FastForex returned no rate for {crypto_symbol}")
                raise ValueError(f"Exchange rate unavailable for {crypto_symbol}")
                
        except Exception as e:
            logger.error(f"❌ CONVERSION_ERROR: Failed to convert {deposit.coin} to USD: {e}")
            # Re-raise the exception - FastForex should always be available
            raise
    
    def _get_or_create_wallet(self, session: Session, user_id: int, currency: str) -> Wallet:
        """Get or create user wallet for currency"""
        # Try to get existing wallet
        stmt = select(Wallet).where(and_(
            Wallet.user_id == user_id,
            Wallet.currency == currency
        ))
        wallet = session.execute(stmt).scalar_one_or_none()
        
        if not wallet:
            # Create new wallet
            wallet = Wallet(
                user_id=user_id,
                currency=currency,
                available_balance=Decimal('0'),
                frozen_balance=Decimal('0'),
                locked_balance=Decimal('0')
            )
            session.add(wallet)
            session.flush()  # Get the ID
            logger.info(f"🆕 WALLET_CREATED: user_id={user_id}, currency={currency}")
        
        return wallet
    
    def _create_transaction_record(self, session: Session, deposit: CryptoDeposit, wallet: Wallet) -> Transaction:
        """Create transaction record for audit trail"""
        transaction_id = f"DEPOSIT-{deposit.provider.upper()}-{deposit.txid[:16]}-{int(datetime.utcnow().timestamp())}"
        
        transaction = Transaction(
            transaction_id=transaction_id,
            user_id=deposit.user_id,
            transaction_type="wallet_deposit",
            amount=deposit.amount_fiat or Decimal('0'),
            currency="USD",
            status="completed",
            description=f"Crypto deposit: {deposit.amount} {deposit.coin.upper()} ({deposit.txid[:16]}...)",
            tx_hash=deposit.txid,
            from_address=deposit.address_in,
            to_address=deposit.address_out,
            blockchain_address=deposit.address_in
        )
        
        session.add(transaction)
        return transaction
    
    def _update_wallet_balance(self, session: Session, wallet: Wallet, amount: Decimal):
        """Update wallet balance with version check"""
        # Atomic balance update with optimistic locking
        old_balance = wallet.available_balance
        wallet.available_balance += amount
        wallet.last_transaction = datetime.utcnow()
        wallet.version += 1  # Increment version for optimistic locking
        
        logger.info(f"💰 BALANCE_UPDATE: wallet_id={wallet.id}, old=${old_balance}, new=${wallet.available_balance}, added=${amount}")
    
    def reconcile_pending_deposits(self, limit: int = 50) -> Dict[str, Any]:
        """
        Reconcile deposits stuck in READY_TO_CREDIT status
        
        This is useful for recovering from system outages or processing
        deposits that were marked ready but not yet credited.
        """
        processed = 0
        successful = 0
        failed = 0
        
        try:
            with get_sync_db_session() as session:
                # Find deposits ready for crediting
                stmt = (
                    select(CryptoDeposit)
                    .where(CryptoDeposit.status == CryptoDepositStatus.READY_TO_CREDIT.value)
                    .limit(limit)
                )
                
                deposits = list(session.execute(stmt).scalars())
                
                for deposit in deposits:
                    try:
                        result = self.process_deposit(deposit.txid, deposit.provider)
                        processed += 1
                        
                        if result["success"]:
                            successful += 1
                        else:
                            failed += 1
                            
                    except Exception as e:
                        logger.error(f"❌ RECONCILE_ERROR: txid={deposit.txid}, error={e}")
                        failed += 1
                        
        except Exception as e:
            logger.error(f"🚨 RECONCILE_CRITICAL_ERROR: {e}")
            
        logger.info(f"🔄 RECONCILE_COMPLETE: processed={processed}, successful={successful}, failed={failed}")
        
        return {
            "processed": processed,
            "successful": successful,
            "failed": failed,
            "success_rate": successful / processed if processed > 0 else 0
        }
    
    def _queue_deposit_notification(self, deposit: CryptoDeposit) -> bool:
        """
        Queue deposit notification for background processing (synchronous version)
        
        This version queues the notification without async/await to work in sync context
        """
        try:
            # Import here to avoid circular imports
            from database import SessionLocal
            from models import NotificationQueue
            
            # Create notification content
            amount_display = f"${deposit.amount_fiat:.2f}" if deposit.amount_fiat else f"{deposit.amount} {deposit.coin.upper()}"
            
            message = (
                f"💰 Wallet Credit Confirmed!\n\n"
                f"✅ Amount: {amount_display}\n"
                f"📄 Transaction: {deposit.txid[:8]}...{deposit.txid[-4:]}\n"
                f"💎 Currency: {deposit.coin.upper()}\n\n"
                f"Your wallet has been successfully credited. Use /wallet to view your balance."
            )
            
            # Create notification queue record directly
            with SessionLocal() as session:
                notification_id = f"deposit_{deposit.id}_{int(datetime.utcnow().timestamp())}"
                
                queue_record = NotificationQueue(
                    notification_id=notification_id,
                    user_id=deposit.user_id,
                    notification_type="payments",
                    priority=3,  # High priority for deposit confirmations (1=low, 2=normal, 3=high, 4=urgent)
                    channel="telegram",  # Primary channel
                    delivery_method="push",  # Required field for notification queue
                    subject=f"Deposit Confirmed: {amount_display}",
                    content=message,
                    template_data={
                        'amount_crypto': str(deposit.amount),
                        'amount_fiat': str(deposit.amount_fiat) if deposit.amount_fiat else None,
                        'coin': deposit.coin.upper(),
                        'txid': deposit.txid,
                        'provider': deposit.provider
                    },
                    status="pending",
                    created_at=datetime.utcnow()
                )
                
                session.add(queue_record)
                session.commit()
                
                logger.info(f"✅ NOTIFICATION_QUEUED: {notification_id} added to queue for user {deposit.user_id}")
                return True
                
        except Exception as e:
            logger.error(f"❌ NOTIFICATION_QUEUE_ERROR: user={deposit.user_id}, error={e}")
            return False

    async def _send_deposit_notification(self, deposit: CryptoDeposit) -> bool:
        """
        Send user notification about successful deposit credit
        
        Uses the ConsolidatedNotificationService for reliable delivery
        """
        try:
            # Create user-friendly message
            amount_display = f"${deposit.amount_fiat:.2f}" if deposit.amount_fiat else f"{deposit.amount} {deposit.coin.upper()}"
            
            message = (
                f"💰 Wallet Credit Confirmed!\n\n"
                f"✅ Amount: {amount_display}\n"
                f"📄 Transaction: {deposit.txid[:8]}...{deposit.txid[-4:]}\n"
                f"💎 Currency: {deposit.coin.upper()}\n\n"
                f"Your wallet has been successfully credited. Use /wallet to view your balance."
            )
            
            title = f"Deposit Confirmed: {amount_display}"
            
            # Create notification request
            notification_request = NotificationRequest(
                user_id=deposit.user_id,
                category=NotificationCategory.PAYMENTS,
                priority=NotificationPriority.HIGH,
                title=title,
                message=message,
                template_data={
                    'amount_crypto': str(deposit.amount),
                    'amount_fiat': str(deposit.amount_fiat) if deposit.amount_fiat else None,
                    'coin': deposit.coin.upper(),
                    'txid': deposit.txid,
                    'provider': deposit.provider
                },
                channels=[NotificationChannel.TELEGRAM, NotificationChannel.EMAIL],
                require_delivery=True,
                broadcast_mode=False,
                idempotency_key=f"deposit_{deposit.user_id}_{deposit.txid}_credited"
            )
            
            # Send notification
            notification_service = ConsolidatedNotificationService()
            await notification_service.initialize()
            
            delivery_results = await notification_service.send_notification(notification_request)
            
            # Check if at least one channel succeeded
            successful_channels = [
                channel for channel, result in delivery_results.items()
                if hasattr(result, 'status') and result.status.value in ['sent', 'delivered']
            ]
            
            if successful_channels:
                logger.info(f"✅ DEPOSIT_NOTIFICATION_SENT: user={deposit.user_id}, channels={successful_channels}")
                return True
            else:
                logger.warning(f"⚠️ DEPOSIT_NOTIFICATION_FAILED: user={deposit.user_id}, no successful channels")
                return False
                
        except Exception as e:
            logger.error(f"❌ DEPOSIT_NOTIFICATION_ERROR: user={deposit.user_id}, error={e}")
            return False


# Global instance for easy access
crypto_deposit_processor = CryptoDepositProcessor()