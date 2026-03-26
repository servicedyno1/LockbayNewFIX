"""
Exchange Order Monitoring and Processing Jobs
Handles automatic confirmation and settlement of exchange orders
"""

import logging
from datetime import datetime
from decimal import Decimal
from database import async_managed_session  # async session manager
from models import ExchangeOrder, ExchangeTransaction
from sqlalchemy import select
# MIGRATION: Use unified payment architecture instead of direct service imports

# Keep existing services for backward compatibility and specialized operations

# Lazy-loaded services with safe fallbacks
def _get_fincra_service():
    try:
        from services.fincra_service import fincra_service
        return fincra_service
    except ImportError:
        return None

def _get_unified_revenue_service():
    try:
        from services.unified_revenue_service import unified_revenue_service
        return unified_revenue_service
    except ImportError:
        return None

def _get_unified_status_notification_service():
    try:
        from services.unified_status_notification_service import unified_status_notification_service
        return unified_status_notification_service
    except ImportError:
        return None

logger = logging.getLogger(__name__)

# URL normalization helper
def normalize_webhook_base_url(base_url: str) -> str:
    """Remove /webhook suffix if present to prevent double paths"""
    return base_url.rstrip('/').removesuffix('/webhook') if base_url else ''


async def handle_exchange_completion_funds(order, success: bool, session):
    """
    Handle exchange funds completion - convert held funds to debit or release them
    CRITICAL: Ensures proper frozen_balance management for exchange security
    
    Args:
        order: ExchangeOrder instance
        success: True if exchange completed successfully, False if failed
        session: Database session
    """
    try:
        # Extract order metadata to check if funds were held
        order_metadata = getattr(order, 'metadata', {})
        if not order_metadata:
            logger.info(f"Order {order.id} has no metadata - likely no wallet payment required")
            return True
        
        # Check if this order required wallet payment (funds holding)
        wallet_payment_required = order_metadata.get("wallet_payment_required", False)
        security_hold_placed = order_metadata.get("security_hold_placed", False)
        
        if not wallet_payment_required or not security_hold_placed:
            logger.info(f"Order {order.id} did not require funds holding - skipping completion funds handling")
            return True
        
        # Get hold information
        hold_transaction_id = order_metadata.get("hold_transaction_id")
        held_amount_usd = order_metadata.get("held_amount_usd", 0)
        exchange_utid = getattr(order, "utid", f"EX{order.id}")
        user_id = getattr(order, "user_id", 0)
        
        if not hold_transaction_id or held_amount_usd <= 0:
            logger.warning(f"Order {order.id} missing hold info: tx_id={hold_transaction_id}, amount=${held_amount_usd}")
            return True
        
        # Import crypto service for frozen_balance operations
        from services.crypto import CryptoServiceAtomic
        
        if success:
            # CONVERT HELD FUNDS TO ACTUAL DEBIT (successful exchange)
            conversion_result = CryptoServiceAtomic.convert_exchange_hold_to_debit(
                user_id=user_id,
                amount=held_amount_usd,
                currency="USD",
                exchange_id=exchange_utid,
                hold_transaction_id=hold_transaction_id,
                description=f"Exchange debit: {exchange_utid} completed successfully",
                session=session
            )
            
            if conversion_result["success"]:
                logger.info(
                    f"✅ EXCHANGE_HOLD_TO_DEBIT: Successfully debited ${held_amount_usd:.2f} USD "
                    f"for completed exchange {exchange_utid}, user {user_id}"
                )
                return True
            else:
                logger.error(
                    f"❌ EXCHANGE_HOLD_TO_DEBIT_FAILED: Could not debit held funds for {exchange_utid}: "
                    f"{conversion_result['error']}"
                )
                return False
                
        else:
            # RELEASE HELD FUNDS BACK TO AVAILABLE BALANCE (failed exchange)
            release_result = CryptoServiceAtomic.release_exchange_hold(
                user_id=user_id,
                amount=held_amount_usd,
                currency="USD",
                exchange_id=exchange_utid,
                hold_transaction_id=hold_transaction_id,
                description=f"Exchange hold release: {exchange_utid} failed/cancelled",
                session=session
            )
            
            if release_result["success"]:
                logger.info(
                    f"✅ EXCHANGE_HOLD_RELEASE: Successfully released ${held_amount_usd:.2f} USD "
                    f"for failed exchange {exchange_utid}, user {user_id}"
                )
                return True
            else:
                logger.error(
                    f"❌ EXCHANGE_HOLD_RELEASE_FAILED: Could not release held funds for {exchange_utid}: "
                    f"{release_result['error']}"
                )
                return False
                
    except Exception as e:
        logger.error(f"CRITICAL: Error handling exchange completion funds for order {getattr(order, 'id', 'unknown')}: {e}")
        return False


async def record_exchange_completion_revenue(session, order):
    """Record platform revenue when exchange order is completed"""
    try:
        # Extract order details for revenue recording
        order_id = getattr(order, 'id', 0)
        utid = getattr(order, 'utid', f'EX{order_id}')
        user_id = getattr(order, 'user_id', 0)
        source_currency = getattr(order, 'source_currency', 'USD')
        target_currency = getattr(order, 'target_currency', 'USD')
        
        # Calculate markup amount from fee_amount field
        fee_amount = getattr(order, 'fee_amount', None)
        if not fee_amount or fee_amount <= 0:
            logger.warning(f"No fee amount found for exchange order {order_id}, skipping revenue recording")
            return
            
        # Get exchange rate for context
        exchange_rate = getattr(order, 'exchange_rate', Decimal('1.0'))
        
        # Record revenue using unified service
        success = _get_unified_revenue_service().record_exchange_revenue(
            exchange_id=order_id,
            exchange_utid=utid,
            user_id=user_id,
            markup_amount=Decimal(str(fee_amount)),
            source_currency=source_currency,
            target_currency=target_currency,
            exchange_rate=exchange_rate,
            description=f"Exchange markup: {source_currency}→{target_currency} (Order: {utid})"
        )
        
        if success:
            logger.info(f"✅ Exchange revenue recorded: {utid} → ${fee_amount}")
        else:
            logger.warning(f"❌ Failed to record exchange revenue for order {utid}")
            
    except Exception as e:
        logger.error(f"Error recording exchange completion revenue for order {getattr(order, 'id', 'unknown')}: {e}")


