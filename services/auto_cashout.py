"""Safe Auto-cashout service for escrow completions with explicit destination selection"""

import logging
from typing import Dict, Any, Optional
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from database import SyncSessionLocal, AsyncSessionLocal  # FIXED: Move to top-level import to prevent local import errors
from models import (
    User, Escrow, Cashout, Transaction, TransactionType, SavedBankAccount, 
    CashoutStatus, WalletHolds, WalletHoldStatus, Wallet, UnifiedTransaction, 
    UnifiedTransactionStatus, UnifiedTransactionType, UnifiedTransactionPriority,
    UnifiedTransactionRetryLog, CashoutErrorCode, CashoutType
)
from services.crypto import CryptoServiceAtomic
from services.minimal_classifier import MinimalClassifier
from services.admin_trade_notifications import admin_trade_notifications
from config import Config
from utils.universal_id_generator import UniversalIDGenerator
from utils.unified_transaction_state_validator import UnifiedTransactionStateValidator
from utils.cashout_state_validator import CashoutStateValidator
from datetime import datetime, timedelta
import asyncio
# Import ORM typing helpers for Column[Type] vs Type compatibility
from utils.orm_typing_helpers import as_int, as_str, as_decimal, as_bool

logger = logging.getLogger(__name__)


class AutoCashoutService:
    """Service for handling automatic cashouts with explicit destination selection"""

    @classmethod
    async def _create_unified_retry_log_entry(
        cls, 
        unified_transaction_id: str, 
        error_code: str, 
        error_message: str, 
        external_provider: str, 
        session: AsyncSession
    ) -> bool:
        """
        Streamlined retry log creation using MinimalClassifier.
        
        Only creates retry logs for technical transient errors that pass MinimalClassifier.
        All other errors are routed to admin review without automatic retry.
        
        Args:
            unified_transaction_id: The unified transaction ID to link to
            error_code: The error code (original provider-specific code)
            error_message: The actual error message from the API
            external_provider: The provider (fincra, kraken)
            session: Database session
            
        Returns:
            bool: True if retry log created (technical error) or False (admin review required)
        """
        try:
            from datetime import timedelta
            
            # Use MinimalClassifier to determine if error should be retried
            error_input = {
                'error_code': error_code,
                'error_message': error_message,
                'external_provider': external_provider
            }
            
            is_retryable = MinimalClassifier.is_retryable_technical(error_input)
            
            # Log classification decision
            MinimalClassifier.log_classification(
                error_input,
                context={
                    'unified_transaction_id': unified_transaction_id,
                    'external_provider': external_provider,
                    'source': 'auto_cashout_service'
                }
            )
            
            if not is_retryable:
                logger.info(
                    f"👨‍💼 AUTO_CASHOUT_ADMIN_REVIEW: Error requires human review - {error_code}: {error_message} "
                    f"(transaction: {unified_transaction_id})"
                )
                return False  # Route to admin review, no retry
            
            # Query ALL existing retry logs to count total retry attempts
            all_retries_stmt = select(UnifiedTransactionRetryLog).where(
                UnifiedTransactionRetryLog.transaction_id == unified_transaction_id
            )
            all_retries_result = await session.execute(all_retries_stmt)
            all_retries = all_retries_result.scalars().all()
            retry_count = len(all_retries)
            
            # Check if retry exhausted (max 3 attempts)
            if retry_count >= 3:
                logger.error(
                    f"❌ RETRY_EXHAUSTED: Transaction {unified_transaction_id} failed after {retry_count} attempts "
                    f"(provider: {external_provider}, error: {error_code})"
                )
                
                # Mark UnifiedTransaction as FAILED
                try:
                    unified_tx_stmt = select(UnifiedTransaction).where(
                        UnifiedTransaction.external_id == unified_transaction_id
                    )
                    unified_tx_result = await session.execute(unified_tx_stmt)
                    unified_tx = unified_tx_result.scalar_one_or_none()
                    
                    if unified_tx:
                        unified_tx.status = UnifiedTransactionStatus.FAILED.value  # type: ignore[assignment]
                        unified_tx.error_message = f"Retry exhausted after {retry_count} attempts: {error_message}"  # type: ignore[attr-defined]
                        await session.flush()
                        logger.info(f"✅ TRANSACTION_FAILED: Marked {unified_transaction_id} as FAILED")
                    else:
                        logger.warning(f"⚠️ UnifiedTransaction not found for {unified_transaction_id}")
                        
                except Exception as tx_error:
                    logger.error(f"❌ Failed to mark transaction as FAILED: {tx_error}")
                
                # Send admin alert for manual intervention
                try:
                    from services.admin_funding_notifications import send_retry_exhausted_alert
                    
                    admin_context = {
                        'transaction_id': unified_transaction_id,
                        'retry_count': retry_count,
                        'error_code': error_code,
                        'error_message': error_message,
                        'external_provider': external_provider,
                        'requires_manual_intervention': True,
                        'retry_history': [
                            {
                                'attempt': r.retry_attempt,
                                'error': r.error_message,
                                'timestamp': r.attempted_at.isoformat() if r.attempted_at else None  # type: ignore[arg-type]
                            } for r in all_retries
                        ]
                    }
                    
                    # Actually send the admin alert (not just log it!)
                    await send_retry_exhausted_alert(
                        transaction_id=unified_transaction_id,
                        retry_count=retry_count,
                        error_code=error_code,
                        error_message=error_message,
                        provider=external_provider,
                        context=admin_context
                    )
                    
                    logger.info(
                        f"✅ ADMIN_ALERT_SENT: Retry exhaustion notification sent for {unified_transaction_id} "
                        f"({retry_count} attempts, provider: {external_provider})"
                    )
                    
                except Exception as alert_error:
                    logger.error(f"❌ Failed to send admin alert for exhausted retries {unified_transaction_id}: {alert_error}")
                
                return False  # No more retries, requires admin intervention
            
            # Check for existing pending retry log to prevent duplicates
            pending_retry_stmt = select(UnifiedTransactionRetryLog).where(
                UnifiedTransactionRetryLog.transaction_id == unified_transaction_id,
                UnifiedTransactionRetryLog.retry_successful.is_(None)  # Pending retries only
            )
            pending_result = await session.execute(pending_retry_stmt)
            existing_pending_retry = pending_result.scalar_one_or_none()
            
            if existing_pending_retry:
                logger.info(
                    f"⚠️ RETRY_IDEMPOTENCY: Retry already queued for {unified_transaction_id} "
                    f"(retry_log_id={existing_pending_retry.id}, attempt={existing_pending_retry.retry_attempt})"
                )
                return True  # Don't create duplicate, but return success
            
            # Calculate exponential backoff: 10min (1st), 20min (2nd), 40min (3rd)
            retry_attempt = retry_count + 1
            delay_minutes = 10 * (2 ** (retry_attempt - 1))  # 10, 20, 40
            delay_seconds = delay_minutes * 60  # 600, 1200, 2400
            final_retry = (retry_attempt >= 3)  # True only on 3rd attempt
            
            # Get streamlined retry configuration (for logging/metadata only)
            streamlined_enabled = getattr(Config, 'STREAMLINED_FAILURE_HANDLING', True)
            
            # Map original error to standardized code for retry system
            if 'timeout' in error_message.lower() or 'timeout' in error_code.lower():
                standardized_error_code = CashoutErrorCode.API_TIMEOUT.value
            elif 'network' in error_message.lower() or 'connection' in error_message.lower():
                standardized_error_code = CashoutErrorCode.NETWORK_ERROR.value
            elif '502' in error_message or '503' in error_message or '504' in error_message:
                standardized_error_code = CashoutErrorCode.SERVICE_UNAVAILABLE.value
            elif '429' in error_message or 'rate limit' in error_message.lower():
                standardized_error_code = CashoutErrorCode.RATE_LIMIT_EXCEEDED.value
            else:
                standardized_error_code = CashoutErrorCode.API_TIMEOUT.value  # Fallback for retryable technical errors
            
            # Create the retry log entry with calculated exponential backoff
            retry_log = UnifiedTransactionRetryLog(
                transaction_id=unified_transaction_id,
                retry_attempt=retry_attempt,  # Properly incremented: 1, 2, or 3
                retry_reason=f"Technical transient error (attempt {retry_attempt}/3)",
                error_code=standardized_error_code,
                error_message=error_message,
                error_details={
                    "provider": external_provider,
                    "error_type": "technical_transient",
                    "retry_trigger": "auto_cashout_service_exponential_backoff",
                    "original_error_code": error_code,
                    "original_error_message": error_message,
                    "classification_system": "minimal_classifier_v1",
                    "streamlined_failure_handling": streamlined_enabled,
                    "retry_attempt": retry_attempt,
                    "total_retry_count": retry_count,
                    "exponential_backoff_minutes": delay_minutes
                },
                retry_strategy="exponential",  # Exponential backoff strategy
                delay_seconds=delay_seconds,  # Calculated: 600, 1200, or 2400
                next_retry_at=datetime.utcnow() + timedelta(seconds=delay_seconds),
                external_provider=external_provider,
                retry_successful=None,  # Pending
                final_retry=final_retry,  # True only on 3rd attempt
                attempted_at=datetime.utcnow()
            )
            
            session.add(retry_log)
            await session.flush()  # Get the ID without committing
            
            logger.info(
                f"✅ EXPONENTIAL_RETRY_CREATED: Created retry log {retry_log.id} "
                f"for {unified_transaction_id} (attempt {retry_attempt}/3, {external_provider}, "
                f"retry in {delay_minutes} minutes, final={final_retry})"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to create streamlined retry log for {unified_transaction_id}: {e}")
            return False  # On error, don't retry - route to admin

    @classmethod
    def _create_unified_transaction_for_cashout(
        cls, cashout: Cashout, user: User, session: Session
    ) -> str:
        """
        Create a UnifiedTransaction record for wallet cashout to enable retry processing
        
        Args:
            cashout: The cashout record
            user: The user making the cashout
            session: Database session
            
        Returns:
            str: The unified transaction ID that was created
        """
        try:
            # Generate unified transaction ID
            transaction_id = UniversalIDGenerator.generate_transaction_id()
            
            # Determine destination based on cashout type
            destination_address = None
            destination_bank_account = None
            
            # Use as_str to safely extract cashout_type from Column[str]
            cashout_type = as_str(cashout.cashout_type) or CashoutType.CRYPTO.value
            
            if cashout_type == CashoutType.CRYPTO.value:
                # Extract crypto address from destination field
                destination_address = as_str(cashout.destination) or "pending_address_config"
            elif cashout_type == CashoutType.NGN_BANK.value:
                # Extract bank account info from destination field or use bank_account_id
                bank_account_id = as_int(cashout.bank_account_id)
                if bank_account_id:
                    # Try to get bank account details from saved_bank_accounts
                    bank_account = session.query(SavedBankAccount).filter_by(
                        id=bank_account_id
                    ).first()
                    if bank_account:
                        destination_bank_account = f"{bank_account.bank_name}:{bank_account.account_number}"
                    else:
                        destination_bank_account = f"bank_id:{bank_account_id}"
                else:
                    # Fallback to destination field or placeholder
                    destination_bank_account = as_str(cashout.destination) or "pending_bank_config"
            
            # Create formatted description with emoji for transaction history display
            cashout_id = as_str(cashout.cashout_id)
            amount = as_decimal(cashout.amount)
            currency_str = as_str(cashout.currency) or "USD"
            
            # Format description based on cashout type with emojis
            if cashout_type == CashoutType.CRYPTO.value:
                formatted_description = f"💸 Crypto Cashout • {cashout_id}"
            elif cashout_type == CashoutType.NGN_BANK.value:
                formatted_description = f"🏦 Bank Cashout • {cashout_id}"
            else:
                formatted_description = f"💸 Cashout • {cashout_id}"
            
            # Create unified transaction record
            unified_tx = UnifiedTransaction(
                user_id=as_int(user.id),
                transaction_type=UnifiedTransactionType.WALLET_CASHOUT.value,
                amount=amount,
                currency=currency_str,
                status=UnifiedTransactionStatus.PENDING.value,
                reference_id=cashout_id,  # Store cashout_id in reference_id
                external_id=transaction_id,  # Store generated transaction_id in external_id
                description=formatted_description,  # Human-readable description with emojis
                
                # Store all metadata in JSONB column (matches actual database schema)
                transaction_metadata={
                    "cashout_id": cashout_id,
                    "cashout_type": cashout_type,
                    "processing_provider": "fincra" if cashout_type == CashoutType.NGN_BANK.value else "kraken",
                    "auto_cashout": True,
                    "user_telegram_id": as_int(user.telegram_id),
                    "destination_address": destination_address,
                    "destination_bank_account": destination_bank_account,
                    "priority": UnifiedTransactionPriority.NORMAL.value,
                    "fund_movement_type": "debit",
                    "total_amount": float(cashout.amount),
                    "description": f"Auto-cashout: {cashout_type} for ${amount}"
                }
            )
            
            session.add(unified_tx)
            session.flush()  # Get the ID without committing
            
            logger.info(
                f"✅ UNIFIED_TX_CREATED: Created UnifiedTransaction {transaction_id} "
                f"for cashout {as_str(cashout.cashout_id)} (${as_decimal(cashout.amount)} {as_str(cashout.currency)})"
            )
            
            return transaction_id
            
        except Exception as e:
            logger.error(f"❌ Failed to create UnifiedTransaction for cashout {cashout.cashout_id}: {e}")
            # Don't fail the entire cashout process if unified transaction creation fails
            return None  # type: ignore[return-value]

    @classmethod
    async def _finalize_user_visible_success_and_notify_admin(
        cls,
        cashout: "Cashout",
        reason: str,
        unified_tx_id: str,
        session: Session,
        user_id: int,
        amount: float,
        currency: str,
        destination: str,
        error_details: dict = None  # type: ignore[assignment]
    ) -> Dict[str, Any]:
        """
        CRITICAL BUSINESS LOGIC: Ensure user always sees success for backend configuration issues.
        
        This function implements the "user success + admin notification" pattern:
        1. Mark cashout as SUCCESS for user experience
        2. Set completion timestamp and external reference
        3. Consume wallet holds to maintain ledger consistency
        4. Send admin notification for backend processing
        5. Add metadata for admin action queue
        6. Return success=True to user
        
        Args:
            cashout: The cashout record to finalize
            reason: Admin-facing reason for backend processing
            unified_tx_id: Unified transaction ID for retry tracking
            session: Database session
            user_id: User ID for notifications and hold processing
            amount: Cashout amount
            currency: Cashout currency
            destination: Destination address/account
            error_details: Optional technical details for admin
            
        Returns:
            Dict with success=True and admin notification status
        """
        from datetime import datetime
        from models import CashoutStatus
        
        try:
            # 1. Mark cashout as SUCCESS for user experience using proper ORM updates
            as_str(cashout.cashout_id)
            rows_updated = session.query(Cashout).filter(Cashout.id == cashout.id).update({
                Cashout.status: CashoutStatus.SUCCESS.value,
                Cashout.completed_at: datetime.utcnow(),
            })
            
            # Verify exactly 1 row was updated (prevents silent failures)
            if rows_updated != 1:
                error_msg = f"Failed to update cashout {cashout.cashout_id}: {rows_updated} rows affected (expected 1)"
                logger.error(f"❌ {error_msg}")
                raise ValueError(error_msg)
            
            # Note: external_tx_id and error_message may not exist as model attributes
            
            # 4. Add admin metadata for backend processing queue
            admin_metadata = {
                "backend_pending": True,
                "reason": reason,
                "requires_admin_action": True,
                "user_sees_success": True,
                "processing_queue": "admin_action_required",
                "currency": currency,  # Target crypto currency for retry logic (ETH/BTC/etc)
                "amount": str(amount),
                "destination": destination[:50],  # Truncate for safety
                "created_at": datetime.utcnow().isoformat()
            }
            
            if error_details:
                admin_metadata["technical_details"] = error_details
                
            if unified_tx_id:
                admin_metadata["unified_transaction_id"] = unified_tx_id
            
            # Store in admin_notes (searchable by admin tools)
            cashout.admin_notes = f"Backend processing required: {reason}\nMetadata: {admin_metadata}"
            
            # 5. Commit user-facing success
            session.commit()
            
            logger.info(
                f"✅ USER_SUCCESS_FINALIZED: Cashout {cashout.cashout_id} marked as SUCCESS "
                f"(${amount} {currency}) - backend processing queued for admin"
            )
            
            # 6. CRITICAL: Consume frozen hold for user-perceived successful cashout
            try:
                from utils.cashout_completion_handler import auto_release_completed_cashout_hold
                hold_result = await auto_release_completed_cashout_hold(
                    cashout_id=cashout.cashout_id,
                    user_id=user_id,
                    session=session
                )
                
                if hold_result.get("success") and hold_result.get("consumed"):
                    logger.info(
                        f"✅ HOLD_CONSUMED: Released ${hold_result['amount']:.2f} {hold_result.get('currency', 'USD')} "
                        f"hold for user-success cashout {cashout.cashout_id}"
                    )
                elif hold_result.get("skipped"):
                    logger.info(f"ℹ️ No hold to consume for cashout {cashout.cashout_id}")
                else:
                    logger.warning(f"⚠️ Failed to consume hold for {cashout.cashout_id}: {hold_result.get('error')}")
                    
            except Exception as hold_error:
                logger.error(f"❌ Hold consumption error for user-success cashout {cashout.cashout_id}: {hold_error}")
            
            # 7. Send admin notification for backend processing
            admin_notified = False
            try:
                from services.admin_funding_notifications import admin_funding_notifications
                from models import User
                
                admin_context = {
                    'cashout_id': cashout.cashout_id,
                    'user_id': user_id,
                    'amount': amount,
                    'currency': currency,
                    'destination': destination,
                    'reason': reason,
                    'processing_required': True,
                    'user_experience': 'success_shown',
                    'backend_status': 'configuration_required'
                }
                
                if error_details:
                    admin_context['technical_details'] = error_details
                
                # Fetch user object for complete user data
                user = session.query(User).filter_by(id=user_id).first()
                user_data = {
                    'id': user.id,
                    'telegram_id': user.telegram_id,
                    'username': user.username,
                    'first_name': user.first_name,
                    'email': user.email
                } if user else {}
                
                # Send appropriate admin email based on error type
                if reason in ["kraken_insufficient_funds", "fincra_insufficient_funds"]:
                    # Insufficient funds - send funding alert
                    service_name = "Kraken" if reason == "kraken_insufficient_funds" else "Fincra"
                    
                    # CRITICAL FIX: Get USD amount from cashout record, not from crypto amount
                    # cashout.amount is the original USD amount that was debited from user's wallet
                    usd_amount = float(cashout.amount) if cashout.amount else 0.0
                    
                    await admin_funding_notifications.send_funding_required_alert(
                        cashout_id=cashout.cashout_id,
                        service=service_name,
                        amount=usd_amount,
                        currency=currency,
                        user_data=user_data,
                        service_currency=currency,
                        service_amount=amount,
                        retry_info={
                            'error_code': error_details.get('error_code', 'INSUFFICIENT_FUNDS') if error_details else 'INSUFFICIENT_FUNDS',
                            'attempt_number': 1,
                            'max_attempts': 5,
                            'next_retry_seconds': 300,
                            'is_auto_retryable': True
                        }
                    )
                    logger.info(f"✅ ADMIN_NOTIFIED: {service_name} funding required alert sent for {cashout.cashout_id}")
                else:
                    # Address configuration or other backend issues
                    # CRITICAL FIX: Get USD amount from cashout record for address config alerts too
                    usd_amount_for_config = float(cashout.amount) if cashout.amount else 0.0
                    net_usd_amount = float(cashout.net_amount) if cashout.net_amount else None
                    
                    # Calculate crypto amount from net USD using current exchange rate
                    crypto_amount_to_send = None
                    if net_usd_amount and currency:
                        try:
                            from services.fastforex_service import FastForexService
                            forex_service = FastForexService()
                            crypto_rate = forex_service.get_crypto_rate(currency)
                            if crypto_rate and float(crypto_rate) > 0:
                                crypto_amount_to_send = net_usd_amount / float(crypto_rate)
                                logger.info(f"📊 Calculated crypto amount for admin email: {crypto_amount_to_send:.8f} {currency}")
                        except Exception as rate_error:
                            logger.warning(f"Could not calculate crypto amount for admin email: {rate_error}")
                    
                    await admin_funding_notifications.send_address_configuration_alert(
                        cashout_id=cashout.cashout_id,
                        currency=currency,
                        address=destination,
                        user_data=user_data,
                        amount=usd_amount_for_config,
                        error_details=error_details,  # type: ignore[arg-type]
                        crypto_amount=crypto_amount_to_send,
                        net_usd_amount=net_usd_amount
                    )
                    logger.info(f"✅ ADMIN_NOTIFIED: Backend configuration alert sent for {cashout.cashout_id}")
                admin_notified = True
                
            except Exception as notification_error:
                logger.error(f"❌ Failed to send admin notification for {cashout.cashout_id}: {notification_error}")
                # Don't fail user experience for notification issues
            
            # 8. Return SUCCESS to user (critical for user experience)
            return {
                "success": True,
                "status": "completed", 
                "cashout_id": cashout.cashout_id,
                "external_tx_id": cashout.external_tx_id,
                "message": f"💰 Cashout processed successfully! Your {currency} withdrawal is being finalized.",
                "admin_notified": admin_notified,
                "backend_pending": True,
                "processing_queue": "admin_action_required"
            }
            
        except Exception as e:
            logger.error(f"❌ Critical error in _finalize_user_visible_success_and_notify_admin for {cashout.cashout_id}: {e}")
            session.rollback()
            
            # Even on error, try to return success to user (last resort)
            return {
                "success": True,
                "status": "processing",
                "cashout_id": cashout.cashout_id,
                "message": f"💰 Cashout request received! We're processing your {currency} withdrawal.",
                "admin_notified": False,
                "backend_pending": True,
                "error": "Failed to complete finalization but user experience preserved"
            }

    @classmethod
    async def process_escrow_completion(
        cls, escrow: Escrow, session: Session
    ) -> Dict[str, Any]:
        """
        Process escrow completion with safe auto-cashout logic:
        - Only process if user has explicitly configured auto-cashout destination
        - Apply business rules for approval thresholds
        - Fallback to wallet credit if auto-cashout not configured
        """
        try:
            seller = session.query(User).filter(User.id == escrow.seller_id).first()
            if not seller:
                logger.error(f"Seller not found for escrow {escrow.escrow_id}")
                return {"success": False, "error": "Seller not found"}

            # Use Decimal for financial precision
            from decimal import Decimal
            amount = as_decimal(escrow.amount) or Decimal("0")
            seller_id = as_int(seller.id)

            # Check if user has auto-cashout enabled using proper Column boolean check
            auto_cashout_enabled = as_bool(seller.auto_cashout_enabled)
            if not auto_cashout_enabled:
                logger.info(
                    f"Auto-cashout disabled for user {seller_id}, crediting wallet only"
                )
                return await cls._credit_wallet_normally(escrow, seller, session)

            # User wants auto-cashout - create cashout regardless of admin processing mode
            logger.info(
                f"User {seller_id} has auto-cashout enabled, creating cashout request"
            )
            
            # Check if user has configured explicit auto-cashout destination
            # Note: cashout_preference may not exist as model attribute
            preference = as_str(getattr(seller, 'cashout_preference', None)) or "CRYPTO"

            if preference == "NGN_BANK":
                if not seller.auto_cashout_bank_account_id:
                    logger.info(
                        f"No auto-cashout bank account configured for user {seller_id}, crediting wallet"
                    )
                    return await cls._credit_wallet_normally(escrow, seller, session)
                
                # SECURITY ENHANCEMENT: Auto-cashout will create cashouts that still require OTP
                # Enhanced: Use default bank account functionality and respect active status
                bank_account = None
                
                # Try explicit auto-cashout bank account first  
                if seller.auto_cashout_bank_account_id:
                    bank_account = session.query(SavedBankAccount).filter_by(
                        id=seller.auto_cashout_bank_account_id, 
                        user_id=seller_id,
                        is_active=True  # Only active accounts
                    ).first()
                
                # Fallback to default bank account if explicit one not found/inactive
                if not bank_account:
                    bank_account = session.query(SavedBankAccount).filter(
                        SavedBankAccount.user_id == seller_id,
                        SavedBankAccount.is_active == True,
                        SavedBankAccount.is_default == True
                    ).first()
                
                # Fallback to most recent active bank account  
                if not bank_account:
                    bank_account = session.query(SavedBankAccount).filter(
                        SavedBankAccount.user_id == seller_id,
                        SavedBankAccount.is_active == True
                    ).order_by(
                        SavedBankAccount.last_used.desc().nullslast(),
                        SavedBankAccount.created_at.desc()
                    ).first()
                
                if not bank_account:
                    logger.warning(
                        f"No active bank account found for user {seller_id}, crediting wallet instead"
                    )
                    return await cls._credit_wallet_normally(escrow, seller, session)
                
                return await cls._process_ngn_auto_cashout(escrow, seller, float(amount), session)
                
            else:  # CRYPTO
                if not seller.auto_cashout_crypto_address:  # type: ignore[attr-defined]
                    logger.info(
                        f"No auto-cashout crypto address configured for user {seller_id}, crediting wallet"
                    )
                    return await cls._credit_wallet_normally(escrow, seller, session)
                
                return await cls._process_crypto_auto_cashout(escrow, seller, float(amount), session)

        except Exception as e:
            logger.error(f"Error in escrow completion processing: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    async def _handle_kraken_cashout(
        cls, 
        cashout: Cashout, 
        user: User, 
        amount: Decimal, 
        currency: str, 
        address: str, 
        session: Session
    ) -> Dict[str, Any]:
        """
        Handle Kraken cryptocurrency withdrawal with address verification and admin routing
        
        This function performs the following:
        1. Verify address is configured in Kraken wallet
        2. If configured: Process withdrawal automatically
        3. If not configured: Route to admin with user-friendly success message
        
        Args:
            cashout: The cashout record
            user: The user requesting the cashout
            amount: Withdrawal amount in crypto
            currency: Cryptocurrency (BTC, LTC, etc.)
            address: Destination crypto address
            session: Database session
            
        Returns:
            Dict with success status and processing details
        """
        try:
            from services.kraken_address_verification_service import KrakenAddressVerificationService
            
            # Step 1: Verify if address is configured and verified in Kraken
            verification_service = KrakenAddressVerificationService()
            verification_result = await verification_service.verify_withdrawal_address(
                crypto_currency=currency,
                withdrawal_address=address
            )
            
            if not verification_result.get('address_exists'):
                # Address not configured - route to admin for configuration
                logger.warning(
                    f"⚠️ ADDRESS_NOT_CONFIGURED: Routing cashout {cashout.cashout_id} to admin - "
                    f"{verification_result.get('routing_reason', 'address_configuration_required')}"
                )
                
                # Update cashout status for admin visibility
                # SECURITY: Validate state transition to prevent overwriting terminal states
                try:
                    current_status = CashoutStatus(cashout.status)
                    CashoutStateValidator.validate_transition(
                        current_status, 
                        CashoutStatus.ADMIN_PENDING, 
                        str(cashout.cashout_id)
                    )
                    cashout.status = "admin_pending"
                    cashout.admin_notes = f"Address configuration required: {address[:20]}... ({currency})"
                    cashout.error_message = None  # Clear error for user - don't show technical details
                    session.commit()
                except Exception as validation_error:
                    logger.error(
                        f"🚫 AUTO_ROUTE_BLOCKED: {current_status}→ADMIN_PENDING for {cashout.cashout_id}: {validation_error}"
                    )
                    # Graceful degradation: already in a valid state, just continue
                    pass
                
                # Send admin notification
                try:
                    from utils.admin_cashout_notifications import notify_admin_cashout_ready_for_processing
                    await notify_admin_cashout_ready_for_processing(cashout)
                    logger.info(f"✅ Admin notification sent for cashout {cashout.cashout_id}")
                except Exception as e:
                    logger.error(f"Failed to send admin notification for {cashout.cashout_id}: {e}")
                
                # Return SUCCESS to user (same experience as configured addresses)
                return {
                    "success": True,  # USER SEES SUCCESS
                    "cashout_id": cashout.cashout_id,
                    "status": "processing",  # User-friendly status
                    "message": f"💰 Cashout request created successfully! We're processing your {cashout.currency} withdrawal.",
                    "auto_processed": True,  # Show as processed from user perspective
                    "requires_manual_review": False,  # Hide admin complexity from user
                    "admin_routing": True,  # Internal flag for admin systems
                    "routing_reason": verification_result.get('routing_reason')
                }
            else:
                # Address is configured and verified - proceed with automatic processing
                logger.info(f"✅ AUTOMATIC_PROCESSING: Address verified, processing cashout {cashout.cashout_id}")
                
                try:
                    from services.kraken_withdrawal_service import get_kraken_withdrawal_service
                    kraken_service = get_kraken_withdrawal_service()
                    
                    # Process withdrawal automatically with context validation
                    from utils.universal_id_generator import UniversalIDGenerator
                    transaction_id = UniversalIDGenerator.generate_transaction_id()
                    
                    withdrawal_result = await kraken_service.execute_withdrawal(
                        currency=cashout.currency,
                        amount=Decimal(str(cashout.amount)),
                        address=cashout.address,  # type: ignore[attr-defined]
                        session=session,
                        cashout_id=cashout.cashout_id,
                        transaction_id=transaction_id
                    )
                    
                    if withdrawal_result.get('success'):
                        # Update cashout with withdrawal details
                        # SECURITY: Validate state transition to prevent overwriting terminal states
                        try:
                            current_status = CashoutStatus(cashout.status)
                            CashoutStateValidator.validate_transition(
                                current_status, 
                                CashoutStatus.PROCESSING, 
                                str(cashout.cashout_id)
                            )
                            cashout.status = "processing"
                            cashout.kraken_withdrawal_id = withdrawal_result.get('kraken_withdrawal_id')  # type: ignore[attr-defined]
                            cashout.external_tx_id = withdrawal_result.get('transaction_id')
                            session.commit()
                        except Exception as validation_error:
                            logger.error(
                                f"🚫 AUTO_PROCESS_BLOCKED: {current_status}→PROCESSING for {cashout.cashout_id}: {validation_error}"
                            )
                            # Graceful degradation: already in a valid state, just continue
                            pass
                        
                        logger.info(
                            f"✅ Automatic withdrawal processed for cashout {cashout.cashout_id} "
                            f"(Kraken ID: {withdrawal_result.get('kraken_withdrawal_id')})"
                        )
                        
                        return {
                            "success": True,
                            "cashout_id": cashout.cashout_id,
                            "tx_hash": withdrawal_result.get('transaction_id'),
                            "status": "processing",
                            "message": f"💰 Cashout request created successfully! We're processing your {cashout.currency} withdrawal.",
                            "auto_processed": True,
                            "kraken_withdrawal_id": withdrawal_result.get('kraken_withdrawal_id')
                        }
                    else:
                        # Withdrawal API failed - route to admin for retry
                        logger.warning(
                            f"⚠️ WITHDRAWAL_API_FAILED: Routing to admin - {withdrawal_result.get('error', 'Unknown withdrawal error')}"
                        )
                        
                        # Update to admin pending (not failed)
                        # SECURITY: Validate state transition to prevent overwriting terminal states
                        try:
                            current_status = CashoutStatus(cashout.status)
                            CashoutStateValidator.validate_transition(
                                current_status, 
                                CashoutStatus.ADMIN_PENDING, 
                                str(cashout.cashout_id)
                            )
                            cashout.status = "admin_pending"
                            cashout.error_message = None  # Clear error for user
                            cashout.admin_notes = f"Withdrawal API error: {withdrawal_result.get('error', 'API processing issue')}"
                            session.commit()
                        except Exception as validation_error:
                            logger.error(
                                f"🚫 AUTO_ROUTE_BLOCKED: {current_status}→ADMIN_PENDING for {cashout.cashout_id}: {validation_error}"
                            )
                            # Graceful degradation: already in a valid state, just continue
                            pass
                        
                        # Send admin notification
                        try:
                            from utils.admin_cashout_notifications import notify_admin_cashout_ready_for_processing
                            await notify_admin_cashout_ready_for_processing(cashout)
                        except Exception as e:
                            logger.error(f"Failed to send admin notification: {e}")
                        
                        # Return SUCCESS to user (consistent experience)
                        return {
                            "success": True,  # USER STILL SEES SUCCESS
                            "cashout_id": cashout.cashout_id,
                            "status": "processing",
                            "message": f"💰 Cashout request created successfully! We're processing your {cashout.currency} withdrawal.",
                            "auto_processed": True,
                            "admin_routing": True,
                            "routing_reason": "api_processing_issue"
                        }
                        
                except Exception as withdrawal_error:
                    # Exception during withdrawal - route to admin
                    logger.error(f"Exception during Kraken withdrawal: {withdrawal_error}")
                    
                    # SECURITY: Validate state transition to prevent overwriting terminal states
                    try:
                        current_status = CashoutStatus(cashout.status)
                        CashoutStateValidator.validate_transition(
                            current_status, 
                            CashoutStatus.ADMIN_PENDING, 
                            str(cashout.cashout_id)
                        )
                        cashout.status = "admin_pending"
                        cashout.error_message = None
                        cashout.admin_notes = f"Withdrawal processing exception: {str(withdrawal_error)}"
                        session.commit()
                    except Exception as validation_error:
                        logger.error(
                            f"🚫 AUTO_ROUTE_BLOCKED: {current_status}→ADMIN_PENDING for {cashout.cashout_id}: {validation_error}"
                        )
                        # Graceful degradation: already in a valid state, just continue
                        pass
                    
                    # Return success to user
                    return {
                        "success": True,  # USER SEES SUCCESS
                        "cashout_id": cashout.cashout_id,
                        "status": "processing",
                        "message": f"💰 Cashout request created successfully! We're processing your {cashout.currency} withdrawal.",
                        "auto_processed": True,
                        "admin_routing": True,
                        "routing_reason": "processing_exception"
                    }
                    
        except Exception as e:
            logger.error(f"Error in _handle_kraken_cashout for {cashout.cashout_id}: {e}")
            
            # Even on exception, return success to user and route to admin
            try:
                # SECURITY: Validate state transition to prevent overwriting terminal states
                try:
                    current_status = CashoutStatus(cashout.status)
                    CashoutStateValidator.validate_transition(
                        current_status, 
                        CashoutStatus.ADMIN_PENDING, 
                        str(cashout.cashout_id)
                    )
                    cashout.status = "admin_pending"
                    cashout.error_message = None
                    cashout.admin_notes = f"System error during processing: {str(e)}"
                    session.commit()
                except Exception as validation_error:
                    logger.error(
                        f"🚫 AUTO_ROUTE_BLOCKED: {current_status}→ADMIN_PENDING for {cashout.cashout_id}: {validation_error}"
                    )
                    # Graceful degradation: already in a valid state, just continue
                    pass
            except Exception as db_error:
                logger.error(f"Database error while updating cashout status: {db_error}")
            
            return {
                "success": True,  # CONSISTENT USER SUCCESS
                "cashout_id": cashout.cashout_id,
                "status": "processing",
                "message": f"💰 Cashout request created successfully! We're processing your {cashout.currency} withdrawal.",
                "auto_processed": True,
                "admin_routing": True,
                "routing_reason": "system_error"
            }

    @classmethod
    async def _process_automatic_cashout(
        cls, escrow: Escrow, seller: User, session: Session
    ) -> Dict[str, Any]:
        """Process automatic cashout using explicitly configured destinations only"""
        try:
            from utils.fee_calculator import FeeCalculator

            from decimal import Decimal

            # Use Decimal precision for financial calculations
            amount_decimal = (
                Decimal(str(escrow.amount)) if escrow.amount else Decimal("0")  # type: ignore[arg-type]
            )
            seller_fee_decimal = (
                Decimal(str(escrow.seller_fee_amount))
                if escrow.seller_fee_amount  # type: ignore[arg-type]
                else None
            )

            # FeeCalculator now accepts Decimal to maintain precision throughout
            release_amount = FeeCalculator.calculate_release_amount(
                escrow_amount=amount_decimal, 
                seller_fee_amount=seller_fee_decimal
            )

            # Create seller fee transactions at release time (revenue recognition)
            from utils.fee_calculator import FeeCalculator

            fee_transactions = FeeCalculator.create_fee_transactions_at_release(
                escrow, seller.id, session
            )
            if fee_transactions:
                logger.info(
                    f"Created {len(fee_transactions)} seller fee transactions for escrow {escrow.escrow_id}"
                )

            # Check user's cashout preference and get explicit destination
            preference = seller.cashout_preference or "CRYPTO"

            if preference == "NGN_BANK":
                # Use explicitly configured NGN bank account
                return await cls._process_ngn_auto_cashout(
                    escrow, seller, float(release_amount), session
                )
            else:  # CRYPTO
                # Use explicitly configured crypto address
                return await cls._process_crypto_auto_cashout(
                    escrow, seller, float(release_amount), session
                )

        except Exception as e:
            logger.error(f"Error in automatic cashout processing: {e}")
            # Fallback to wallet credit on any error
            return await cls._credit_wallet_normally(escrow, seller, session)

    @classmethod
    async def _process_crypto_auto_cashout(
        cls, escrow: Escrow, seller: User, amount: float, session: Session
    ) -> Dict[str, Any]:
        """Process crypto auto-cashout using explicit destination"""
        try:
            # Get the explicitly configured crypto address
            crypto_address = seller.auto_cashout_crypto_address  # type: ignore[attr-defined]
            if not crypto_address:
                logger.error(
                    f"No auto-cashout crypto address configured for user {seller.id}"
                )
                return await cls._credit_wallet_normally(escrow, seller, session)

            from utils.universal_id_generator import UniversalIDGenerator

            cashout_utid = UniversalIDGenerator.generate_cashout_id()

            # Create cashout request using explicit destination
            cashout_request = Cashout(
                utid=cashout_utid,
                user_id=seller.id,
                amount=amount,
                currency=crypto_address.currency,  # CRITICAL FIX: Use actual currency from address, not hardcoded "USDT"
                network=crypto_address.network,
                address=crypto_address.address,
                status="processing",
                escrow_id=escrow.id,
                reason=f"Auto-cashout - Escrow #{escrow.escrow_id} completion",
            )
            session.add(cashout_request)
            session.commit()

            logger.info(
                f"Crypto auto-cashout request created for escrow {escrow.escrow_id}"
            )
            
            # Notify admin of cashout started with crypto amount calculation
            try:
                # Calculate crypto amount for display using current rate
                crypto_amount_for_display = None
                net_amount_after_fees = float(cashout_request.net_amount) if cashout_request.net_amount else float(amount)
                network_fee = float(cashout_request.network_fee) if cashout_request.network_fee else 0.0
                
                try:
                    from services.fastforex_service import FastForexService
                    forex_service = FastForexService()
                    crypto_rate = forex_service.get_crypto_rate(crypto_address.currency)
                    if crypto_rate and float(crypto_rate) > 0:
                        crypto_amount_for_display = net_amount_after_fees / float(crypto_rate)
                except Exception as rate_error:
                    logger.warning(f"Could not calculate crypto amount for notification: {rate_error}")
                
                asyncio.create_task(
                    admin_trade_notifications.notify_cashout_started({
                        'cashout_id': cashout_request.cashout_id,
                        'user_id': seller.id,
                        'username': seller.username or 'N/A',
                        'first_name': seller.first_name or 'Unknown',
                        'last_name': seller.last_name or '',
                        'amount': float(amount),
                        'currency': 'USD',  # CRITICAL FIX: Show source currency (USD), not target crypto
                        'target_currency': crypto_address.currency,  # Add target currency for display
                        'net_amount': net_amount_after_fees,  # Net USD after fees
                        'network_fee': network_fee,  # Network fee in USD
                        'crypto_amount': crypto_amount_for_display,  # Calculated crypto amount
                        'cashout_type': 'crypto',
                        'destination': crypto_address.address,
                        'started_at': cashout_request.created_at or datetime.utcnow()
                    })
                )
            except Exception as notify_error:
                logger.error(f"Failed to notify admin of cashout started {cashout_request.cashout_id}: {notify_error}")
            
            return {
                "success": True,
                "message": "Crypto auto-cashout request created",
            }

        except Exception as e:
            logger.error(f"Error in crypto auto-cashout: {e}")
            return await cls._credit_wallet_normally(escrow, seller, session)

    @classmethod
    async def _process_ngn_auto_cashout(
        cls, escrow: Escrow, seller: User, amount: float, session: Session
    ) -> Dict[str, Any]:
        """Process NGN auto-cashout using explicit destination"""
        try:
            # Get the explicitly configured bank account
            bank_account = seller.auto_cashout_bank_account  # type: ignore[attr-defined]
            if not bank_account:
                logger.error(
                    f"No auto-cashout bank account configured for user {seller.id}"
                )
                return await cls._credit_wallet_normally(escrow, seller, session)

            # Use unified atomic cashout service to process
            from services.auto_cashout import AutoCashoutService
            from decimal import Decimal

            # Create cashout request
            cashout_result = await AutoCashoutService.create_cashout_request(  # type: ignore[attr-defined]
                user_id=seller.id,
                amount=Decimal(str(amount)),
                currency="USD",
                cashout_type=CashoutType.NGN_BANK.value,
                destination=f"{seller.auto_cashout_bank_code}:{seller.auto_cashout_bank_account}",  # type: ignore[attr-defined]
            )

            if cashout_result.get("success"):
                # Auto-approve and process
                result = await AutoCashoutService.process_approved_cashout(
                    cashout_id=cashout_result.get("cashout_id"),
                    admin_approved=True,
                )
            else:
                result = cashout_result

            if result.get("success"):
                logger.info(
                    f"NGN auto-cashout processed for escrow {escrow.escrow_id}"
                )
                return result
            else:
                logger.error(
                    f"NGN auto-cashout failed for escrow {escrow.escrow_id}: {result.get('error')}"
                )
                return await cls._credit_wallet_normally(escrow, seller, session)

        except Exception as e:
            logger.error(f"Error in NGN auto-cashout: {e}")
            return await cls._credit_wallet_normally(escrow, seller, session)

    @classmethod
    async def _create_admin_approval_request(
        cls, escrow: Escrow, seller: User, session: Session, reason: str
    ) -> Dict[str, Any]:
        """Create cashout request requiring admin approval"""
        try:
            from utils.universal_id_generator import UniversalIDGenerator

            cashout_utid = UniversalIDGenerator.generate_cashout_id()

            # Use explicit destinations or fallback for admin processing
            preference = seller.cashout_preference or "CRYPTO"

            if preference == "NGN_BANK" and seller.auto_cashout_bank_account:  # type: ignore[attr-defined]
                # Admin approval for NGN with configured account
                cashout_request = Cashout(
                    utid=cashout_utid,
                    user_id=seller.id,
                    amount=Decimal(str(escrow.amount)),
                    currency="NGN",
                    status="pending",
                    reason=f"Requires approval: {reason} | From escrow {escrow.escrow_id}",
                )
            else:
                # Admin approval for crypto with configured address or fallback
                address = "TBA"
                network = "USDT-TRC20"

                if seller.auto_cashout_crypto_address:  # type: ignore[attr-defined]
                    address = seller.auto_cashout_crypto_address.address  # type: ignore[attr-defined]
                    network = seller.auto_cashout_crypto_address.network  # type: ignore[attr-defined]

                cashout_request = Cashout(
                    utid=cashout_utid,
                    user_id=seller.id,
                    amount=Decimal(str(escrow.amount)),
                    currency="USDT",
                    address=address,
                    network=network,
                    status="pending",
                    reason=f"Requires approval: {reason} | From escrow {escrow.escrow_id}",
                )

            session.add(cashout_request)
            session.commit()
            
            # ENABLED: Admin notifications for escrow cashout creation
            if Config.ADMIN_CASHOUT_CREATION_ALERTS:
                try:
                    from utils.admin_cashout_notifications import notify_admin_cashout_ready_for_processing
                    await notify_admin_cashout_ready_for_processing(cashout_request)
                    logger.info(f"✅ Admin notified: Escrow cashout {cashout_request.cashout_id} created successfully")
                except Exception as e:
                    logger.error(f"Failed to send admin notification for escrow cashout {cashout_request.cashout_id}: {e}")
            else:
                logger.info(f"Cashout {cashout_request.cashout_id} created successfully (admin notifications disabled by config)")
            
            # Notify admin of cashout started
            try:
                cashout_type = 'ngn_bank' if preference == "NGN_BANK" else 'crypto'
                if preference == "NGN_BANK":
                    destination_display = f"{seller.auto_cashout_bank_code}:{seller.auto_cashout_bank_account}" if seller.auto_cashout_bank_account else "TBA"
                else:
                    destination_display = cashout_request.address or "TBA"
                
                # Determine source and target currency
                if cashout_type == 'crypto':
                    source_currency = 'USD'  # Escrow amounts are in USD
                    target_currency = cashout_request.currency  # Target crypto
                else:
                    source_currency = cashout_request.currency  # For NGN, it's direct
                    target_currency = None
                
                # Calculate crypto amount for display
                crypto_amount_display = None
                net_amount_display = float(cashout_request.net_amount) if cashout_request.net_amount else float(cashout_request.amount)
                network_fee_display = float(cashout_request.network_fee) if cashout_request.network_fee else 0.0
                
                if cashout_type == 'crypto' and target_currency:
                    try:
                        from services.fastforex_service import FastForexService
                        forex = FastForexService()
                        rate = forex.get_crypto_rate(target_currency)
                        if rate and float(rate) > 0:
                            crypto_amount_display = net_amount_display / float(rate)
                    except Exception:
                        pass
                
                asyncio.create_task(
                    admin_trade_notifications.notify_cashout_started({
                        'cashout_id': cashout_request.cashout_id,
                        'user_id': seller.id,
                        'username': seller.username or 'N/A',
                        'first_name': seller.first_name or 'Unknown',
                        'last_name': seller.last_name or '',
                        'amount': float(cashout_request.amount),
                        'currency': source_currency,  # CRITICAL FIX: Show source currency correctly
                        'target_currency': target_currency,  # Add target currency for crypto conversions
                        'net_amount': net_amount_display,  # Net USD after fees
                        'network_fee': network_fee_display,  # Network fee
                        'crypto_amount': crypto_amount_display,  # Calculated crypto amount
                        'cashout_type': cashout_type,
                        'destination': destination_display,
                        'started_at': cashout_request.created_at or datetime.utcnow()
                    })
                )
            except Exception as notify_error:
                logger.error(f"Failed to notify admin of cashout started {cashout_request.cashout_id}: {notify_error}")

            # Credit user's wallet for the transaction (after deducting seller fee)
            # CRITICAL FIX: Use sync method since we're in sync context
            escrow_amount = Decimal(str(escrow.amount))
            seller_fee = Decimal(str(escrow.seller_fee_amount)) if escrow.seller_fee_amount else Decimal("0.0")
            release_amount = escrow_amount - seller_fee
            
            logger.info(f"💰 Admin cashout {escrow.escrow_id}: amount=${escrow_amount}, seller_fee=${seller_fee}, release_amount=${release_amount}")
            
            success = CryptoServiceAtomic.credit_user_wallet_simple(
                user_id=seller.id,
                amount=float(release_amount),  # FIX: Use release_amount, not full escrow.amount
                description=f"💼 Trade payment from {escrow.buyer.full_name or escrow.buyer.username} for #{escrow.escrow_id} (Fee: ${seller_fee})"
            )

            if success:
                logger.info(
                    f"Admin approval cashout request created for escrow {escrow.escrow_id}"
                )
                return {
                    "success": True,
                    "message": "Cashout request created for admin approval",
                }
            else:
                logger.error(
                    f"Failed to credit wallet for admin approval request - escrow {escrow.escrow_id}"
                )
                return {
                    "success": False,
                    "error": "Failed to process cashout request",
                }

        except Exception as e:
            logger.error(f"Error creating admin approval request: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    async def _credit_wallet_normally(
        cls, escrow: Escrow, seller: User, session: Session
    ) -> Dict[str, Any]:
        """Credit user's wallet normally without auto-cashout"""
        try:
            buyer_name = (
                escrow.buyer.full_name
                or escrow.buyer.username
                or f"User #{escrow.buyer_id}"
            )

            # Calculate release amount (escrow amount minus seller fee)
            # CRITICAL FIX: Use sync method since we're in sync context
            escrow_amount = Decimal(str(escrow.amount))
            seller_fee = Decimal(str(escrow.seller_fee_amount)) if escrow.seller_fee_amount else Decimal("0.0")
            release_amount = escrow_amount - seller_fee
            
            logger.info(f"💰 Normal credit {escrow.escrow_id}: amount=${escrow_amount}, seller_fee=${seller_fee}, release_amount=${release_amount}")

            success = CryptoServiceAtomic.credit_user_wallet_simple(
                user_id=seller.id,
                amount=float(release_amount),  # FIX: Use release_amount, not full escrow.amount
                description=f"💼 Trade payment from {buyer_name} for #{escrow.escrow_id} (Fee: ${seller_fee})"
            )

            if success:
                logger.info(f"Wallet credited normally for escrow {escrow.escrow_id}")
                return {"success": True, "message": "Funds credited to wallet"}
            else:
                logger.error(f"Failed to credit wallet for escrow {escrow.escrow_id}")
                return {"success": False, "error": "Failed to credit wallet"}

        except Exception as e:
            logger.error(f"Error crediting wallet normally: {e}")
            return {"success": False, "error": str(e)}

    @classmethod
    async def send_auto_cashout_notification(
        cls, user_id: int, amount: float, escrow_id: str, bot_token: str
    ):
        """Send notification about auto-cashout processing"""
        try:
            from telegram import Bot

            bot = Bot(token=bot_token)

            # FIXED: Remove markdown to prevent parse entities error
            message = f"🔄 Auto-Cashout - Escrow #{escrow_id}\n\n${amount:.2f} processing to your account\n\n⏱ Confirmation coming soon"

            await bot.send_message(chat_id=user_id, text=message)

        except Exception as e:
            logger.error(f"Error sending auto-cashout notification: {e}")

    @classmethod
    async def send_wallet_credit_notification(
        cls, user_id: int, amount: float, escrow_id: str, bot_token: str
    ):
        """Send notification about wallet credit (when auto-cashout not configured)"""
        try:
            from telegram import Bot

            bot = Bot(token=bot_token)

            # FIXED: Remove markdown to prevent parse entities error
            message = f"💰 Payment - Escrow #{escrow_id}\n\n${amount:.2f} added to wallet\n\n/wallet to view"

            await bot.send_message(chat_id=user_id, text=message)

        except Exception as e:
            logger.error(f"Error sending wallet credit notification: {e}")

    # REMOVED: Old _send_urgent_address_config_alert and _get_user_recent_transactions_for_admin_email methods
    # Replaced with simplified notification system using admin_funding_notifications.send_address_configuration_alert

    @classmethod
    async def process_approved_cashout(
        cls, cashout_id: str, admin_approved: bool = True
    ) -> Dict[str, Any]:
        """
        Process admin-approved cashout completions.
        Handles both crypto (Kraken) and NGN bank (Fincra) processing.
        CRITICAL: Consumes frozen holds when successful.
        
        Args:
            cashout_id: The cashout ID to process
            admin_approved: Whether this is admin-approved (default: True)
            
        Returns:
            Dict with success status, results, and details
        """

        from models import Cashout, CashoutStatus, User
        from datetime import datetime
        
        logger.info(f"🔄 ADMIN_CASHOUT_PROCESS: Starting approved cashout processing for {cashout_id}")
        
        session = SyncSessionLocal()
        try:
            # Find the cashout record
            cashout = session.query(Cashout).filter(
                Cashout.cashout_id == cashout_id
            ).first()
            
            if not cashout:
                error_msg = f"Cashout {cashout_id} not found"
                logger.error(f"❌ {error_msg}")
                return {"success": False, "error": error_msg}
            
            # Get user details for processing
            user = session.query(User).filter(User.id == cashout.user_id).first()
            if not user:
                error_msg = f"User {cashout.user_id} not found for cashout {cashout_id}"
                logger.error(f"❌ {error_msg}")
                return {"success": False, "error": error_msg}
            
            # Validate cashout can be processed
            processable_statuses = [
                CashoutStatus.APPROVED.value,
                CashoutStatus.ADMIN_PENDING.value,
                CashoutStatus.PENDING_ADDRESS_CONFIG.value,
                CashoutStatus.PENDING_SERVICE_FUNDING.value,
                CashoutStatus.AWAITING_RESPONSE.value,  # CRITICAL FIX: Include unified transaction status
                CashoutStatus.PROCESSING.value,  # CRITICAL FIX: Include processing status for immediate cashouts
                CashoutStatus.PENDING.value  # Include pending status for auto-cashout processing
            ]
            
            if cashout.status not in processable_statuses:
                error_msg = f"Cashout {cashout_id} has status {cashout.status}, cannot process (expected one of: {processable_statuses})"
                logger.error(f"❌ {error_msg}")
                return {"success": False, "error": error_msg}
            
            # Update admin approval fields
            if admin_approved:
                cashout.admin_approved = True
                cashout.admin_approved_at = datetime.utcnow()  # type: ignore[attr-defined]
                cashout.status = CashoutStatus.EXECUTING.value
                session.commit()
                
                logger.info(f"✅ Admin approval recorded for cashout {cashout_id}")
            
            # Route to appropriate processing method based on cashout type
            cashout_type = as_str(cashout.cashout_type) or CashoutType.CRYPTO.value
            
            if cashout_type == CashoutType.NGN_BANK.value:
                # Process NGN bank transfer
                return await cls._process_ngn_cashout(cashout, user, session)
            else:
                # Process crypto cashout
                return await cls._process_crypto_cashout(cashout, user, session)
                
        except Exception as e:
            logger.error(f"❌ ADMIN_CASHOUT_ERROR: Exception processing {cashout_id}: {e}")
            session.rollback()
            return {
                "success": False,
                "error": f"Failed to process cashout: {str(e)}",
                "cashout_id": cashout_id
            }
        finally:
            session.close()

    @classmethod
    async def _process_crypto_cashout(
        cls, cashout: "Cashout", user: "User", session: Session
    ) -> Dict[str, Any]:
        """Process crypto cashout via Kraken - with unified retry integration"""
        from services.kraken_withdrawal_service import get_kraken_withdrawal_service
        from services.kraken_address_verification_service import KrakenAddressVerificationService
        from services.fastforex_service import FastForexService
        from models import CashoutStatus
        
        # Create UnifiedTransaction for retry tracking (if not already exists)
        unified_tx_id = None
        try:
            # Check if unified transaction already exists (query by reference_id which stores cashout_id)
            existing_unified_tx = session.query(UnifiedTransaction).filter(
                UnifiedTransaction.reference_id == cashout.cashout_id
            ).first()
            
            if not existing_unified_tx:
                unified_tx_id = cls._create_unified_transaction_for_cashout(cashout, user, session)
            else:
                unified_tx_id = existing_unified_tx.transaction_id  # type: ignore[attr-defined]
                # Update status to processing using validated transition
                UnifiedTransactionStateValidator.validate_and_transition(
                    existing_unified_tx,
                    UnifiedTransactionStatus.PROCESSING,
                    transaction_id=existing_unified_tx.transaction_id,  # type: ignore[attr-defined]
                    force=False
                )
                existing_unified_tx.updated_at = datetime.utcnow()  # type: ignore[assignment]
                
            logger.info(f"🔄 Processing crypto cashout {cashout.cashout_id} with UnifiedTransaction {unified_tx_id}")
            
        except Exception as e:
            logger.warning(f"Failed to create/update UnifiedTransaction for {cashout.cashout_id}: {e}")
            # Continue processing even if unified transaction fails
        
        cashout_id = cashout.cashout_id
        destination = cashout.destination
        user_id = user.id
        
        # CRITICAL FIX: Handle both new (USD-based) and legacy (crypto-based) cashouts
        if cashout.cashout_metadata and 'target_currency' in cashout.cashout_metadata:
            # NEW FLOW: Cashout stored in USD, need to convert to crypto
            target_currency = cashout.cashout_metadata['target_currency']
            
            # CRITICAL FIX: Use net_amount (after fees) for conversion, not gross amount
            usd_amount = as_decimal(cashout.net_amount) or as_decimal(cashout.amount)
            
            logger.info(
                f"💱 CRYPTO_CONVERSION: Converting ${usd_amount} USD to {target_currency} "
                f"for cashout {cashout_id}"
            )
            
            # Get current exchange rate
            try:
                from services.fastforex_service import FastForexService
                forex_service = FastForexService()
                
                # Get crypto rate in USD (synchronous call, returns Decimal directly)
                crypto_rate = forex_service.get_crypto_rate(target_currency)
                if not crypto_rate:
                    error_msg = f"Failed to get {target_currency} exchange rate"
                    logger.error(f"❌ {error_msg}")
                    
                    # Route to admin for manual processing
                    return await cls._finalize_user_visible_success_and_notify_admin(
                        cashout=cashout,
                        reason="exchange_rate_unavailable",
                        unified_tx_id=unified_tx_id,  # type: ignore[arg-type]
                        session=session,
                        user_id=user_id,
                        amount=float(usd_amount),
                        currency="USD",
                        destination=destination or "",
                        error_details={"error": error_msg, "target_currency": target_currency}
                    )
                
                # crypto_rate is already a Decimal, use it directly
                crypto_amount = usd_amount / crypto_rate
                
                logger.info(
                    f"✅ CONVERSION_COMPLETE: ${usd_amount} USD = {crypto_amount:.8f} {target_currency} "
                    f"(rate: ${crypto_rate:.4f})"
                )
                
                # Update crypto amount for withdrawal
                currency = target_currency
                amount_crypto = crypto_amount
                
            except Exception as rate_error:
                logger.error(f"❌ Failed to convert USD to crypto for {cashout_id}: {rate_error}")
                
                # Route to admin for manual processing
                return await cls._finalize_user_visible_success_and_notify_admin(
                    cashout=cashout,
                    reason="conversion_error",
                    unified_tx_id=unified_tx_id,  # type: ignore[arg-type]
                    session=session,
                    user_id=user_id,
                    amount=float(usd_amount),
                    currency="USD",
                    destination=destination or "",
                    error_details={"error": str(rate_error), "target_currency": target_currency}
                )
        else:
            # LEGACY FLOW: Amount already in crypto
            currency = as_str(cashout.currency) or "BTC"
            amount_crypto = as_decimal(cashout.net_amount) or as_decimal(cashout.amount)
            
            logger.info(
                f"🔄 LEGACY_FLOW: Processing {amount_crypto:.8f} {currency} "
                f"(no conversion needed)"
            )
        
        # Step 2: Verify address is configured in Kraken
        try:
            from services.kraken_address_verification_service import KrakenAddressVerificationService
            verification_service = KrakenAddressVerificationService()
            verification_result = await verification_service.verify_withdrawal_address(
                crypto_currency=currency,
                withdrawal_address=destination or ""
            )
            
            if not verification_result.get('address_exists'):
                # Address not configured - route to admin
                logger.warning(
                    f"⚠️ ADDRESS_NOT_CONFIGURED: Routing to admin - "
                    f"{verification_result.get('routing_reason')}"
                )
                
                # Determine correct amount to show admin (USD for new flow, crypto for legacy)
                is_usd_flow = cashout.cashout_metadata and 'target_currency' in cashout.cashout_metadata
                admin_amount = float(cashout.amount) if is_usd_flow else float(amount_crypto)
                
                return await cls._finalize_user_visible_success_and_notify_admin(
                    cashout=cashout,
                    reason="address_configuration_required",
                    unified_tx_id=unified_tx_id,  # type: ignore[arg-type]
                    session=session,
                    user_id=user_id,
                    amount=admin_amount,
                    currency=currency,  # Always use target crypto currency (ETH/BTC/etc)
                    destination=destination or "",
                    error_details=verification_result
                )
                
        except Exception as verify_error:
            logger.error(f"❌ Address verification error for {cashout_id}: {verify_error}")
            
            # Determine correct amount to show admin (USD for new flow, crypto for legacy)
            is_usd_flow = cashout.cashout_metadata and 'target_currency' in cashout.cashout_metadata
            admin_amount = float(cashout.amount) if is_usd_flow else float(amount_crypto)
            
            # Route to admin on verification errors
            return await cls._finalize_user_visible_success_and_notify_admin(
                cashout=cashout,
                reason="address_verification_error",
                unified_tx_id=unified_tx_id,  # type: ignore[arg-type]
                session=session,
                user_id=user_id,
                amount=admin_amount,
                currency=currency,  # Always use target crypto currency (ETH/BTC/etc)
                destination=destination or "",
                error_details={"error": str(verify_error)}
            )
        
        # Step 3: Execute withdrawal via Kraken
        try:
            kraken_service = get_kraken_withdrawal_service()
            
            # Generate transaction ID for idempotency
            transaction_id = UniversalIDGenerator.generate_transaction_id()
            
            withdrawal_result = await kraken_service.execute_withdrawal(
                currency=currency,
                amount=amount_crypto,
                address=destination or "",
                session=session,
                cashout_id=cashout_id,
                transaction_id=transaction_id
            )
            
            if withdrawal_result.get('success'):
                # Update cashout with success
                cashout.status = CashoutStatus.SUCCESS.value
                cashout.completed_at = datetime.utcnow()
                cashout.external_tx_id = withdrawal_result.get('txid')
                cashout.admin_notes = f"Kraken withdrawal successful: {withdrawal_result.get('kraken_withdrawal_id')}"
                session.commit()
                
                # Notify admin of cashout completed with full amount breakdown
                try:
                    net_usd = float(cashout.net_amount) if cashout.net_amount else float(cashout.amount)
                    network_fee_usd = float(cashout.network_fee) if cashout.network_fee else 0.0
                    
                    asyncio.create_task(
                        admin_trade_notifications.notify_cashout_completed({
                            'cashout_id': cashout_id,
                            'user_id': user_id,
                            'username': user.username if user else 'N/A',
                            'first_name': user.first_name if user and user.first_name else 'Unknown',
                            'last_name': user.last_name if user and user.last_name else '',
                            'amount': float(amount_crypto),
                            'currency': currency,
                            'usd_amount': float(cashout.amount),  # Original USD amount (gross)
                            'crypto_amount': float(amount_crypto),  # Exact crypto amount sent
                            'net_amount': net_usd,  # Net USD after fees
                            'network_fee': network_fee_usd,  # Network fee in USD
                            'cashout_type': 'crypto',
                            'destination': destination or '',
                            'txid': withdrawal_result.get('txid', 'N/A'),
                            'completed_at': datetime.utcnow()
                        })
                    )
                except Exception as notify_error:
                    logger.error(f"Failed to notify admin of cashout completed {cashout_id}: {notify_error}")
                
                # Consume holds
                try:
                    from utils.cashout_completion_handler import auto_release_completed_cashout_hold
                    await auto_release_completed_cashout_hold(
                        cashout_id=cashout_id,
                        user_id=user_id,
                        session=session
                    )
                    logger.info(f"✅ Hold consumed for successful cashout {cashout_id}")
                except Exception as hold_error:
                    logger.error(f"❌ Failed to consume hold for {cashout_id}: {hold_error}")
                
                # Send email notification to user
                try:
                    from services.withdrawal_notification_service import WithdrawalNotificationService
                    
                    # Get user details for email notification (user is already available from function params)
                    if user and user.email:
                        notification_service = WithdrawalNotificationService()
                        await notification_service._send_email_notification(
                            user_email=user.email,
                            cashout_id=cashout_id,
                            amount=float(amount_crypto),
                            currency=currency,
                            blockchain_hash=withdrawal_result.get('txid', ''),
                            usd_amount=float(cashout.amount),
                            destination_address=destination,
                            pending_funding=False
                        )
                        logger.info(f"✅ EMAIL_SENT: User {user_id} ({user.email}) notified via email for cashout {cashout_id}")
                    else:
                        logger.warning(f"⚠️ No email available for user {user_id} - skipping email notification")
                except Exception as email_error:
                    logger.error(f"❌ Failed to send email notification for {cashout_id}: {email_error}")
                
                logger.info(f"✅ CRYPTO_CASHOUT_SUCCESS: {cashout_id} - {amount_crypto:.8f} {currency}")
                
                return {
                    "success": True,
                    "cashout_id": cashout_id,
                    "amount": float(amount_crypto),
                    "currency": currency,
                    "txid": withdrawal_result.get('txid'),
                    "status": "completed"
                }
                
            else:
                # Withdrawal failed - check error type
                error_msg = withdrawal_result.get('error', 'Unknown Kraken error')
                error_code = withdrawal_result.get('error_code', 'UNKNOWN')
                
                logger.error(f"❌ KRAKEN_WITHDRAWAL_FAILED: {cashout_id} - {error_msg}")
                
                # Check for insufficient funds error
                if 'insufficient funds' in error_msg.lower() or error_code == 'API_INSUFFICIENT_FUNDS':
                    # Route to admin for funding
                    return await cls._finalize_user_visible_success_and_notify_admin(
                        cashout=cashout,
                        reason="kraken_insufficient_funds",
                        unified_tx_id=unified_tx_id,  # type: ignore[arg-type]
                        session=session,
                        user_id=user_id,
                        amount=float(amount_crypto),
                        currency=currency,
                        destination=destination or "",
                        error_details={"error": error_msg, "error_code": error_code}
                    )
                else:
                    # Other Kraken errors - mark as failed and create retry log
                    cashout.status = CashoutStatus.FAILED.value
                    cashout.error_message = error_msg
                    cashout.failed_at = datetime.utcnow()
                    session.commit()
                    
                    # Create retry log for technical errors
                    if unified_tx_id:
                        try:
                            async with AsyncSessionLocal() as async_session:
                                await cls._create_unified_retry_log_entry(
                                    unified_transaction_id=unified_tx_id,
                                    error_code=error_code,
                                    error_message=error_msg,
                                    external_provider="kraken",
                                    session=async_session
                                )
                                await async_session.commit()
                        except Exception as retry_error:
                            logger.error(f"Failed to create retry log: {retry_error}")
                    
                    return {
                        "success": False,
                        "cashout_id": cashout_id,
                        "error": error_msg,
                        "error_code": error_code
                    }
                    
        except Exception as withdrawal_error:
            logger.error(f"❌ WITHDRAWAL_EXCEPTION: {cashout_id} - {withdrawal_error}")
            
            # Route to admin on exceptions
            return await cls._finalize_user_visible_success_and_notify_admin(
                cashout=cashout,
                reason="withdrawal_exception",
                unified_tx_id=unified_tx_id,  # type: ignore[arg-type]
                session=session,
                user_id=user_id,
                amount=float(amount_crypto),
                currency=currency,
                destination=destination or "",
                error_details={"error": str(withdrawal_error)}
            )

    @classmethod
    async def _process_ngn_cashout(
        cls, cashout: "Cashout", user: "User", session: Session
    ) -> Dict[str, Any]:
        """Process NGN bank transfer via Fincra - with unified retry integration"""
        from services.fincra_service import FincraService
        from services.fastforex_service import FastForexService
        from decimal import Decimal
        from models import CashoutStatus, SavedBankAccount
        
        # Create UnifiedTransaction for retry tracking (if not already exists)
        unified_tx_id = None
        try:
            # Check if unified transaction already exists
            existing_unified_tx = session.query(UnifiedTransaction).filter(
                UnifiedTransaction.reference_id == cashout.cashout_id
            ).first()
            
            if not existing_unified_tx:
                unified_tx_id = cls._create_unified_transaction_for_cashout(cashout, user, session)
            else:
                unified_tx_id = existing_unified_tx.transaction_id  # type: ignore[attr-defined]
                UnifiedTransactionStateValidator.validate_and_transition(
                    existing_unified_tx,
                    UnifiedTransactionStatus.PROCESSING,
                    transaction_id=existing_unified_tx.transaction_id,  # type: ignore[attr-defined]
                    force=False
                )
                existing_unified_tx.updated_at = datetime.utcnow()  # type: ignore[assignment]
                
            logger.info(f"🔄 Processing NGN cashout {cashout.cashout_id} with UnifiedTransaction {unified_tx_id}")
            
        except Exception as e:
            logger.warning(f"Failed to create/update UnifiedTransaction for {cashout.cashout_id}: {e}")
        
        cashout_id = cashout.cashout_id
        user_id = user.id
        usd_amount = as_decimal(cashout.net_amount) or as_decimal(cashout.amount)
        
        # Get bank account details
        bank_account = None
        bank_account_id = as_int(cashout.bank_account_id)
        
        if bank_account_id:
            bank_account = session.query(SavedBankAccount).filter_by(
                id=bank_account_id,
                user_id=user_id,
                is_active=True
            ).first()
        
        if not bank_account:
            error_msg = "No valid bank account found for NGN cashout"
            logger.error(f"❌ {error_msg}: cashout {cashout_id}")
            
            return await cls._finalize_user_visible_success_and_notify_admin(
                cashout=cashout,
                reason="bank_account_not_found",
                unified_tx_id=unified_tx_id,  # type: ignore[arg-type]
                session=session,
                user_id=user_id,
                amount=float(usd_amount),
                currency="USD",
                destination="NGN Bank Transfer",
                error_details={"error": error_msg, "bank_account_id": bank_account_id}
            )
        
        # Convert USD to NGN
        try:
            forex_service = FastForexService()
            rate_data = await forex_service.get_usd_ngn_rate()  # type: ignore[attr-defined]
            
            if not rate_data or not rate_data.get('rate'):
                error_msg = "Failed to get USD-NGN exchange rate"
                logger.error(f"❌ {error_msg}")
                
                return await cls._finalize_user_visible_success_and_notify_admin(
                    cashout=cashout,
                    reason="exchange_rate_unavailable",
                    unified_tx_id=unified_tx_id,  # type: ignore[arg-type]
                    session=session,
                    user_id=user_id,
                    amount=float(usd_amount),
                    currency="USD",
                    destination=f"{bank_account.bank_name} - {bank_account.account_number}",
                    error_details={"error": error_msg}
                )
            
            ngn_rate = Decimal(str(rate_data['rate']))
            ngn_amount = usd_amount * ngn_rate
            
            logger.info(
                f"✅ CONVERSION: ${usd_amount} USD = ₦{ngn_amount:.2f} NGN (rate: ₦{ngn_rate:.2f})"
            )
            
        except Exception as rate_error:
            logger.error(f"❌ Failed to convert USD to NGN for {cashout_id}: {rate_error}")
            
            return await cls._finalize_user_visible_success_and_notify_admin(
                cashout=cashout,
                reason="conversion_error",
                unified_tx_id=unified_tx_id,  # type: ignore[arg-type]
                session=session,
                user_id=user_id,
                amount=float(usd_amount),
                currency="USD",
                destination=f"{bank_account.bank_name} - {bank_account.account_number}",
                error_details={"error": str(rate_error)}
            )
        
        # Process Fincra payout
        try:
            fincra_service = FincraService()
            
            # Set idempotency reference before API call
            if not cashout.fincra_request_id:
                cashout.fincra_request_id = f"FINCRA_{UniversalIDGenerator.generate_transaction_id()}"
                session.commit()
            
            payout_result = await fincra_service.initiate_payout(
                amount_ngn=float(ngn_amount),
                bank_code=bank_account.bank_code,
                account_number=bank_account.account_number,
                account_name=bank_account.account_name,
                reference=cashout.fincra_request_id,
                user_id=user_id
            )
            
            if payout_result and payout_result.get('success'):
                # Update cashout with success
                cashout.status = CashoutStatus.SUCCESS.value
                cashout.completed_at = datetime.utcnow()
                cashout.external_id = payout_result.get('payout_id')
                cashout.admin_notes = f"Fincra payout successful: ₦{ngn_amount:.2f}"
                session.commit()
                
                # Notify admin of cashout completed with NGN conversion details
                try:
                    asyncio.create_task(
                        admin_trade_notifications.notify_cashout_completed({
                            'cashout_id': cashout_id,
                            'user_id': user_id,
                            'username': user.username if user else 'N/A',
                            'first_name': user.first_name if user and user.first_name else 'Unknown',
                            'last_name': user.last_name if user and user.last_name else '',
                            'amount': float(ngn_amount),
                            'currency': 'NGN',
                            'usd_amount': float(usd_amount),  # Original USD amount
                            'cashout_type': 'ngn_bank',
                            'destination': f"{bank_account.bank_name} - {bank_account.account_number}",
                            'txid': payout_result.get('payout_id', 'N/A'),
                            'completed_at': datetime.utcnow()
                        })
                    )
                except Exception as notify_error:
                    logger.error(f"Failed to notify admin of cashout completed {cashout_id}: {notify_error}")
                
                # Consume holds
                try:
                    from utils.cashout_completion_handler import auto_release_completed_cashout_hold
                    await auto_release_completed_cashout_hold(
                        cashout_id=cashout_id,
                        user_id=user_id,
                        session=session
                    )
                    logger.info(f"✅ Hold consumed for successful NGN cashout {cashout_id}")
                except Exception as hold_error:
                    logger.error(f"❌ Failed to consume hold for {cashout_id}: {hold_error}")
                
                # NOTE: Wallet handler sends the user notification - no duplicate notification needed here
                
                logger.info(f"✅ NGN_CASHOUT_SUCCESS: {cashout_id} - ₦{ngn_amount:.2f} NGN")
                
                return {
                    "success": True,
                    "cashout_id": cashout_id,
                    "usd_amount": float(usd_amount),
                    "ngn_amount": float(ngn_amount),
                    "payout_id": payout_result.get('payout_id'),
                    "status": "completed"
                }
                
            else:
                # Payout failed - check error type
                error_msg = payout_result.get('error', 'Unknown Fincra error') if payout_result else 'Fincra API returned no response'
                
                logger.error(f"❌ FINCRA_PAYOUT_FAILED: {cashout_id} - {error_msg}")
                
                # Check for insufficient funds error
                if payout_result and ('insufficient' in error_msg.lower() or 'no_enough_money' in error_msg.lower()):
                    # Route to admin for funding
                    return await cls._finalize_user_visible_success_and_notify_admin(
                        cashout=cashout,
                        reason="fincra_insufficient_funds",
                        unified_tx_id=unified_tx_id,  # type: ignore[arg-type]
                        session=session,
                        user_id=user_id,
                        amount=float(ngn_amount),
                        currency="NGN",
                        destination=f"{bank_account.bank_name} - {bank_account.account_number}",
                        error_details={"error": error_msg, "usd_amount": float(usd_amount)}
                    )
                else:
                    # Other Fincra errors - mark as failed and create retry log
                    cashout.status = CashoutStatus.FAILED.value
                    cashout.error_message = error_msg
                    cashout.failed_at = datetime.utcnow()
                    session.commit()
                    
                    # Create retry log for technical errors
                    if unified_tx_id:
                        try:
                            async with AsyncSessionLocal() as async_session:
                                await cls._create_unified_retry_log_entry(
                                    unified_transaction_id=unified_tx_id,
                                    error_code="FINCRA_API_ERROR",
                                    error_message=error_msg,
                                    external_provider="fincra",
                                    session=async_session
                                )
                                await async_session.commit()
                        except Exception as retry_error:
                            logger.error(f"Failed to create retry log: {retry_error}")
                    
                    return {
                        "success": False,
                        "cashout_id": cashout_id,
                        "error": error_msg
                    }
                    
        except Exception as payout_error:
            logger.error(f"❌ PAYOUT_EXCEPTION: {cashout_id} - {payout_error}")
            
            # Route to admin on exceptions
            return await cls._finalize_user_visible_success_and_notify_admin(
                cashout=cashout,
                reason="payout_exception",
                unified_tx_id=unified_tx_id,  # type: ignore[arg-type]
                session=session,
                user_id=user_id,
                amount=float(ngn_amount),
                currency="NGN",
                destination=f"{bank_account.bank_name} - {bank_account.account_number}",
                error_details={"error": str(payout_error), "usd_amount": float(usd_amount)}
            )

    @classmethod
    def confirm_and_hold_cashout(
        cls, cashout_id: int, user_id: int, session: Session = None  # type: ignore[arg-type]
    ) -> Dict[str, Any]:
        """
        ATOMIC CASHOUT HOLD PLACEMENT
        
        Critical function that atomically:
        1. Locks cashout and wallet records using SELECT ... FOR UPDATE
        2. Validates cashout belongs to user (security)
        3. Checks and validates available balance
        4. Creates WalletHold record
        5. Creates CASHOUT_HOLD transaction
        6. Moves funds from available_balance to frozen_balance
        7. Updates cashout status to PENDING (ready for auto-processing)
        8. Commits ALL changes atomically
        
        Args:
            cashout_id: The cashout ID to confirm and place hold for
            user_id: The user ID requesting confirmation (security check)
            session: Optional database session (creates one if not provided)
            
        Returns:
            Dict with success status, hold details, and cashout status
        """
        from decimal import Decimal
        from datetime import datetime
        
        # Session management
        session_provided = session is not None
        if not session:
            session = SyncSessionLocal()
        
        try:
            # Step 1: Use SELECT ... FOR UPDATE for concurrency safety
            logger.info(f"🔒 CONFIRM_HOLD: Starting atomic hold placement for cashout {cashout_id}, user {user_id}")
            
            # Lock cashout record first
            cashout = session.query(Cashout).filter(
                Cashout.id == cashout_id
            ).with_for_update().first()
            
            if not cashout:
                logger.warning(f"❌ CONFIRM_HOLD_VALIDATION: Cashout {cashout_id} not found")
                return {
                    "success": False,
                    "error": "Cashout not found",
                    "error_code": "CASHOUT_NOT_FOUND"
                }
            
            # Step 2: Validate cashout belongs to user (security check)
            if cashout.user_id != user_id:
                logger.warning(f"🚨 CONFIRM_HOLD_SECURITY: User {user_id} attempted to confirm cashout {cashout_id} belonging to user {cashout.user_id}")
                return {
                    "success": False,
                    "error": "Unauthorized: Cashout does not belong to this user",
                    "error_code": "UNAUTHORIZED_ACCESS"
                }
            
            # Step 3: Handle idempotency - check if already processed
            if cashout.status not in [CashoutStatus.USER_CONFIRM_PENDING.value]:
                if cashout.status in [CashoutStatus.ADMIN_PENDING.value, CashoutStatus.PENDING.value]:
                    logger.info(f"✅ CONFIRM_HOLD_IDEMPOTENT: Cashout {cashout_id} already confirmed with status {cashout.status}")
                    return {
                        "success": True,
                        "message": "Cashout already confirmed",
                        "status": cashout.status,
                        "cashout_id": cashout_id,
                        "idempotent": True
                    }
                else:
                    logger.warning(f"❌ CONFIRM_HOLD_STATUS: Invalid status {cashout.status} for confirmation")
                    return {
                        "success": False,
                        "error": f"Cashout cannot be confirmed in {cashout.status} status",
                        "error_code": "INVALID_STATUS",
                        "current_status": cashout.status
                    }
            
            # Step 4: Get and lock user's wallet for balance validation
            wallet = session.query(Wallet).filter(  # type: ignore[arg-type]
                Wallet.user_id == user_id,
                Wallet.currency == cashout.currency or (  # type: ignore[arg-type]
                    cashout.cashout_type == CashoutType.NGN_BANK.value and Wallet.currency == "USD"  # type: ignore[arg-type]
                )
            ).with_for_update().first()
            
            if not wallet:
                logger.error(f"❌ CONFIRM_HOLD_WALLET: No {cashout.currency} wallet found for user {user_id}")
                return {
                    "success": False,
                    "error": f"No {cashout.currency} wallet found",
                    "error_code": "WALLET_NOT_FOUND"
                }
            
            # Step 5: Revalidate sufficient available balance at confirmation time
            amount_decimal = Decimal(str(cashout.amount))
            available_balance = wallet.available_balance
            
            if available_balance < amount_decimal:
                logger.warning(f"💸 CONFIRM_HOLD_INSUFFICIENT: User {user_id} has insufficient balance. Available: {available_balance}, Required: {amount_decimal}")
                
                # Send comprehensive admin notification for user insufficient balance
                try:
                    from services.comprehensive_admin_notifications import comprehensive_admin_notifications
                    
                    # Get user data for notification
                    user = session.query(User).filter_by(id=user_id).first()
                    user_data = {
                        'id': user.id,
                        'telegram_id': user.telegram_id,
                        'username': user.username,
                        'first_name': user.first_name,
                        'email': getattr(user, 'email', None)
                    } if user else {}
                    
                    # Create managed task for admin notification
                    from utils.graceful_shutdown import create_managed_task
                    create_managed_task(
                        comprehensive_admin_notifications.send_user_insufficient_balance_alert(
                            cashout_id=cashout.cashout_id,
                            requested_amount=float(Decimal(str(amount_decimal))),
                            available_balance=float(Decimal(str(available_balance))),
                            currency=cashout.currency,
                            user_data=user_data,
                            error_details={"context": "cashout_confirmation"}
                        )
                    )
                    logger.info(f"✅ User insufficient balance admin alert queued for {cashout.cashout_id}")
                    
                except Exception as alert_error:
                    logger.error(f"❌ Failed to send user insufficient balance admin alert for {cashout.cashout_id}: {alert_error}")
                
                return {
                    "success": False,
                    "error": f"Insufficient balance. Available: {available_balance:.2f} {cashout.currency}, Required: {amount_decimal:.2f} {cashout.currency}",
                    "error_code": "INSUFFICIENT_FUNDS",
                    "available_balance": str(available_balance),
                    "required_amount": str(amount_decimal),
                    "currency": cashout.currency
                }
            
            # Step 6: Check if hold already exists (additional idempotency check)
            existing_hold = session.query(WalletHolds).filter(
                WalletHolds.linked_type == "cashout",  # type: ignore[attr-defined]
                WalletHolds.linked_id == str(cashout_id),  # type: ignore[attr-defined]
                WalletHolds.user_id == user_id
            ).first()
            
            if existing_hold:
                logger.info(f"✅ CONFIRM_HOLD_EXISTS: Hold already exists for cashout {cashout_id}")
                # Update cashout status if needed - streamlined approach
                if cashout.status == CashoutStatus.USER_CONFIRM_PENDING.value:
                    cashout.status = CashoutStatus.PENDING.value
                    logger.info(f"⚡ CONFIRM_HOLD_READY: Cashout {cashout_id} ready for processing")
                    
                    if not cashout.otp_verified_at:  # type: ignore[attr-defined]
                        cashout.otp_verified_at = datetime.utcnow()  # type: ignore[attr-defined]
                    
                    session.commit()
                
                return {
                    "success": True,
                    "message": "Hold already exists, cashout confirmed",
                    "status": cashout.status,
                    "cashout_id": cashout_id,
                    "hold_id": existing_hold.id,
                    "idempotent": True
                }
            
            # Step 7: Atomically place the hold
            # Generate unique transaction ID for CASHOUT_HOLD
            hold_txn_id = f"HOLD_{UniversalIDGenerator.generate_transaction_id()}"
            
            # Create WalletHold record
            wallet_hold = WalletHolds(
                user_id=user_id,
                wallet_id=wallet.id,
                currency=cashout.currency,
                amount=amount_decimal,
                purpose="cashout",
                linked_type="cashout",
                linked_id=str(cashout_id),
                status=WalletHoldStatus.HELD.value,
                hold_txn_id=hold_txn_id,
                provider=None,  # Will be set during processing
                external_ref=None  # Will be set during processing
            )
            session.add(wallet_hold)
            session.flush()  # Get hold ID
            
            # Create CASHOUT_HOLD transaction
            hold_transaction = Transaction(
                user_id=user_id,
                transaction_type=TransactionType.CASHOUT_HOLD.value,
                amount=-amount_decimal,  # Negative for debit from available
                currency=cashout.currency,
                reference_id=hold_txn_id,
                description=f"Hold funds for cashout {cashout_id}",
                wallet_id=wallet.id,
                cashout_id=cashout_id,
                metadata={
                    "hold_id": wallet_hold.id,
                    "cashout_id": cashout_id,
                    "hold_type": "cashout_confirmation",
                    "original_amount": str(amount_decimal)
                }
            )
            session.add(hold_transaction)
            
            # Move funds from available to frozen balance
            wallet.frozen_balance = wallet.frozen_balance + amount_decimal
            logger.info(f"💰 CONFIRM_HOLD_TRANSFER: Moved {amount_decimal} {cashout.currency} from available to frozen for user {user_id}")
            
            # Step 8: Update cashout metadata and otp_verified_at
            cashout.metadata = cashout.metadata or {}  # type: ignore
            cashout.metadata.update({  # type: ignore
                "hold_confirmed_at": datetime.utcnow().isoformat(),
                "hold_id": wallet_hold.id,
                "hold_transaction_id": hold_transaction.reference_id,  # type: ignore[attr-defined]
                "confirmation_method": "user_button"
            })
            
            if not cashout.otp_verified_at:  # type: ignore[attr-defined]
                cashout.otp_verified_at = datetime.utcnow()  # type: ignore[attr-defined]
            
            # Step 9: Transition status - streamlined processing approach
            cashout.status = CashoutStatus.PENDING.value
            logger.info(f"⚡ CONFIRM_HOLD_READY: Cashout {cashout_id} ready for automatic processing")
            
            # Commit all changes atomically
            session.commit()
            
            # Step 10: Log success and return
            logger.info(f"✅ CONFIRM_HOLD_SUCCESS: Cashout {cashout_id} confirmed with hold {wallet_hold.id}, status: {cashout.status}")
            
            return {
                "success": True,
                "message": "Cashout confirmed and funds placed on hold",
                "cashout_id": cashout_id,
                "hold_id": wallet_hold.id,
                "amount": str(amount_decimal),
                "currency": cashout.currency,
                "status": cashout.status,
                "requires_admin_approval": False,  # Always false in streamlined approach
                "ready_for_processing": cashout.status == CashoutStatus.PENDING.value
            }
            
        except Exception as e:
            # Rollback on any error
            if session and session.is_active:
                session.rollback()
            
            logger.error(f"❌ CONFIRM_HOLD_ERROR: Exception in confirm_and_hold for cashout {cashout_id}: {e}")
            
            return {
                "success": False,
                "error": f"Failed to confirm cashout: {str(e)}",
                "error_code": "CONFIRM_HOLD_EXCEPTION",
                "cashout_id": cashout_id
            }
            
        finally:
            # Close session if we created it
            if not session_provided and session:
                session.close()

    @classmethod
    async def _process_single_cashout(
        cls,
        cashout: Cashout,
        user_id: int,
        cashout_type: str,
        sync_session: Session,
        stats: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Process a single cashout with proper routing and notifications.
        
        This is the core processing method that:
        1. Validates user settings
        2. Routes to crypto or NGN processing
        3. Handles errors and retries
        4. Sends notifications
        
        Args:
            cashout: The cashout record to process
            user_id: User ID for validation
            cashout_type: Type of cashout (crypto or ngn_bank)
            sync_session: Synchronous database session
            stats: Statistics dictionary to update
            
        Returns:
            Dict with success status and details
        """
        cashout_id = as_str(cashout.cashout_id)
        
        try:
            # Get user record for settings validation
            user = sync_session.query(User).filter(User.id == user_id).first()
            if not user:
                return {"success": False, "error": "User not found"}
            
            # **PHASE 1: Filter and validate by cashout type**
            if cashout_type == CashoutType.NGN_BANK.value:
                # Route to NGN processing
                logger.info(f"🏦 Routing cashout {cashout_id} to NGN processing")
                stats["ngn_processed"] += 1
                
                result = await cls._process_ngn_cashout(cashout, user, sync_session)
                
            elif cashout_type == CashoutType.CRYPTO.value:
                # Route to crypto processing
                logger.info(f"💎 Routing cashout {cashout_id} to crypto processing")
                stats["crypto_processed"] += 1
                
                result = await cls._process_crypto_cashout(cashout, user, sync_session)
                
            else:
                # Unknown cashout type
                error_msg = f"Unknown cashout type: {cashout_type}"
                logger.error(f"❌ {error_msg}")
                return {"success": False, "error": error_msg}
            
            # **PHASE 3: Backend processing complete - wallet handler sends user notification**
            # NOTE: Wallet handler already sends the detailed formatted notification to user
            if result.get("success"):
                pass  # Wallet handler sends notification - no duplicate needed
            else:
                # **PHASE 3: Handle failures and retry logic**
                error_msg = result.get("error", "Unknown error")
                
                # Update retry count and next retry time
                try:
                    cashout_record = sync_session.query(Cashout).filter(
                        Cashout.cashout_id == cashout_id
                    ).first()
                    
                    if cashout_record:
                        retry_count = (cashout_record.retry_count or 0) + 1
                        cashout_record.retry_count = retry_count
                        
                        # Calculate next retry time (exponential backoff: 10, 20, 40 minutes)
                        retry_delay_minutes = min(10 * (2 ** (retry_count - 1)), 60)
                        cashout_record.next_retry_at = datetime.utcnow() + timedelta(minutes=retry_delay_minutes)  # type: ignore[attr-defined]
                        
                        sync_session.commit()
                        
                        # **PHASE 3: Alert admin if retry count exhausted (max 3 retries)**
                        if retry_count >= 3:
                            try:
                                from services.consolidated_notification_service import ConsolidatedNotificationService, NotificationPriority
                                
                                notification_service = ConsolidatedNotificationService()
                                await notification_service.send_admin_alert(
                                    title="⚠️ Cashout Retry Exhausted",
                                    message=(
                                        f"Cashout {cashout_id} has exhausted retries (3/3)\n\n"
                                        f"User: {user_id}\n"
                                        f"Type: {cashout_type}\n"
                                        f"Amount: {cashout.amount}\n"
                                        f"Last Error: {error_msg}\n\n"
                                        f"Manual intervention required."
                                    ),
                                    priority=NotificationPriority.CRITICAL
                                )
                            except Exception as alert_error:
                                logger.error(f"Failed to send retry exhausted alert: {alert_error}")
                
                except Exception as retry_error:
                    logger.error(f"Failed to update retry count for {cashout_id}: {retry_error}")
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Exception processing cashout {cashout_id}: {e}")
            return {"success": False, "error": str(e)}


# ===== WALLET DIRECT WRAPPER FUNCTIONS =====

async def process_ngn_cashout_wrapper(user_id: int, cashout_id: str) -> Dict[str, Any]:
    """
    Wrapper function for processing NGN cashouts from wallet_direct.py
    
    This function bridges the gap between the wallet UI (wallet_direct.py) and 
    the AutoCashoutService for NGN bank transfer processing.
    
    Args:
        user_id: The user ID requesting the cashout
        cashout_id: The cashout ID to process
        
    Returns:
        Dict with success status, NGN amount, and processing details
    """
    logger.info(f"🏦 NGN_CASHOUT_WRAPPER: Processing cashout {cashout_id} for user {user_id}")
    
    try:
        # Use the existing admin cashout processor
        result = await AutoCashoutService.process_approved_cashout(
            cashout_id=cashout_id, 
            admin_approved=True
        )
        
        if result.get("success"):
            # Extract NGN amount from result if available
            ngn_amount = result.get("ngn_amount") or result.get("local_amount")
            
            logger.info(f"✅ NGN_CASHOUT_SUCCESS: Cashout {cashout_id} processed successfully")
            
            return {
                "success": True,
                "cashout_id": cashout_id,
                "ngn_amount": ngn_amount,
                "message": "NGN bank transfer processed successfully",
                "processing_details": result.get("processing_details", {})
            }
        else:
            logger.error(f"❌ NGN_CASHOUT_FAILED: Cashout {cashout_id} processing failed: {result.get('error')}")
            
            return {
                "success": False,
                "cashout_id": cashout_id,
                "error": result.get("error", "Unknown processing error"),
                "error_code": result.get("error_code", "PROCESSING_FAILED")
            }
            
    except Exception as e:
        logger.error(f"❌ NGN_CASHOUT_WRAPPER_EXCEPTION: Error processing cashout {cashout_id}: {e}")
        
        return {
            "success": False,
            "cashout_id": cashout_id,
            "error": f"Failed to process NGN cashout: {str(e)}",
            "error_code": "WRAPPER_EXCEPTION"
        }


async def process_crypto_cashout_wrapper(user_id: int, cashout_id: str) -> Dict[str, Any]:
    """
    Wrapper function for processing crypto cashouts immediately via Kraken API
    
    This function enables truly immediate crypto processing like NGN cashouts,
    using Kraken API for automatic withdrawals with admin fallback for errors.
    
    Args:
        user_id: The user ID requesting the cashout
        cashout_id: The cashout ID to process
        
    Returns:
        Dict with success status, transaction hash, and processing details
    """
    logger.info(f"💎 CRYPTO_CASHOUT_WRAPPER: Processing immediate crypto cashout {cashout_id} for user {user_id}")
    
    try:
        from services.kraken_service import KrakenService
        from services.admin_email_actions import send_crypto_cashout_error_email
        
        session = SyncSessionLocal()
        try:
            # Get cashout details
            cashout = session.query(Cashout).filter_by(cashout_id=cashout_id).first()
            if not cashout:
                return {"success": False, "error": "Cashout not found"}
            
            # Get user details
            user = session.query(User).filter(User.id == cashout.user_id).first()
            if not user:
                return {"success": False, "error": "User not found"}
            
            # Initialize Kraken service
            kraken = KrakenService()
            
            # Process immediate withdrawal via Kraken with idempotency protection
            from utils.universal_id_generator import UniversalIDGenerator
            transaction_id = UniversalIDGenerator.generate_transaction_id()
            
            withdraw_result = await kraken.withdraw_crypto(
                currency=cashout.currency,
                amount=Decimal(str(cashout.net_amount)),
                address=cashout.destination,
                reference=cashout_id,
                session=session,
                cashout_id=cashout_id,
                transaction_id=transaction_id
            )
            
            if withdraw_result.get("success"):
                # Update cashout record with success
                cashout.status = CashoutStatus.SUCCESS.value
                cashout.admin_approved = True  # type: ignore[attr-defined]
                cashout.admin_approved_at = datetime.utcnow()  # type: ignore[attr-defined]
                cashout.completed_at = datetime.utcnow()
                cashout.processed_at = datetime.utcnow()  # type: ignore[attr-defined]
                cashout.external_tx_id = withdraw_result.get("txid")
                cashout.admin_notes = (cashout.admin_notes or "") + "\nProcessing method: automatic_kraken"
                
                # Update wallet balances (release locked funds)
                # NOTE: available_balance was already reduced when the hold was placed
                # We only need to reduce locked_balance to release the hold
                wallet = session.query(Wallet).filter(
                    Wallet.user_id == cashout.user_id,
                    Wallet.currency == "USD"
                ).first()
                
                if wallet and wallet.locked_balance >= cashout.amount:  # type: ignore[attr-defined]
                    wallet.locked_balance -= cashout.amount  # type: ignore[attr-defined]
                
                session.commit()
                
                # Notify admin of cashout completed with full breakdown
                try:
                    asyncio.create_task(
                        admin_trade_notifications.notify_cashout_completed({
                            'cashout_id': cashout_id,
                            'user_id': cashout.user_id,
                            'username': user.username or 'N/A',
                            'first_name': user.first_name or 'Unknown',
                            'last_name': user.last_name or '',
                            'amount': float(cashout.net_amount),
                            'currency': cashout.currency,
                            'usd_amount': float(cashout.amount),  # Original USD amount
                            'crypto_amount': float(cashout.net_amount),  # Crypto amount sent
                            'net_amount': float(cashout.net_amount) if cashout.net_amount else None,
                            'network_fee': float(cashout.network_fee) if cashout.network_fee else 0.0,
                            'cashout_type': 'crypto',
                            'destination': cashout.destination or '',
                            'txid': withdraw_result.get("txid", 'N/A'),
                            'completed_at': datetime.utcnow()
                        })
                    )
                except Exception as notify_error:
                    logger.error(f"Failed to notify admin of cashout completed {cashout_id}: {notify_error}")
                
                logger.info(f"✅ CRYPTO_CASHOUT_SUCCESS: Immediate processing completed for {cashout_id}")
                
                return {
                    "success": True,
                    "cashout_id": cashout_id,
                    "txid": withdraw_result.get("txid"),
                    "amount": f"${cashout.amount:.2f}",
                    "net_amount": f"{cashout.net_amount:.6f} {cashout.currency}",
                    "processing_method": "immediate_kraken",
                    "message": "Crypto withdrawal processed successfully"
                }
            else:
                # Handle Kraken errors with admin notification
                error_msg = withdraw_result.get("error", "Unknown Kraken error")
                error_code = withdraw_result.get("error_code", "UNKNOWN")
                
                logger.warning(f"⚠️ CRYPTO_CASHOUT_ERROR: Kraken processing failed for {cashout_id}: {error_msg}")
                
                # Check if error requires admin intervention
                admin_intervention_errors = [
                    "KRAKEN_ADDR_NOT_FOUND",  # Address needs to be configured on Kraken
                    "API_INSUFFICIENT_FUNDS",  # Kraken wallet insufficient balance
                    "KRAKEN_API_ERROR"        # Other Kraken configuration issues
                ]
                
                if error_code in admin_intervention_errors:
                    # Send admin email with action buttons
                    try:
                        await send_crypto_cashout_error_email(
                            cashout_id=cashout_id,
                            user_email=user.email or "",
                            user_name=user.first_name or "",
                            amount=f"${cashout.amount:.2f}",
                            currency=cashout.currency,
                            address=cashout.destination or "",
                            error_message=error_msg,
                            error_code=error_code
                        )
                        logger.info(f"📧 CRYPTO_ADMIN_EMAIL: Sent admin intervention email for {cashout_id}")
                    except Exception as email_error:
                        logger.error(f"❌ Failed to send admin email for {cashout_id}: {email_error}")
                    
                    # Update cashout to pending admin approval
                    cashout.status = CashoutStatus.PENDING_ADMIN_APPROVAL.value  # type: ignore[attr-defined]
                    cashout.error_message = error_msg
                    session.commit()
                    
                    return {
                        "success": False,
                        "cashout_id": cashout_id,
                        "error": "Processing requires admin intervention",
                        "admin_notified": True,
                        "error_code": error_code,
                        "message": "Admin has been notified to configure Kraken settings"
                    }
                else:
                    # Technical error - use existing retry system
                    cashout.status = CashoutStatus.FAILED.value
                    cashout.error_message = error_msg
                    session.commit()
                    
                    return {
                        "success": False,
                        "cashout_id": cashout_id,
                        "error": error_msg,
                        "error_code": "KRAKEN_TECHNICAL_ERROR",
                        "retry_possible": True
                    }
                    
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"❌ CRYPTO_CASHOUT_EXCEPTION: Cashout {cashout_id} failed with exception - {str(e)}")
        return {
            "success": False,
            "cashout_id": cashout_id,
            "error": f"Crypto processing error: {str(e)}",
            "error_code": "WRAPPER_EXCEPTION"
        }


# Global service instance
auto_cashout_service = AutoCashoutService()


async def process_pending_cashouts(session: Optional[AsyncSession] = None) -> Dict[str, Any]:
    """
    **PHASE 1-3: COMPLETE AUTO-CASHOUT SYSTEM**
    
    Entry point for processing pending auto cashouts from retry engine.
    Implements comprehensive 3-phase auto-cashout processing:
    
    **Phase 1: Core Processing**
    - Queries pending cashouts from database
    - Filters by cashout type (crypto vs NGN)
    - Routes to appropriate processing methods
    - Updates cashout status based on results
    
    **Phase 2: Integration**
    - Uses DBAdvisoryLockService for concurrency protection
    - Implements batch processing (max 10 cashouts per run)
    - Handles timeouts (30s per cashout)
    - Integrates with Fincra for NGN and Kraken for crypto
    
    **Phase 3: Monitoring & Notifications**
    - Sends user notifications on completion
    - Logs detailed processing metrics
    - Tracks success/failure rates
    - Alerts admins on retry exhaustion
    
    Args:
        session: Optional AsyncSession for database operations
        
    Returns:
        Dict with comprehensive processing statistics:
        {
            "processed": int,
            "successful": int,
            "failed": int,
            "skipped": int,
            "crypto_processed": int,
            "ngn_processed": int,
            "locked": int,
            "timeout": int,
            "errors": []
        }
    """
    from utils.db_advisory_locks import DBAdvisoryLockService
    
    # Initialize statistics
    stats = {
        "processed": 0,
        "successful": 0,
        "failed": 0,
        "skipped": 0,
        "crypto_processed": 0,
        "ngn_processed": 0,
        "locked": 0,
        "timeout": 0,
        "errors": []
    }
    
    # Session management
    session_provided = session is not None
    if not session_provided:
        session = AsyncSessionLocal()
    
    try:
        logger.info("🔄 PROCESS_PENDING_CASHOUTS: Starting auto cashout processing")
        
        # **PHASE 1: Query pending cashouts with batch limit**
        # Query cashouts with status='pending' or 'awaiting_processing'
        stmt = select(Cashout).where(
            or_(
                Cashout.status == CashoutStatus.PENDING.value,
                Cashout.status == 'awaiting_processing'
            )
        ).order_by(
            Cashout.created_at.asc()  # Process oldest first
        ).limit(10)  # **PHASE 2: Batch limit of 10**
        
        result = await session.execute(stmt)
        pending_cashouts = result.scalars().all()
        
        logger.info(f"📋 Found {len(pending_cashouts)} pending cashouts to process")
        
        if not pending_cashouts:
            logger.info("✅ PROCESS_PENDING_CASHOUTS: No pending cashouts found")
            return stats
        
        # **PHASE 2: Initialize DB Advisory Lock Service**
        db_lock_service = DBAdvisoryLockService()
        
        # Process each cashout with concurrency protection and timeout
        for cashout in pending_cashouts:
            cashout_id = as_str(cashout.cashout_id)
            user_id = as_int(cashout.user_id)
            cashout_type = as_str(cashout.cashout_type) or CashoutType.CRYPTO.value
            
            # **PHASE 2: Acquire distributed lock to prevent double-processing**
            # Use synchronous session for lock acquisition
            sync_session = SyncSessionLocal()
            try:
                lock_acquired = db_lock_service.acquire_lock(
                    session=sync_session,
                    lock_key=f"cashout_processing_{cashout_id}",
                    timeout_seconds=35,  # Slightly longer than per-cashout timeout
                    is_financial=True  # Financial operation - longer timeout
                )
                
                if not lock_acquired:
                    logger.warning(f"🔒 LOCK_FAILED: Could not acquire lock for cashout {cashout_id}, skipping")
                    stats["locked"] += 1
                    stats["skipped"] += 1
                    continue
                
                logger.info(f"🔐 LOCK_ACQUIRED: Processing cashout {cashout_id} (type: {cashout_type})")
                
                # **PHASE 2: Timeout handling - 30 seconds per cashout**
                try:
                    processing_result = await asyncio.wait_for(
                        AutoCashoutService._process_single_cashout(
                            cashout=cashout,
                            user_id=user_id,
                            cashout_type=cashout_type,
                            sync_session=sync_session,
                            stats=stats
                        ),
                        timeout=30.0  # 30 second timeout per cashout
                    )
                    
                    if processing_result.get("success"):
                        stats["successful"] += 1
                        logger.info(f"✅ SUCCESS: Cashout {cashout_id} processed successfully")
                    else:
                        stats["failed"] += 1
                        error_msg = processing_result.get("error", "Unknown error")
                        stats["errors"].append(f"{cashout_id}: {error_msg}")
                        logger.error(f"❌ FAILED: Cashout {cashout_id} - {error_msg}")
                    
                    stats["processed"] += 1
                    
                except asyncio.TimeoutError:
                    logger.error(f"⏱️ TIMEOUT: Cashout {cashout_id} exceeded 30 second processing limit")
                    stats["timeout"] += 1
                    stats["failed"] += 1
                    stats["processed"] += 1
                    stats["errors"].append(f"{cashout_id}: Processing timeout (>30s)")
                    
                    # Update cashout with timeout error
                    try:
                        cashout_record = sync_session.query(Cashout).filter(
                            Cashout.cashout_id == cashout_id
                        ).first()
                        
                        if cashout_record:
                            cashout_record.status = CashoutStatus.FAILED.value
                            cashout_record.error_message = "Processing timeout - exceeded 30 seconds"
                            cashout_record.retry_count = (cashout_record.retry_count or 0) + 1
                            cashout_record.next_retry_at = datetime.utcnow() + timedelta(minutes=10)  # type: ignore[attr-defined]
                            sync_session.commit()
                    except Exception as update_error:
                        logger.error(f"Failed to update timeout status for {cashout_id}: {update_error}")
                
                finally:
                    # **PHASE 2: Release lock after processing**
                    try:
                        db_lock_service.release_lock(sync_session, f"cashout_processing_{cashout_id}")
                        logger.info(f"🔓 LOCK_RELEASED: Released lock for cashout {cashout_id}")
                    except Exception as lock_error:
                        logger.error(f"Failed to release lock for {cashout_id}: {lock_error}")
                
            finally:
                sync_session.close()
        
        # **PHASE 3: Log comprehensive metrics**
        logger.info(
            f"📊 AUTO_CASHOUT_METRICS: "
            f"Processed={stats['processed']}, "
            f"Successful={stats['successful']}, "
            f"Failed={stats['failed']}, "
            f"Crypto={stats['crypto_processed']}, "
            f"NGN={stats['ngn_processed']}, "
            f"Locked={stats['locked']}, "
            f"Timeout={stats['timeout']}"
        )
        
        # **PHASE 3: Send admin alert if failure rate is high**
        if stats['processed'] > 0:
            failure_rate = (stats['failed'] / stats['processed']) * 100
            if failure_rate > 50:  # Alert if more than 50% failures
                try:
                    from services.consolidated_notification_service import ConsolidatedNotificationService, NotificationPriority
                    
                    notification_service = ConsolidatedNotificationService()
                    await notification_service.send_admin_alert(
                        title="⚠️ High Auto-Cashout Failure Rate",
                        message=f"Auto-cashout processing has {failure_rate:.1f}% failure rate\n\n"
                                f"Processed: {stats['processed']}\n"
                                f"Failed: {stats['failed']}\n"
                                f"Successful: {stats['successful']}\n\n"
                                f"Errors: {', '.join(stats['errors'][:5])}",
                        priority=NotificationPriority.CRITICAL
                    )
                except Exception as alert_error:
                    logger.error(f"Failed to send admin alert: {alert_error}")
        
        logger.info(f"✅ PROCESS_PENDING_CASHOUTS: Completed - {stats}")
        return stats
        
    except Exception as e:
        logger.error(f"❌ PROCESS_PENDING_CASHOUTS_ERROR: {e}")
        stats["errors"].append(f"System error: {str(e)}")
        return stats
        
    finally:
        if not session_provided:
            await session.close()
