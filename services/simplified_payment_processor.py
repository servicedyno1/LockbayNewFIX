"""
Simplified Payment Processor - Architect-Approved Design

Direct flow: Provider Confirmation → Wallet Credit → Immediate Notification
No background jobs, no complex queues, no over-engineering.

Handles: BlockBee, DynoPay, Fincra payment confirmations
"""

import logging
from decimal import Decimal
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from sqlalchemy import select, and_
from sqlalchemy.exc import IntegrityError

from models import Wallet, CryptoDeposit, Transaction, CryptoDepositStatus
from services.fastforex_service import fastforex_service
from database import get_sync_db_session

logger = logging.getLogger(__name__)


class SimplifiedPaymentProcessor:
    """
    Simplified payment processor that treats provider confirmation as authoritative.
    
    Philosophy: If BlockBee/DynoPay/Fincra says it's confirmed, credit the wallet immediately.
    No complex state machines, no background dependencies, no over-engineering.
    """
    
    def __init__(self):
        self.logger = logger

    def process_payment(
        self, 
        provider: str,
        txid: str,
        user_id: int,
        amount: Decimal,
        currency: str,
        confirmed: bool,
        order_id: str,
        raw_data: Dict[str, Any],
        payment_type: str = "crypto"  # "crypto" or "fiat"
    ) -> Dict[str, Any]:
        """
        Process payment with immediate wallet credit if confirmed.
        
        Args:
            provider: 'blockbee', 'dynopay', or 'fincra'
            txid: Transaction ID
            user_id: Database user ID
            amount: Payment amount
            currency: Currency code (BTC, ETH, NGN, USD, etc.)
            confirmed: Provider's confirmation status
            order_id: Order/reference ID
            raw_data: Original webhook data
            payment_type: "crypto" or "fiat"
            
        Returns:
            Result dict with success status and details
        """
        try:
            with get_sync_db_session() as session:
                # CONCURRENCY FIX: Use SELECT FOR UPDATE for atomic idempotency check
                existing = session.execute(
                    select(CryptoDeposit).where(
                        and_(
                            CryptoDeposit.txid == txid,
                            CryptoDeposit.provider == provider
                        )
                    ).with_for_update()
                ).scalar_one_or_none()
                
                if existing is not None and str(existing.status) == CryptoDepositStatus.CREDITED.value:
                    self.logger.info(f"✅ ALREADY_PROCESSED: {txid} already credited")
                    return {
                        "success": True,
                        "reason": "already_processed",
                        "message": "Payment already processed"
                    }
                
                # If confirmed by provider, credit wallet immediately
                if confirmed:
                    return self._credit_wallet_immediate(
                        session, provider, txid, user_id, amount, currency, order_id, raw_data, payment_type
                    )
                else:
                    # Just record unconfirmed deposit
                    return self._record_unconfirmed(
                        session, provider, txid, user_id, amount, currency, order_id, raw_data, payment_type
                    )
                    
        except Exception as e:
            self.logger.error(f"❌ PAYMENT_PROCESS_ERROR: {txid} - {e}")
            return {
                "success": False,
                "reason": "processing_error",
                "message": f"Failed to process payment: {e}"
            }

    def _credit_wallet_immediate(
        self,
        session: Session,
        provider: str,
        txid: str,
        user_id: int,
        amount: Decimal,
        currency: str,
        order_id: str,
        raw_data: Dict[str, Any],
        payment_type: str = "crypto"
    ) -> Dict[str, Any]:
        """Credit wallet immediately when provider confirms payment."""
        
        # CRITICAL FIX: Use provider-supplied fiat value when available
        # This prevents rate discrepancy losses from re-converting crypto→USD via FastForex
        provider_usd_amount = self._extract_provider_usd_amount(provider, raw_data, amount, currency)
        
        if provider_usd_amount is not None:
            usd_amount = provider_usd_amount
            self.logger.info(f"💱 PROVIDER_USD: Using {provider} authoritative USD value: ${usd_amount:.2f} (crypto: {amount} {currency})")
        else:
            # Fallback: Get USD amount via FastForex (handle both crypto and fiat)
            usd_amount = self._get_usd_amount(amount, currency, payment_type)
        
        # MINIMUM DEPOSIT ENFORCEMENT: Reject deposits below $10 USD
        MIN_WALLET_DEPOSIT_USD = Decimal("10.0")
        if usd_amount < MIN_WALLET_DEPOSIT_USD:
            self.logger.warning(
                f"⚠️ BELOW_MINIMUM: {txid} - ${usd_amount} is below minimum ${MIN_WALLET_DEPOSIT_USD} USD. "
                f"Payment NOT credited for user {user_id}."
            )
            return {
                "success": False,
                "reason": "below_minimum",
                "message": f"Payment of ${usd_amount} is below the minimum deposit of ${MIN_WALLET_DEPOSIT_USD} USD",
                "amount": float(usd_amount)
            }
        
        try:
            # Update or create deposit record
            deposit = session.execute(
                select(CryptoDeposit).where(
                    and_(
                        CryptoDeposit.txid == txid,
                        CryptoDeposit.provider == provider
                    )
                )
            ).scalar_one_or_none()
            
            if not deposit:
                # Extract address information from webhook data
                address_in = (
                    raw_data.get('address_in')
                    or raw_data.get('address')
                    or raw_data.get('transaction_reference')
                    or f"dynopay-{txid}"
                )
                address_out = raw_data.get('address_out') or raw_data.get('forwarding_address') or ""
                
                deposit = CryptoDeposit(
                    provider=provider,
                    txid=txid,
                    order_id=order_id,
                    address_in=address_in,
                    address_out=address_out,
                    user_id=user_id,
                    coin=currency.upper(),
                    amount=amount,
                    amount_fiat=usd_amount,
                    status=CryptoDepositStatus.CREDITED.value,
                    confirmations=1,
                    credited_at=datetime.now(timezone.utc)
                )
                session.add(deposit)
            else:
                # Update deposit status (use credited_at instead of last_updated_at)
                deposit.amount_fiat = usd_amount  # type: ignore[assignment]
                deposit.status = CryptoDepositStatus.CREDITED.value  # type: ignore[assignment]
                deposit.coin = currency.upper()  # type: ignore[assignment]
                deposit.credited_at = datetime.now(timezone.utc)  # type: ignore[assignment]
            
            session.flush()
            
            # Credit wallet
            wallet = session.execute(
                select(Wallet).where(
                    and_(
                        Wallet.user_id == user_id,
                        Wallet.currency == "USD"
                    )
                )
            ).scalar_one_or_none()
            
            if not wallet:
                # Create USD wallet if doesn't exist
                wallet = Wallet(
                    user_id=user_id,
                    currency="USD",
                    available_balance=usd_amount,
                    updated_at=datetime.now(timezone.utc)
                )
                session.add(wallet)
            else:
                old_balance = wallet.available_balance
                wallet.available_balance += usd_amount  # type: ignore[assignment]
                wallet.updated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
                
                self.logger.info(f"💰 WALLET_CREDIT: user={user_id}, old=${old_balance}, new=${wallet.available_balance}, added=${usd_amount}")
            
            session.flush()
            
            # Create transaction record (created_at auto-generated by default)
            transaction = Transaction(
                user_id=user_id,
                transaction_type="wallet_deposit",
                amount=usd_amount,
                currency="USD",
                status="completed",
                transaction_id=f"DEP-{txid[:20]}-{user_id}"[:36],
                description=f"Payment: {amount} {currency.upper()} from {provider}",
                blockchain_tx_hash=txid,
                provider=provider,
            )
            session.add(transaction)
            session.flush()
            
            # Commit all changes (CRITICAL SECTION)
            session.commit()
            
            self.logger.info(f"✅ PAYMENT_CREDITED: {txid} - ${usd_amount} from {amount} {currency}")
            
            # Send immediate notifications (ARCHITECT REQUIREMENT)
            try:
                self._send_immediate_notification(user_id, amount, currency, usd_amount, txid)
                self.logger.info(f"✅ NOTIFICATIONS_SENT: {txid} - immediate notifications triggered")
            except Exception as e:
                self.logger.error(f"❌ NOTIFICATION_ERROR: {txid} - {e}")
                # Don't fail the payment because of notification issues
            
            return {
                "success": True,
                "reason": "credited",
                "message": f"Credited ${usd_amount} to wallet",
                "transaction_id": transaction.transaction_id,
                "wallet_id": wallet.id if hasattr(wallet, 'id') else None,
                "amount": float(usd_amount)
            }
            
        except IntegrityError as e:
            session.rollback()
            self.logger.warning(f"🔄 CONCURRENCY_DETECTED: {txid} - checking if already processed: {e}")
            
            # Re-fetch to see if another process already credited this
            existing = session.execute(
                select(CryptoDeposit).where(
                    and_(
                        CryptoDeposit.txid == txid,
                        CryptoDeposit.provider == provider
                    )
                )
            ).scalar_one_or_none()
            
            if existing is not None and str(existing.status) == CryptoDepositStatus.CREDITED.value:
                self.logger.info(f"✅ ALREADY_CREDITED: {txid} was processed by concurrent webhook")
                return {
                    "success": True,
                    "reason": "already_credited",
                    "message": "Payment already processed by concurrent request"
                }
            else:
                # Unexpected integrity error - re-raise
                self.logger.error(f"❌ UNEXPECTED_INTEGRITY_ERROR: {txid} - {e}")
                raise e

    def _record_unconfirmed(
        self,
        session: Session,
        provider: str,
        txid: str,
        user_id: int,
        amount: Decimal,
        currency: str,
        order_id: str,
        raw_data: Dict[str, Any],
        payment_type: str = "crypto"
    ) -> Dict[str, Any]:
        """Record unconfirmed deposit without crediting wallet."""
        
        # Extract address information from webhook data
        address_in = raw_data.get('address_in') or raw_data.get('address')
        address_out = raw_data.get('address_out') or raw_data.get('forwarding_address')
        
        deposit = CryptoDeposit(
            provider=provider,
            txid=txid,
            order_id=order_id,
            address_in=address_in,
            address_out=address_out,
            user_id=user_id,
            coin=currency.upper(),
            amount=amount,
            status=CryptoDepositStatus.PENDING_UNCONFIRMED.value,
            confirmations=0
        )
        session.add(deposit)
        session.commit()
        
        self.logger.info(f"📋 UNCONFIRMED_RECORDED: {txid} - waiting for confirmation")
        
        # Send immediate pending notification (ARCHITECT REQUIREMENT)
        try:
            usd_amount = self._get_usd_amount(amount, currency, payment_type)
            self._send_pending_notification(user_id, amount, currency, usd_amount, txid)
            self.logger.info(f"✅ PENDING_NOTIFICATION_SENT: {txid} - user notified payment detected")
        except Exception as e:
            self.logger.error(f"❌ PENDING_NOTIFICATION_ERROR: {txid} - {e}")
            # Don't fail the deposit recording because of notification issues
        
        return {
            "success": True,
            "reason": "recorded_unconfirmed",
            "message": "Deposit recorded, waiting for confirmation"
        }

    def _extract_provider_usd_amount(self, provider: str, raw_data: Dict[str, Any], amount: Decimal, currency: str) -> Optional[Decimal]:
        """
        Extract provider-supplied USD amount from webhook data.
        
        Providers like BlockBee and DynoPay include authoritative fiat values
        in their callbacks. Using these prevents rate discrepancy losses that 
        occur when re-converting crypto→USD via a different rate source (FastForex).
        """
        try:
            if provider == "blockbee":
                # BlockBee provides 'price' (USD per coin) in callback data
                price = raw_data.get("price")
                if price:
                    usd_value = amount * Decimal(str(price))
                    self.logger.info(f"💱 BLOCKBEE_USD: {amount} {currency} * ${price}/coin = ${usd_value:.2f}")
                    return usd_value
                    
            elif provider == "dynopay":
                # DynoPay provides 'base_amount' (authoritative USD value)
                base_amount = raw_data.get("base_amount")
                base_currency = raw_data.get("base_currency", "")
                if base_amount and base_currency == "USD":
                    usd_value = Decimal(str(base_amount))
                    self.logger.info(f"💱 DYNOPAY_USD: base_amount=${usd_value:.2f}")
                    return usd_value
                    
        except (ValueError, TypeError, ArithmeticError) as e:
            self.logger.warning(f"⚠️ PROVIDER_USD_EXTRACT_ERROR: {provider} - {e}, falling back to FastForex")
        
        return None

    def _get_usd_amount(self, amount: Decimal, currency: str, payment_type: str = "crypto") -> Decimal:
        """Get USD amount using FastForex for crypto or direct conversion for fiat."""
        try:
            if currency.upper() == "USD":
                return amount
            
            # Handle fiat currencies (like NGN)
            if payment_type == "fiat":
                if currency.upper() == "NGN":
                    # Convert NGN to USD using FastForex
                    rate = fastforex_service.get_ngn_to_usd_rate()
                    usd_amount = amount * Decimal(str(rate))
                    self.logger.info(f"💱 NGN_CONVERSION: {amount} NGN = ${usd_amount} (rate: {rate})")
                    return usd_amount
                else:
                    # For other fiat currencies, implement as needed
                    self.logger.warning(f"⚠️ UNSUPPORTED_FIAT: {currency} not supported, using 1:1 rate")
                    return amount
            
            # Get crypto rate from FastForex
            rate = fastforex_service.get_crypto_rate(currency.upper())
            usd_amount = amount * Decimal(str(rate))
            
            self.logger.info(f"💱 CONVERSION: {amount} {currency} = ${usd_amount} (rate: ${rate})")
            return usd_amount
            
        except Exception as e:
            self.logger.error(f"❌ RATE_ERROR: Failed to get {currency} rate: {e}")
            raise Exception(f"Failed to get exchange rate for {currency}")

    def _send_immediate_notification(
        self,
        user_id: int,
        amount: Decimal,
        currency: str,
        usd_amount: Decimal,
        txid: str
    ):
        """Send immediate notifications to user via unified notification service."""
        try:
            # Use the new unified notification service
            from services.unified_notification_service import notify_user, NotificationType
            
            message = (
                f"💰 Wallet Credit Confirmed!\n\n"
                f"✅ Amount: ${usd_amount:.2f}\n"
                f"📄 Transaction: {txid[:8]}...{txid[-4:]}\n"
                f"💎 Currency: {currency.upper()}\n\n"
                f"Your wallet has been successfully credited. Use /wallet to view your balance."
            )
            
            subject = f"Deposit Confirmed: ${usd_amount:.2f}"
            
            # Send via unified notification service (all channels)
            result = notify_user(
                user_id=user_id,
                message=message,
                subject=subject,
                notification_type=NotificationType.ALL
            )
            
            if result.success:
                self.logger.info(f"✅ UNIFIED_NOTIFICATIONS_SENT: user={user_id}, channels={result.channels_sent}")
            else:
                self.logger.error(f"❌ UNIFIED_NOTIFICATIONS_FAILED: user={user_id}, errors={result.errors}")
                
        except Exception as e:
            self.logger.error(f"❌ NOTIFICATION_ERROR: user={user_id}, error={e}")
            # Don't fail the payment because of notification issues
    
    def _send_pending_notification(self, user_id: int, amount: Decimal, currency: str, usd_amount: Decimal, txid: str):
        """Send immediate notification that payment was detected and is pending confirmation"""
        try:
            from services.unified_notification_service import unified_notification_service
            
            # Create user-friendly pending message
            message = f"""
🟡 <b>Payment Detected!</b>

💰 <b>Amount:</b> {amount} {currency.upper()} (${usd_amount:.2f} USD)
🔍 <b>Transaction:</b> {txid[:16]}...
⏳ <b>Status:</b> Waiting for blockchain confirmation

Your payment has been detected and will be credited to your wallet after 1 blockchain confirmation (usually 2-10 minutes).

We'll notify you again once it's confirmed and credited! 🚀
            """.strip()

            # Send notification via all channels
            result = unified_notification_service.notify_user(
                user_id=user_id,
                message=message,
                subject="Payment Detected - Pending Confirmation"
            )
            
            if result.success:
                self.logger.info(f"✅ PENDING_NOTIFICATION_SENT: user={user_id}, txid={txid[:16]}...")
            else:
                self.logger.warning(f"⚠️ PENDING_NOTIFICATION_PARTIAL: user={user_id}, channels={result.channels_sent}, errors={result.errors}")
                
        except Exception as e:
            self.logger.error(f"❌ PENDING_NOTIFICATION_ERROR: user={user_id}, txid={txid[:16]}..., error={e}")
            # Don't fail the payment recording because of notification issues


# Global instance
simplified_payment_processor = SimplifiedPaymentProcessor()