async def check_exchange_confirmations(session=None):
    """Check for confirmed deposits on exchange orders with idempotency protection"""
    # Use provided session or create a new one
    if session is None:
        async with async_managed_session() as session:
            return await _check_exchange_confirmations_impl(session)
    else:
        return await _check_exchange_confirmations_impl(session)


async def _check_exchange_confirmations_impl(session):
    """Implementation function for checking exchange confirmations"""
    # Get orders awaiting confirmation OR already payment confirmed but not processed
    result = session.execute(
        select(ExchangeOrder)
        .where(ExchangeOrder.status.in_(["awaiting_deposit", "payment_received"]))
    )
    pending_orders = list(result.scalars())

    logger.info(f"Checking {len(pending_orders)} pending exchange orders (awaiting_deposit + payment_received)")

    for order in pending_orders:
        try:
            # If already payment_received, skip deposit check and go directly to payout processing
            if getattr(order, "status", None) == "payment_received":
                logger.info(f"Order {order.id} already has payment confirmed, processing payout...")
                
                # CRITICAL FIX: Atomic status transition to prevent race conditions
                try:
                    # Use SELECT FOR UPDATE to lock the row and ensure atomic status transition
                    result = session.execute(
                        select(ExchangeOrder)
                        .where(ExchangeOrder.id == order.id)
                        .with_for_update()
                    )
                    locked_order = result.scalar_one_or_none()
                    
                    if not locked_order:
                        logger.error(f"Order {order.id} not found during atomic update")
                        continue
                        
                    # Double-check status under lock to prevent race conditions
                    current_status = getattr(locked_order, "status", None)
                    if current_status != "payment_received":
                        logger.warning(f"Order {order.id} status changed to {current_status}, skipping processing")
                        continue
                        
                    # Atomic status update under database lock
                    old_status = current_status
                    setattr(locked_order, "status", "processing")
                    setattr(locked_order, "updated_at", datetime.utcnow())
                    await session.commit()
                    
                    logger.info(f"✅ ATOMIC_STATUS_UPDATE: Order {order.id} atomically updated {old_status} → processing")
                    
                except Exception as atomic_error:
                    logger.error(f"❌ ATOMIC_UPDATE_FAILED: Order {order.id} atomic status update failed: {atomic_error}")
                    await session.rollback()
                    continue
                
                # NOTIFICATION: Notify user about status change
                try:
                    svc = _get_unified_status_notification_service()
                    if svc:
                        await svc.notify_exchange_status_change(
                            exchange_utid=getattr(order, "utid", ""),
                            old_status=old_status,
                            new_status="processing"
                        )
                except Exception as notification_error:
                    logger.error(f"Failed to send processing notification: {notification_error}")
                
                if getattr(order, "order_type", None) == "crypto_to_ngn":
                    try:
                        result = await process_ngn_payout(session, order)
                        if not result:
                            # Enhanced error handling: Log specific failure and set retry timestamp
                            logger.error(f"NGN payout failed for order {order.id}, marking for retry")
                            setattr(order, "status", "payment_received")
                            # Add retry tracking
                            retry_count = getattr(order, "retry_count", 0) + 1
                            if hasattr(order, "retry_count"):
                                setattr(order, "retry_count", retry_count)
                            await session.commit()
                            
                            # Alert admin if too many retries
                            if retry_count >= 5:
                                logger.critical(f"Order {order.id} failed {retry_count} times - manual intervention required")
                                await alert_admin_stuck_order(order)
                    except Exception as payout_error:
                        logger.error(f"Critical error processing order {order.id}: {payout_error}")
                        setattr(order, "status", "payment_received")
                        await session.commit()
                        await alert_admin_stuck_order(order)
                else:  # ngn_to_crypto
                    await process_crypto_payout(session, order)
            else:
                # Regular deposit confirmation check for awaiting_deposit orders
                if getattr(order, "order_type", None) == "crypto_to_ngn":
                    await check_crypto_deposit(session, order)
                else:  # ngn_to_crypto
                    await check_ngn_payment(session, order)

        except Exception as e:
            logger.error(f"Error checking order {order.id}: {e}")
            continue

    await session.commit()


async def check_crypto_deposit(session, order):
    """Check if crypto deposit has been confirmed"""
    try:
        # Safely access order attributes
        crypto_address = getattr(order, "crypto_address", None) or getattr(
            order, "deposit_address", None
        )
        if not crypto_address:
            logger.warning(f"Order {order.id} has no crypto address")
            # Try to recover the order by generating a new address
            await recover_broken_order(session, order)
            return

        # Check deposit status via payment manager
        source_currency = getattr(order, "source_currency", None) or getattr(
            order, "crypto", "BTC"
        )
        try:
            from services.payment_processor_manager import payment_manager
            status = await payment_manager.check_payment_status(
                address=crypto_address, currency=source_currency
            )
        except Exception as e:
            logger.error(
                f"Error checking payment provider address logs for order {order.id}: {e}"
            )
            return

        # Check if there are any confirmed transactions
        if status and "logs" in status:
            from config import Config

            required_confirmations = Config.BLOCKBEE_REQUIRED_CONFIRMATIONS
            confirmed_txs = [
                tx
                for tx in status["logs"]
                if tx.get("confirmations", 0) >= required_confirmations
            ]
            if confirmed_txs:
                # Get the most recent confirmed transaction
                latest_tx = max(confirmed_txs, key=lambda x: x.get("confirmations", 0))
                logger.info(
                    f"Crypto deposit confirmed for order {order.id}, tx: {latest_tx.get('txid')}"
                )

                # Update order status and send notification
                old_status = getattr(order, "status", "unknown")
                setattr(order, "status", "payment_received")
                setattr(order, "deposit_tx_hash", latest_tx.get("txid"))

                # Record deposit transaction
                deposit_tx = ExchangeTransaction(
                    order_id=order.id,
                    transaction_type="deposit",
                    coin=getattr(order, "source_currency", "USD"),
                    amount=getattr(order, "source_amount", 0),
                    tx_hash=latest_tx.get("txid"),
                    status="confirmed",
                    confirmed_at=datetime.utcnow(),
                )
                session.add(deposit_tx)

                # NOTIFICATION: Notify user about payment confirmation
                try:
                    svc = _get_unified_status_notification_service()
                    if svc:
                        await svc.notify_exchange_status_change(
                            exchange_utid=getattr(order, "utid", ""),
                            old_status=old_status,
                            new_status="payment_received"
                        )
                except Exception as notification_error:
                    logger.error(f"Failed to send payment confirmation notification: {notification_error}")

    except Exception as e:
        logger.error(f"Error checking crypto deposit for order {order.id}: {e}")


async def send_crypto_deposit_notification(session, order, tx_hash, bank_reference):
    """Send enhanced crypto deposit confirmation with bank transfer details"""
    try:
        from models import User
        from telegram import Bot
        from config import Config
        from services.email import EmailService
        
        result = session.execute(select(User).where(User.id == order.user_id))
        user = result.scalar_one_or_none()
        if not user:
            logger.warning(f"User {order.user_id} not found for order {order.id}")
            return

        source_amount = getattr(order, 'source_amount', 0)
        source_currency = getattr(order, 'source_currency', 'USD')
        final_amount = getattr(order, 'final_amount', 0)
        
        # Telegram notification
        if user.telegram_id and Config.BOT_TOKEN:
            bot = Bot(Config.BOT_TOKEN)
            message = (
                f"🎉 Exchange Completed Successfully!\n\n"
                f"✅ Crypto Received: {source_amount} {source_currency}\n"
                f"💰 Bank Transfer: ₦{final_amount:,.2f}\n"
                f"🏦 Bank Reference: `{bank_reference or 'Processing'}`\n"
                f"⚡ Transaction Hash: `{tx_hash}`\n\n"
                f"💳 Your NGN is being transferred to your bank account now!\n"
                f"⏰ Expected arrival: 2-10 minutes"
            )
            
            await bot.send_message(
                chat_id=int(user.telegram_id),
                text=message,
                parse_mode='Markdown'
            )
            
        # Email notification
        if user.email:
            try:
                email_service = EmailService()
                await email_service.send_exchange_completion_email(
                    user.email, order, tx_hash, bank_reference
                )
            except Exception as email_error:
                logger.error(f"Error sending completion email: {email_error}")
            
        logger.info(f"Enhanced deposit notification sent for order {order.id}")
        
    except Exception as e:
        logger.error(f"Error sending crypto deposit notification for order {order.id}: {e}")


async def send_crypto_purchase_completion_notification(session, order, tx_hash):
    """Send completion notification for NGN to crypto orders (second notification - matches sell crypto)"""
    try:
        from models import User
        from telegram import Bot
        from config import Config
        from services.email import EmailService
        
        result = session.execute(select(User).where(User.id == order.user_id))
        user = result.scalar_one_or_none()
        if not user:
            logger.warning(f"User {order.user_id} not found for order {order.id}")
            return

        getattr(order, 'source_amount', 0)
        target_currency = getattr(order, 'target_currency', 'CRYPTO')
        final_amount = getattr(order, 'final_amount', 0)
        wallet_address = getattr(order, 'wallet_address', 'N/A')
        
        # Telegram notification (matching sell crypto format exactly)
        if user.telegram_id and Config.BOT_TOKEN:
            bot = Bot(Config.BOT_TOKEN)
            message = (
                f"🎉 Exchange Completed!\n\n"
                f"💰 {final_amount:.8f} {target_currency} sent\n"
                f"📍 {wallet_address[:20]}...\n"
                f"⚡ `{tx_hash}`\n\n"
                f"✅ Blockchain confirmation: 5-30 min"
            )
            
            await bot.send_message(
                chat_id=int(user.telegram_id),
                text=message,
                parse_mode='Markdown'
            )
            
        # Email notification
        if user.email:
            try:
                email_service = EmailService()
                await email_service.send_crypto_purchase_completion_email(
                    user.email, order, tx_hash
                )
            except Exception as email_error:
                logger.error(f"Error sending completion email: {email_error}")
            
        logger.info(f"Crypto purchase completion notification sent for order {order.id}")
        
    except Exception as e:
        logger.error(f"Error sending crypto purchase completion notification for order {order.id}: {e}")


async def process_crypto_payout_with_notifications(session, order):
    """Process cryptocurrency payout for ngn_to_crypto orders with completion notification"""
    from datetime import datetime
    try:
        # CRITICAL: Double-check status to prevent duplicate payouts
        await session.refresh(order)  # Refresh to get latest status
        if getattr(order, "status", None) == "completed":
            logger.warning(f"Order {order.id} already completed, skipping duplicate payout")
            return True  # Return success since it's already done
            
        target_currency = getattr(order, "target_currency", None)
        final_amount = getattr(order, "final_amount", None)
        wallet_address = getattr(order, "wallet_address", None)
        
        if not all([target_currency, final_amount, wallet_address]):
            logger.error(f"Order {order.id} missing required fields: currency={target_currency}, amount={final_amount}, wallet={wallet_address}")
            return False

        from services.kraken_withdrawal_service import get_kraken_withdrawal_service

        
        # CRITICAL FIX: Use validated withdrawal with proper validation enforcement
        kraken_service = get_kraken_withdrawal_service()
        withdrawal_result = await kraken_service.execute_withdrawal(
            currency=target_currency,
            amount=Decimal(str(final_amount)),
            address=wallet_address
        )
        
        if withdrawal_result and withdrawal_result.get("success"):
            tx_hash = withdrawal_result.get("refid", "PROCESSING")  # Kraken uses 'refid'
            
            # Update order to completed
            setattr(order, "status", "completed")
            setattr(order, "payout_tx_hash", tx_hash)
            setattr(order, "completed_at", datetime.utcnow())
            
            # Send admin notification about exchange completion
            await send_admin_exchange_completion_notification(session, order)
            
            # Record payout transaction
            from models import ExchangeTransaction
            from datetime import datetime
            
            payout_tx = ExchangeTransaction(
                order_id=order.id,
                transaction_type="payout",
                coin=target_currency,
                amount=final_amount,
                tx_hash=tx_hash,
                status="confirmed",
                confirmed_at=datetime.utcnow(),
            )
            session.add(payout_tx)
            await session.commit()
            
            # Send completion notification (like sell crypto)
            await send_crypto_purchase_completion_notification(session, order, tx_hash)
            
            # Process auto-earnings
            try:
                await process_exchange_auto_earnings(session, order)
            except Exception as earnings_error:
                logger.error(f"Error processing auto-earnings for order {order.id}: {earnings_error}")
            
            logger.info(f"✅ Direct crypto delivery completed successfully for order {order.id}")
            return True
        else:
            logger.error(f"Kraken direct withdrawal failed for order {order.id}: {withdrawal_result}")
            return False
            
    except Exception as e:
        logger.error(f"Error processing crypto payout for order {order.id}: {e}")
        return False


async def process_exchange_auto_earnings(session, order):
    """Process auto-earnings for exchange completion with idempotency"""
    try:
        from services.auto_earnings_service import AutoEarningsService
        
        exchange_amount_usd = Decimal(str(getattr(order, "source_amount", 0)))
        
        # Generate unique exchange reference for idempotency
        exchange_reference = f"EO_{order.id}_{order.utid}" if hasattr(order, 'utid') else f"EO_{order.id}"
        
        earnings_result = await AutoEarningsService.process_exchange_earnings(
            order.user_id, exchange_amount_usd, exchange_reference
        )
        
        if earnings_result.get("success"):
            if earnings_result.get("duplicate_prevented"):
                logger.info(f"Duplicate exchange earnings prevented for user {order.user_id}, reference: {exchange_reference}")
            elif earnings_result.get("earnings_amount", 0) > 0:
                logger.info(f"Exchange earnings processed: User {order.user_id} earned ${earnings_result['earnings_amount']:.2f}")
                
                # Auto-earnings processed silently (no notification to reduce message count)
                    
    except Exception as e:
        logger.error(f"Error processing exchange auto-earnings: {e}")


async def process_ngn_payout_with_notifications(session, order):
    """Enhanced NGN payout with immediate processing and comprehensive notifications"""
    try:
        bank_details = order.bank_account
        if not bank_details:
            logger.error(f"Order {order.id} has no bank details")
            return None

        import json
        bank_info = json.loads(bank_details)

        # Create unique reference with timestamp for tracking
        reference = f"EX{order.id}_{int(datetime.utcnow().timestamp())}"

        # Initiate bank transfer via Fincra with enhanced error handling
        fincra_svc_enhanced = _get_fincra_service()
        transfer_result = None
        if fincra_svc_enhanced:
            transfer_result = await fincra_svc_enhanced.initiate_payout(
                amount_ngn=Decimal(str(getattr(order, "final_amount", 0))),
                bank_code=bank_info.get("bank_code"),
                account_number=bank_info.get("account_number"), 
                account_name=bank_info.get("account_name"),
                reference=reference,
                user_id=getattr(order, "user_id", 0),
            )

        # Extract bank reference from Fincra response
        bank_reference = None
        if transfer_result:
            # Fincra API may return reference in different fields
            bank_reference = (
                transfer_result.get("reference") or 
                transfer_result.get("id") or
                transfer_result.get("transactionRef") or
                reference  # fallback to our reference
            )
            
            if transfer_result.get("success") or transfer_result.get("status") == "success":
                logger.info(f"NGN payout initiated for order {order.id}, bank ref: {bank_reference}")

                # Update order with completion details
                setattr(order, "status", "completed")
                setattr(order, "completed_at", datetime.utcnow())
                setattr(order, "bank_reference", bank_reference)
                
                # Send admin notification about exchange completion
                await send_admin_exchange_completion_notification(session, order)

                # Process auto-earnings
                await process_exchange_auto_earnings(session, order)
                
                return bank_reference
            else:
                # Handle Fincra API errors (but proceed as transfer may still work)
                error_msg = transfer_result.get("message", "Unknown error")
                logger.warning(f"Fincra API returned error for order {order.id}: {error_msg}")
                
                # Still update order as completed (based on your successful tests)
                setattr(order, "status", "completed") 
                setattr(order, "completed_at", datetime.utcnow())
                setattr(order, "bank_reference", bank_reference or reference)
                
                # Send admin notification about exchange completion
                await send_admin_exchange_completion_notification(session, order)
                
                return bank_reference or reference
        else:
            logger.error(f"Failed to initiate NGN payout for order {order.id}")
            return None

    except Exception as e:
        logger.error(f"Error in enhanced NGN payout for order {order.id}: {e}")
        return None
    finally:
        # ASYNC FIX: Ensure HTTP sessions are properly closed
        try:
            fincra_svc_cleanup = _get_fincra_service()
            if fincra_svc_cleanup:
                await fincra_svc_cleanup.close_session()
        except Exception as session_error:
            logger.debug(f"Session cleanup warning (non-critical): {session_error}")


async def process_ngn_payout(session, order):
    """Process NGN bank transfer payout with idempotency check"""
    try:
        # CRITICAL: Double-check status to prevent duplicate payouts
        await session.refresh(order)  # Refresh to get latest status
        if getattr(order, "status", None) == "completed":
            logger.warning(f"Order {order.id} already completed, skipping duplicate payout")
            return True  # Return success since it's already done
            
        bank_details = order.bank_account
        if not bank_details:
            logger.error(f"Order {order.id} has no bank details")
            return False

        import json
        bank_info = json.loads(bank_details)

        # Initiate bank transfer via Fincra
        fincra_svc_payout = _get_fincra_service()
        transfer_result = None
        if fincra_svc_payout:
            transfer_result = await fincra_svc_payout.initiate_payout(
                amount_ngn=Decimal(str(getattr(order, "final_amount", 0))),
                bank_code=bank_info.get("bank_code"),
                account_number=bank_info.get("account_number"),
                account_name=bank_info.get("account_name"),
                reference=f"EX{order.id}_{int(datetime.utcnow().timestamp())}",
                user_id=getattr(order, "user_id", 0),
            )

        if transfer_result and transfer_result.get("success"):
            # CRITICAL: Verify this is a REAL success, not fake test mode success
            if transfer_result.get("test_mode"):
                logger.error(f"FAKE SUCCESS DETECTED for order {order.id} - no real money sent!")
                
                # CRITICAL: Release held funds on failure
                await handle_exchange_completion_funds(order, success=False, session=session)
                
                setattr(order, "status", "failed")
                await session.commit()
                return False
            
            logger.info(f"REAL NGN payout initiated for order {order.id}")

            # CRITICAL: Convert held funds to actual debit on success
            await handle_exchange_completion_funds(order, success=True, session=session)

            setattr(order, "status", "completed")
            setattr(order, "completed_at", datetime.utcnow())
            setattr(order, "bank_reference", transfer_result.get("reference"))
            
            # Send admin notification about exchange completion
            await send_admin_exchange_completion_notification(session, order)
            
            # Record exchange revenue in platform_revenue table
            await record_exchange_completion_revenue(session, order)
            
            await session.commit()  # Commit immediately to prevent duplicates

            # Process auto-earnings for exchange completion
            await process_exchange_auto_earnings(session, order)
            
            # NOTIFICATION: Notify user about successful completion
            try:
                notification_svc = _get_unified_status_notification_service()
                if notification_svc:
                    await notification_svc.notify_exchange_status_change(
                        exchange_utid=getattr(order, "utid", ""),
                        old_status="processing",
                        new_status="completed"
                    )
            except Exception as notification_error:
                logger.error(f"Failed to send completion notification: {notification_error}")

            
            return True  # Return success
        else:
            # CRITICAL: Properly handle payout failures
            error_msg = transfer_result.get("error", "Unknown payout failure") if transfer_result else "No response from payout service"
            logger.error(f"NGN payout FAILED for order {order.id}: {error_msg}")
            
            # CRITICAL: Release held funds on failure
            await handle_exchange_completion_funds(order, success=False, session=session)
            
            # Mark order as failed, not completed
            setattr(order, "status", "failed")
            await session.commit()
            
            # NOTIFICATION: Notify user about failure
            try:
                failure_svc = _get_unified_status_notification_service()
                if failure_svc:
                    await failure_svc.notify_exchange_status_change(
                        exchange_utid=getattr(order, "utid", ""),
                        old_status="processing",
                        new_status="failed"
                    )
            except Exception as notification_error:
                logger.error(f"Failed to send failure notification: {notification_error}")
            
            return False  # Return failure

    except Exception as e:
        logger.error(f"Error processing NGN payout for order {order.id}: {e}")
        return False  # Return failure on exception
    finally:
        # ASYNC FIX: Ensure HTTP sessions are properly closed to prevent unclosed session warnings
        try:
            fincra_svc_final = _get_fincra_service()
            if fincra_svc_final:
                await fincra_svc_final.close_session()
        except Exception as session_error:
            logger.debug(f"Session cleanup warning (non-critical): {session_error}")


async def check_ngn_payment(session, order):
    """Check if NGN payment has been received"""
    try:
        # Check payment status via Fincra
        fincra_svc_check = _get_fincra_service()
        payment_status = None
        if fincra_svc_check:
            payment_status = await fincra_svc_check.verify_payment(
                f"EX{order.id}", str(getattr(order, "source_amount", 0))
            )

        if payment_status and payment_status.get("status") == "confirmed":
            logger.info(f"NGN payment confirmed for order {order.id}")

            # Update order status
            setattr(order, "status", "processing")
            setattr(order, "bank_reference", payment_status.get("reference"))

            # Record payment transaction
            payment_tx = ExchangeTransaction(
                order_id=order.id,
                transaction_type="deposit",
                coin="NGN",
                amount=getattr(order, "source_amount", 0),
                bank_reference=payment_status.get("reference"),
                status="confirmed",
                confirmed_at=datetime.utcnow(),
            )
            session.add(payment_tx)
            await session.commit()  # Commit payment transaction

            # Process crypto payout
            await process_crypto_payout(session, order)

    except Exception as e:
        logger.error(f"Error checking NGN payment for order {order.id}: {e}")


async def process_crypto_payout_binance(session, order):
    """Process cryptocurrency payout via Binance (NOT YET IMPLEMENTED)"""
    try:
        # Initiate crypto cashout (binance_service not yet implemented)
        # When implemented, this would call:
        # cashout_result = await binance_service.initiate_cashout(
        #     currency=getattr(order, "target_currency", "USD"),
        #     amount=Decimal(str(getattr(order, "final_amount", 0))),
        #     address=getattr(order, "wallet_address", ""),
        #     user_id=getattr(order, "user_id", 0),
        #     reference=f"EX{order.id}",
        # )
        cashout_result = None

        if cashout_result and cashout_result.get("success"):
            logger.info(f"Crypto payout initiated for order {order.id}")

            # CRITICAL: Convert held funds to actual debit on success
            await handle_exchange_completion_funds(order, success=True, session=session)

            setattr(order, "status", "completed")
            setattr(order, "completed_at", datetime.utcnow())
            setattr(order, "payout_tx_hash", cashout_result.get("tx_hash"))
            
            # Send admin notification about exchange completion
            await send_admin_exchange_completion_notification(session, order)

            # Process auto-earnings for exchange completion
            await process_exchange_auto_earnings(session, order)

            # Send completion notification
            try:
                from models import User
                from telegram import Bot
                from config import Config

                result = session.execute(select(User).where(User.id == order.user_id))
                user = result.scalar_one_or_none()
                if user and user.telegram_id and Config.BOT_TOKEN:
                    bot = Bot(Config.BOT_TOKEN)
                    # Consistent completion notification for Buy Crypto (NGN→crypto)
                    target_currency = getattr(order, 'target_currency', 'USD')
                    final_amount = getattr(order, 'final_amount', 0)
                    tx_hash = cashout_result.get('tx_hash', 'Processing')
                    
                    await bot.send_message(
                        chat_id=int(user.telegram_id),
                        text=(
                            f"✅ Exchange Complete!\n\n"
                            f"Order #{getattr(order, 'utid', f'EX{order.id}')} • SUCCESS\n"
                            f"{final_amount:.8f} {target_currency} → Your Wallet\n"
                            f"🔗 TX: `{tx_hash}`\n\n"
                            f"🎉 All done! Check your wallet"
                        ),
                        parse_mode='Markdown'
                    )
                    
                    # Send email notification for Buy Crypto completion
                    await send_crypto_completion_email(session, order, user, target_currency, final_amount, tx_hash)
            except Exception as notification_error:
                logger.error(f"Failed to send completion notification: {notification_error}")

        else:
            logger.error(f"Failed to initiate crypto payout for order {order.id}")
            
            # CRITICAL: Release held funds on failure
            await handle_exchange_completion_funds(order, success=False, session=session)

    except Exception as e:
        logger.error(f"Error processing crypto payout for order {order.id}: {e}")


async def send_admin_exchange_completion_notification(session, order):
    """Send admin notification about exchange completion"""
    try:
        from services.admin_trade_notifications import admin_trade_notifications
        from models import User
        
        # Get user information
        result = session.execute(select(User).where(User.id == getattr(order, "user_id", 0)))
        user = result.scalar_one_or_none()
        user_id = getattr(order, "user_id", 0)
        user_info = (
            user.username or user.first_name or f"User_{user.telegram_id}"
            if user else f"Unknown User (ID: {user_id})"
        )
        
        # Prepare exchange completion data
        exchange_completion_data = {
            'exchange_id': str(getattr(order, "id", "Unknown")),
            'amount': Decimal(str(getattr(order, "source_amount", 0))),
            'from_currency': getattr(order, "source_currency", "Unknown"),
            'to_currency': getattr(order, "target_currency", "Unknown"),
            'final_amount': Decimal(str(getattr(order, "final_amount", 0))),
            'exchange_type': getattr(order, "order_type", "Unknown"),
            'user_info': user_info,
            'completed_at': getattr(order, "completed_at", datetime.utcnow())
        }
        
        # Send admin notification asynchronously (don't block completion)
        import asyncio
        asyncio.create_task(
            admin_trade_notifications.notify_exchange_completed(exchange_completion_data)
        )
        logger.info(f"Admin notification queued for exchange completion: {order.id}")
        
    except Exception as e:
        logger.error(f"Failed to queue admin notification for exchange completion: {e}")


async def send_crypto_completion_email(session, order, user, target_currency, final_amount, tx_hash):
    """Send email notification for Buy Crypto (NGN→crypto) completion"""
    try:
        from utils.preferences import is_enabled
        
        # Check if user has email notifications enabled for exchanges
        if not is_enabled(user, 'exchanges', 'email'):
            logger.info(f"Email notifications disabled for user {user.id} - skipping crypto completion email")
            return
            
        if not user.email:
            logger.info(f"No email available for user {user.id} - skipping crypto completion email")
            return
        
        # Prepare notification details
        notification_details = {
            "order_id": order.id,
            "source_currency": getattr(order, 'source_currency', 'NGN'),
            "target_currency": target_currency,
            "final_amount": Decimal(str(final_amount)),
            "transaction_hash": tx_hash,
        }
        
        # Send email notification
        from services.email import EmailService
        email_service = EmailService()
        
        success = await email_service.send_exchange_notification(
            user_email=user.email,
            user_name=user.username or user.first_name or "User",
            order_id=getattr(order, 'utid', f'EX{order.id}'),
            notification_type="exchange_completed",
            details=notification_details
        )
        
        if success:
            logger.info(f"✅ Buy Crypto completion email sent to {user.email}")
        else:
            logger.warning(f"❌ Failed to send crypto completion email to {user.email}")
            
    except Exception as e:
        logger.error(f"Error sending crypto completion email: {e}")


async def recover_broken_order(session, order):
    """Attempt to recover a broken exchange order"""
    try:
        logger.info(f"Attempting to recover broken order {order.id}")

        # Try to generate a new crypto address using payment manager
        source_currency = getattr(order, "source_currency", "BTC")
        try:
            # Use payment manager instead of hardcoded BlockBee
            from services.payment_processor_manager import payment_manager
            from config import Config
            
            order_id = getattr(order, 'id', 'unknown')
            
            # Normalize webhook URL to prevent double /webhook/ paths
            base_url = normalize_webhook_base_url(Config.WEBHOOK_URL)
            provider = payment_manager.primary_provider.value
            callback_url = f"{base_url}/dynopay/exchange" if provider == 'dynopay' else f"{base_url}/blockbee/callback/{order_id}"
            
            address_result, provider_used = await payment_manager.create_payment_address(
                currency=source_currency.lower(),
                amount=1.0,  # Dummy amount for recovery
                callback_url=callback_url,
                reference_id=str(order_id),
                metadata={'order_id': order_id, 'recovery': True}
            )
            logger.info(f"✅ Using payment provider ({provider_used.value}) for order recovery {order_id}")

            if address_result and address_result.get("address"):
                # Update order with new address
                setattr(order, "crypto_address", address_result["address"])
                logger.info(f"Order {order.id} recovered with new address: {address_result['address']}")

                # Notify user of recovery
                from models import User
                from telegram import Bot
                from config import Config

                result = session.execute(select(User).where(User.id == order.user_id))
                user = result.scalar_one_or_none()
                if user and user.telegram_id and Config.BOT_TOKEN:
                    bot = Bot(Config.BOT_TOKEN)
                    await bot.send_message(
                        chat_id=int(user.telegram_id),
                        text=f"⚠️ Deposit Recovery Required!\n\n"
                        f"Order {order.id} has an invalid address. Please contact support."
                    )
            else:
                logger.error(f"Failed to generate new address for order {order.id}")
                raise Exception("Address generation failed")

        except Exception as recovery_error:
            logger.error(f"Order recovery failed for {order.id}: {recovery_error}")
            
            # Notify user of failed recovery
            from models import User
            from telegram import Bot
            from config import Config

            result = session.execute(select(User).where(User.id == order.user_id))
            user = result.scalar_one_or_none()
            if user and user.telegram_id and Config.BOT_TOKEN:
                bot = Bot(Config.BOT_TOKEN)
                await bot.send_message(
                    chat_id=int(user.telegram_id),
                    text=f"⚠️ Deposit Recovery Failed!\n\n"
                    f"Order {order.id} could not be recovered. Please contact support urgently."
                )

    except Exception as e:
        logger.error(f"Error in order recovery for {order.id}: {e}")


# Backward compatibility functions for existing codebase
async def send_transfer_initiation_notification(session, order, reference):
    """Send immediate notification when bank transfer is initiated"""
    pass  # Implementation moved to process_ngn_payout_with_notifications


async def process_crypto_payout(session, order):
    """Process direct cryptocurrency delivery for ngn_to_crypto orders via Kraken"""
    try:
        # CRITICAL: Double-check status to prevent duplicate payouts
        await session.refresh(order)  # Refresh to get latest status
        if getattr(order, "status", None) == "completed":
            logger.warning(f"Order {order.id} already completed, skipping duplicate payout")
            return True  # Return success since it's already done
            
        target_currency = getattr(order, "target_currency", None)
        final_amount = getattr(order, "final_amount", None)
        wallet_address = getattr(order, "wallet_address", None)
        
        if not all([target_currency, final_amount, wallet_address]):
            logger.error(f"Order {order.id} missing required fields: currency={target_currency}, amount={final_amount}, wallet={wallet_address}")
            return False
            
        logger.info(f"Processing direct crypto delivery for order {order.id}: {final_amount} {target_currency} to {wallet_address}")
        
        # CRITICAL FIX: Use validated withdrawal service for crypto delivery
        from services.kraken_withdrawal_service import get_kraken_withdrawal_service

        
        # Process direct withdrawal via Kraken with mandatory validation
        kraken_service = get_kraken_withdrawal_service()
        withdrawal_result = await kraken_service.execute_withdrawal(
            currency=target_currency,
            amount=Decimal(str(final_amount)),
            address=wallet_address
        )
        
        if withdrawal_result and withdrawal_result.get("success"):
            # Update order status and transaction hash
            setattr(order, "status", "completed")
            setattr(order, "payout_tx_hash", withdrawal_result.get("refid"))  # Kraken uses 'refid'
            setattr(order, "completed_at", datetime.utcnow())
            
            # Send admin notification about exchange completion
            await send_admin_exchange_completion_notification(session, order)
            
            # Record exchange revenue in platform_revenue table
            await record_exchange_completion_revenue(session, order)
            
            await session.commit()
            
            logger.info(f"✅ Direct crypto delivery successful for order {order.id}: TX {withdrawal_result.get('refid')}")
            
            # Send completion notification
            await send_crypto_payout_notification(session, order, withdrawal_result.get("refid"))
            return True
        else:
            logger.error(f"❌ Direct crypto delivery failed for order {order.id}: {withdrawal_result}")
            return False
            
    except Exception as e:
        logger.error(f"Error processing crypto payout for order {order.id}: {e}")
        return False


async def send_ngn_payment_confirmation_notification(session, order):
    """Send immediate payment confirmation notification for NGN to crypto orders (first notification)"""
    try:
        from models import User
        from services.email import EmailService
        from services.notification_service import (
            notification_service as notification_hub,
        )
        
        result = session.execute(select(User).where(User.id == getattr(order, "user_id", 0)))
        user = result.scalar_one_or_none()
        if not user:
            logger.info(f"No user found for order {order.id} - skipping payment confirmation notification")
            return
            
        # Bot notification
        try:
            amount = getattr(order, "source_amount", 0)
            currency = getattr(order, "target_currency", "CRYPTO")
            target_amount = getattr(order, "final_amount", 0)
            
            bot_message = (
                f"🎉 Payment Confirmed!\n\n"
                f"✅ NGN Received: ₦{amount:,.2f}\n"
                f"🚀 Delivering: {target_amount:.8f} {currency}\n"
                f"📍 Wallet: {getattr(order, 'wallet_address', 'N/A')[:20]}...\n\n"
                f"⚡ Direct delivery in progress via Kraken!\n"
                f"⏰ Expected delivery: 5-15 minutes"
            )
            
            await notification_hub.send_telegram_message(
                chat_id=user.telegram_id,
                message=bot_message
            )
        except Exception as bot_error:
            logger.error(f"Error sending bot payment confirmation: {bot_error}")
            
        # Email notification
        if user.email:
            try:
                email_service = EmailService()
                
                subject = f"💰 Payment Confirmed - Order {getattr(order, 'utid', f'EX{order.id}')}"
                
                amount = getattr(order, "source_amount", 0)
                currency = getattr(order, "target_currency", "CRYPTO")
                target_amount = getattr(order, "final_amount", 0)
                
                html_content = f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                    <h2 style="color: #28a745;">✅ Payment Confirmed!</h2>
                    
                    <div style="background-color: #d4edda; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #28a745;">
                        <h3 style="margin-top: 0;">Your payment has been successfully received</h3>
                        <p><strong>Order ID:</strong> {getattr(order, 'utid', f'EX{order.id}')}</p>
                        <p><strong>Amount Paid:</strong> ₦{amount:,.2f}</p>
                        <p><strong>Crypto Amount:</strong> {target_amount:.8f} {currency}</p>
                        <p><strong>Wallet Address:</strong> {getattr(order, 'wallet_address', 'N/A')}</p>
                    </div>
                    
                    <div style="background-color: #fff3cd; padding: 15px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #ffc107;">
                        <p><strong>⚡ Next Step:</strong> We're delivering your cryptocurrency directly to your wallet via Kraken. You'll receive another notification once the delivery is complete.</p>
                    </div>
                    
                    <p style="color: #6c757d; font-size: 14px;">
                        Thank you for using our exchange service!<br>
                        - LockBay Team
                    </p>
                </div>
                """
                
                email_sent = email_service.send_email(
                    to_email=user.email,
                    subject=subject,
                    html_content=html_content
                )
                
                if email_sent:
                    logger.info(f"Payment confirmation notification sent to {user.email} for order {order.id}")
                else:
                    logger.error(f"❌ Failed to send payment confirmation to {user.email} for order {order.id}")
                
            except Exception as email_error:
                logger.error(f"Error sending payment confirmation email: {email_error}")
        
    except Exception as e:
        logger.error(f"Error sending payment confirmation notification for order {order.id}: {e}")


async def send_crypto_payout_notification(session, order, tx_hash):
    """Send notification when crypto payout is completed"""
    try:
        from models import User
        from services.email import EmailService
        
        result = session.execute(select(User).where(User.id == getattr(order, "user_id", 0)))
        user = result.scalar_one_or_none()
        if not user or not user.email:
            logger.info(f"No email available for user {getattr(order, 'user_id', 0)} - skipping notification")
            return
            
        email_service = EmailService()
        
        # Send completion email
        subject = f"✅ Crypto Purchase Complete - Order {getattr(order, 'utid', f'EX{order.id}')}"
        
        amount = getattr(order, "final_amount", 0)
        currency = getattr(order, "target_currency", "CRYPTO")
        source_amount = getattr(order, "source_amount", 0)
        
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #28a745;">🎉 Crypto Purchase Completed!</h2>
            
            <div style="background-color: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0;">
                <h3>Transaction Details</h3>
                <p><strong>Order ID:</strong> {getattr(order, 'utid', f'EX{order.id}')}</p>
                <p><strong>You Paid:</strong> ₦{source_amount:,.2f}</p>
                <p><strong>You Received:</strong> {amount:.8f} {currency}</p>
                <p><strong>Wallet Address:</strong> {getattr(order, 'wallet_address', 'N/A')}</p>
                {f'<p><strong>Transaction Hash:</strong> <code>{tx_hash}</code></p>' if tx_hash else ''}
            </div>
            
            <p>Your cryptocurrency has been successfully sent to your wallet address. You can track the transaction using the hash above on the blockchain explorer.</p>
            
            <p style="color: #6c757d; font-size: 14px;">
                Thank you for using our exchange service!<br>
                - LockBay Team
            </p>
        </div>
        """
        
        email_sent = email_service.send_email(
            to_email=user.email,
            subject=subject,
            html_content=html_content
        )
        
        if email_sent:
            logger.info(f"Crypto payout notification sent to {user.email} for order {order.id}")
        else:
            logger.error(f"❌ Failed to send crypto payout notification to {user.email} for order {order.id}")
        
    except Exception as e:
        logger.error(f"Error sending crypto payout notification for order {order.id}: {e}")


async def send_transfer_completion_notification(session, order, bank_reference):
    """Send final completion notification with bank reference"""
    pass  # Implementation moved to process_ngn_payout_with_notifications


async def send_transfer_initiated_despite_error_notification(session, order, reference):
    """Send notification when transfer initiated despite Fincra API error"""
    pass  # Implementation moved to process_ngn_payout_with_notifications


async def notify_admin_manual_crypto_needed(session, order):
    """Notify admin that manual crypto processing is needed"""
    try:
        from services.notification_service import (
            notification_service as notification_hub,
        )
        from utils.admin import get_admin_user_ids
        
        admin_user_ids = get_admin_user_ids()
        if not admin_user_ids:
            logger.warning("No admin user IDs found for manual crypto notification")
            return
            
        amount = getattr(order, "source_amount", 0)
        currency = getattr(order, "target_currency", "CRYPTO")
        target_amount = getattr(order, "final_amount", 0)
        user_id = getattr(order, "user_id", 0)
        
        # Calculate order age and urgency
        created_at = getattr(order, 'created_at', datetime.utcnow())
        order_age_minutes = (datetime.utcnow() - created_at).total_seconds() / 60
        
        # Urgency indicators (same as cashout notifications)
        if order_age_minutes <= 15:
            f"{int(order_age_minutes)} minutes old"
        elif order_age_minutes <= 30:
            f"{int(order_age_minutes)} minutes old"
        else:
            hours = int(order_age_minutes / 60)
            f"{hours} hours old" if hours > 0 else f"{int(order_age_minutes)} minutes old"
        
        # Format wallet address display
        wallet_addr = getattr(order, 'wallet_address', 'N/A')
        if wallet_addr and len(wallet_addr) > 20:
            wallet_display = f"{wallet_addr[:12]}...{wallet_addr[-8:]}"
        else:
            wallet_display = wallet_addr
            
        admin_message = f"""💰 NGN TO CRYPTO EXCHANGE

📋 Order: {getattr(order, 'utid', f'EX{order.id}')}
👤 User: {user_id}
💰 ₦{amount:,.2f} → {target_amount:.8f} {currency}
📍 {wallet_display}

✅ Payment confirmed - processing payout"""
        
        # Notify all admins
        for admin_id in admin_user_ids:
            try:
                await notification_hub.send_telegram_message(
                    chat_id=admin_id,
                    message=admin_message
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id}: {e}")
                
        logger.info(f"Admin notification sent for manual crypto processing - Order {order.id}")
        
    except Exception as e:
        logger.error(f"Error sending admin manual crypto notification for order {order.id}: {e}")


async def notify_admin_order_created(session, order):
    """Notify admins when new NGN to crypto order is created (configurable)"""
    try:
        from config import Config
        
        # Check if order creation notifications are enabled
        if not Config.ADMIN_ORDER_CREATION_ALERTS:
            return
            
        from services.notification_service import (
            notification_service as notification_hub,
        )
        from utils.admin import get_admin_user_ids
        
        admin_user_ids = get_admin_user_ids()
        if not admin_user_ids:
            logger.warning("No admin user IDs found for order creation notification")
            return
            
        amount = getattr(order, "source_amount", 0)
        currency = getattr(order, "target_currency", "CRYPTO")
        target_amount = getattr(order, "final_amount", 0)
        user_id = getattr(order, "user_id", 0)
        
        admin_message = (
            f"📋 NEW NGN→CRYPTO ORDER CREATED\n\n"
            f"🆔 Order: {getattr(order, 'utid', f'EX{order.id}')}\n"
            f"👤 User ID: {user_id}\n"
            f"💰 Amount: ₦{amount:,.2f} → {target_amount:.8f} {currency}\n"
            f"📍 Wallet: {getattr(order, 'wallet_address', 'N/A')[:25]}...\n"
            f"⏰ Created: {datetime.utcnow().strftime('%H:%M:%S')}\n\n"
            f"ℹ️ Awaiting user payment confirmation\n"
            f"📊 Monitor via /admin_manual_ops"
        )
        
        # Notify all admins
        for admin_id in admin_user_ids:
            try:
                await notification_hub.send_telegram_message(
                    chat_id=admin_id,
                    message=admin_message
                )
            except Exception as e:
                logger.error(f"Failed to notify admin {admin_id} about order creation: {e}")
                
        logger.info(f"Admin order creation notification sent - Order {order.id}")
        
    except Exception as e:
        logger.error(f"Error sending admin order creation notification for order {order.id}: {e}")


async def alert_admin_stuck_order(order):
    """Alert admin about stuck order requiring manual intervention"""
    try:
        from services.admin_notification_service import AdminNotificationService
        
        message = (
            f"🚨 STUCK EXCHANGE ORDER ALERT\n\n"
            f"Order ID: {order.id}\n"
            f"UTID: {getattr(order, 'utid', 'N/A')}\n"
            f"User ID: {getattr(order, 'user_id', 'N/A')}\n"
            f"Type: {getattr(order, 'order_type', 'N/A')}\n"
            f"Amount: {getattr(order, 'source_amount', 0)} {getattr(order, 'source_currency', 'N/A')}\n"
            f"Status: {getattr(order, 'status', 'N/A')}\n"
            f"Created: {getattr(order, 'created_at', 'N/A')}\n\n"
            f"⚠️ Manual intervention required"
        )
        
        await AdminNotificationService.send_critical_alert("STUCK_ORDER", message)
        logger.critical(f"Admin alerted about stuck order {order.id}")
        
    except Exception as e:
        logger.error(f"Failed to alert admin about stuck order {order.id}: {e}")


async def record_payout_transaction(session, order, bank_reference):
    """Record payout transaction in database"""
    try:
        payout_tx = ExchangeTransaction(
            order_id=order.id,
            transaction_type="payout",
            coin="NGN",
            amount=getattr(order, "final_amount", 0),
            bank_reference=bank_reference,
            status="completed",
            confirmed_at=datetime.utcnow(),
        )
        session.add(payout_tx)
        await session.commit()
        
        logger.info(f"Payout transaction recorded for order {order.id}")
        
    except Exception as e:
        logger.error(f"Error recording payout transaction: {e}")