"""
Admin Funding Action Service
Handles processing of admin funding actions (fund & complete, cancel & refund)
"""

import logging
from typing import Dict, Any
from datetime import datetime

from database import SessionLocal
from models import Cashout, CashoutStatus, User
from services.crypto import CryptoServiceAtomic

logger = logging.getLogger(__name__)


class AdminFundingActionService:
    """Service for processing admin funding actions"""

    @classmethod
    async def fund_and_complete_cashout(cls, cashout_id: str) -> Dict[str, Any]:
        """Admin clicked 'Fund & Complete' - immediately retry cashout after funding"""
        try:
            from database import async_managed_session
            from sqlalchemy import select
            from decimal import Decimal
            import re
            import ast
            
            async with async_managed_session() as session:
                # Find the cashout
                result = await session.execute(
                    select(Cashout).filter_by(cashout_id=cashout_id)
                )
                cashout = result.scalar_one_or_none()
                
                if cashout:
                    await session.refresh(cashout)
                
                if not cashout:
                    logger.error(f"Cashout {cashout_id} not found for funding action")
                    return {
                        "success": False,
                        "error": "Cashout not found"
                    }
                
                # Verify cashout is in SUCCESS status with backend_pending (same as address config retry)
                cashout_status = cashout.status.value if hasattr(cashout.status, 'value') else cashout.status
                if cashout_status != 'success':
                    logger.warning(f"Cashout {cashout_id} not in success status: {cashout_status}")
                    return {
                        "success": False,
                        "error": f"Cashout status '{cashout_status}' is not eligible for retry. Expected 'success' with backend pending."
                    }
                
                # Parse backend_pending metadata from admin_notes
                backend_pending = False
                backend_pending_metadata = None
                if cashout.admin_notes:
                    metadata_match = re.search(r"Metadata: (\{.+\})", cashout.admin_notes, re.DOTALL)
                    if metadata_match:
                        try:
                            metadata_str = metadata_match.group(1)
                            backend_pending_metadata = ast.literal_eval(metadata_str)
                            backend_pending = backend_pending_metadata.get('backend_pending', False)
                        except Exception as parse_error:
                            logger.warning(f"Failed to parse metadata from admin_notes: {parse_error}")
                
                if not backend_pending:
                    return {
                        "success": False,
                        "error": "Cashout is not pending backend completion. Nothing to retry."
                    }
                
                # Get user data for logging
                user_result = await session.execute(
                    select(User).filter_by(id=cashout.user_id)
                )
                user = user_result.scalar_one_or_none()
                user_info = f"@{user.username}" if user and user.username else f"User {cashout.user_id}"
                
                # IMMEDIATE RETRY: Call external service now that admin has funded it
                usd_net_amount = float(cashout.net_amount)
                
                # Extract crypto currency from metadata
                currency = None
                if backend_pending_metadata:
                    currency = (
                        backend_pending_metadata.get('currency') or
                        backend_pending_metadata.get('crypto_currency') or
                        backend_pending_metadata.get('asset') or
                        (backend_pending_metadata.get('technical_details', {}).get('currency')) or
                        (backend_pending_metadata.get('technical_details', {}).get('crypto_currency')) or
                        (backend_pending_metadata.get('technical_details', {}).get('asset'))
                    )
                
                if not currency:
                    return {
                        "success": False,
                        "error": f"No crypto currency found in cashout metadata for {cashout_id}"
                    }
                
                # Extract destination from metadata
                destination = None
                if backend_pending_metadata:
                    destination = (
                        backend_pending_metadata.get('destination') or
                        (backend_pending_metadata.get('technical_details', {}).get('destination'))
                    )
                
                if not destination:
                    destination = cashout.destination_address
                
                if not destination:
                    return {
                        "success": False,
                        "error": "No destination address found in cashout metadata"
                    }
                
                # Save destination to database if missing
                if not cashout.destination_address:
                    cashout.destination_address = destination
                    await session.commit()
                
                # Convert USD to crypto amount
                from services.fastforex_service import fastforex_service
                crypto_usd_rate = await fastforex_service.get_crypto_to_usd_rate(currency.upper())
                if not crypto_usd_rate:
                    return {
                        "success": False,
                        "error": f"Unable to get {currency} exchange rate"
                    }
                
                crypto_amount = Decimal(str(usd_net_amount)) / Decimal(str(crypto_usd_rate))
                amount = float(crypto_amount)
                
                # Generate transaction ID
                transaction_id = cashout.utid or f"ADMIN_FUNDING_{cashout_id}_{int(datetime.utcnow().timestamp())}"
                
                # Call Kraken/Fincra to complete withdrawal NOW (not auto-retry)
                from services.kraken_service import get_kraken_service
                kraken_service = get_kraken_service()
                
                withdrawal_result = await kraken_service.withdraw_crypto(
                    currency=currency,
                    amount=amount,
                    address=destination,
                    cashout_id=cashout_id,
                    session=session,
                    transaction_id=transaction_id,
                    force_fresh=True  # Bypass cache - admin just funded the account
                )
                
                if withdrawal_result.get("success"):
                    # Update cashout with transaction ID
                    cashout.external_tx_id = withdrawal_result.get("refid")
                    cashout.admin_notes = f"Backend completed via admin funding retry at {datetime.utcnow().isoformat()}"
                    cashout.updated_at = datetime.utcnow()
                    await session.commit()
                    
                    logger.info(f"✅ ADMIN_FUNDING_COMPLETE: {cashout_id} completed immediately by admin - {user_info} - TXID: {cashout.external_tx_id}")
                    
                    return {
                        "success": True,
                        "message": "Cashout completed successfully",
                        "status": "completed",
                        "cashout_id": cashout_id,
                        "external_tx_id": cashout.external_tx_id,
                        "amount": amount,
                        "currency": currency,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                else:
                    error_msg = withdrawal_result.get('error', 'Unknown error')
                    logger.error(f"❌ ADMIN_FUNDING_FAILED: {cashout_id} - {error_msg}")
                    return {
                        "success": False,
                        "error": f"Withdrawal failed: {error_msg}"
                    }
                
        except Exception as e:
            logger.error(f"Error processing fund & complete action for {cashout_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    @classmethod
    async def cancel_and_refund_cashout(cls, cashout_id: str) -> Dict[str, Any]:
        """Admin clicked 'Cancel & Refund' - cancel cashout and refund user"""
        try:
            session = SessionLocal()
            try:
                # Find the cashout
                cashout = session.query(Cashout).filter(
                    Cashout.cashout_id == cashout_id
                ).first()
                
                if not cashout:
                    logger.error(f"Cashout {cashout_id} not found for cancellation")
                    return {
                        "success": False,
                        "error": "Cashout not found"
                    }
                
                # Verify cashout is in a status that allows admin cancellation
                # EXPANDED: Allow cancellation of any cashout where external transaction hasn't been sent
                allowed_cancellation_statuses = [
                    CashoutStatus.PENDING_SERVICE_FUNDING.value,
                    CashoutStatus.FAILED.value,
                    CashoutStatus.PENDING_ADDRESS_CONFIG.value,
                    CashoutStatus.ADMIN_PENDING.value,
                    CashoutStatus.SUCCESS.value,  # Can cancel if no external_tx_id (funds debited but not sent)
                    CashoutStatus.PENDING.value,
                    CashoutStatus.AWAITING_RESPONSE.value
                ]
                
                # Additional safety check: Don't allow cancellation if external transaction was sent
                if cashout.status not in allowed_cancellation_statuses:
                    logger.warning(f"Cashout {cashout_id} not in cancellable status: {cashout.status}")
                    return {
                        "success": False,
                        "error": f"Cashout status '{cashout.status}' cannot be cancelled by admin. Cashout has completed externally."
                    }
                
                # CRITICAL: Check if external transaction was already sent
                if cashout.external_tx_id and cashout.status == CashoutStatus.SUCCESS.value:
                    logger.error(f"Cannot cancel {cashout_id} - external transaction already sent: {cashout.external_tx_id}")
                    return {
                        "success": False,
                        "error": f"Cannot cancel - external transaction already sent to blockchain/bank (TX: {cashout.external_tx_id})"
                    }
                
                # Get user data
                user = session.query(User).filter_by(id=cashout.user_id).first()
                user_info = f"@{user.username}" if user and user.username else f"User {cashout.user_id}"
                
                # CRITICAL: Use cashout.amount directly - this is the USD amount debited from user
                # The cashout.amount field contains the actual USD amount that was debited
                refund_amount = float(cashout.amount) if cashout.amount else 0.0
                refund_currency = "USD"
                
                logger.info(f"💰 Refunding ${refund_amount} USD for cashout {cashout_id} (original debit amount)")
                
                # Process refund using synchronous function (admin_funding_actions uses sync session)
                refund_success = CryptoServiceAtomic.credit_user_wallet_simple(
                    user_id=cashout.user_id,
                    amount=refund_amount,
                    description=f"Admin cancellation refund for service funding issue - {cashout_id}"
                )
                
                if not refund_success:
                    logger.error(f"❌ Failed to process refund for {cashout_id}")
                    return {
                        "success": False,
                        "error": "Failed to process refund - please handle manually"
                    }
                
                # Update cashout status to cancelled
                cashout.status = CashoutStatus.CANCELLED.value
                cashout.failed_at = datetime.utcnow()
                cashout.error_message = "Cancelled by admin - service funding issue resolved via refund"
                
                session.commit()
                
                # Notify user about cancellation and refund
                user_notified = "Pending"
                if user:
                    try:
                        await cls._notify_user_funding_cancellation(
                            cashout_id=cashout_id,
                            user_id=cashout.user_id,
                            refund_amount=refund_amount,
                            refund_currency=refund_currency,
                            user_data=user
                        )
                        user_notified = "Sent"
                    except Exception as notification_error:
                        logger.error(f"Failed to notify user about funding cancellation: {notification_error}")
                        user_notified = "Failed"
                else:
                    logger.warning(f"User {cashout.user_id} not found for notification")
                    user_notified = "User not found"
                
                logger.info(f"✅ ADMIN_CANCELLATION: {cashout_id} cancelled and ${refund_amount} {refund_currency} refunded to {user_info}")
                
                return {
                    "success": True,
                    "message": "Cashout cancelled and user refunded successfully",
                    "cashout_id": cashout_id,
                    "user_id": cashout.user_id,
                    "refund_amount": refund_amount,
                    "refund_currency": refund_currency,
                    "user_notified": user_notified,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error processing cancel & refund action for {cashout_id}: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    @classmethod
    async def _notify_user_funding_cancellation(
        cls,
        cashout_id: str,
        user_id: int,
        refund_amount: float,
        refund_currency: str,
        user_data: User
    ):
        """Notify user that their cashout was cancelled due to service funding and they were refunded"""
        try:
            # Get bot application instance
            try:
                from main import get_application_instance
                application = get_application_instance()
                if application:
                    bot = application.bot
                else:
                    logger.error("No application instance available for funding cancellation notifications")
                    return
            except ImportError:
                logger.error("Cannot import application instance for funding cancellation notifications")
                return
            
            # Compact mobile-friendly notification
            telegram_message = f"""
🔄 *Cashout Cancelled*

Cashout ID: `{cashout_id}`
💰 Refunded: *${refund_amount:.2f} {refund_currency}*

Service temporarily unavailable. Your funds are back in your wallet.
            """.strip()
            
            await bot.send_message(chat_id=user_id, text=telegram_message, parse_mode="Markdown")
            logger.info(f"✅ Cancellation notification sent to user {user_id} via Telegram")
            
            # Send email notification as backup channel
            if user_data and user_data.email:
                try:
                    from services.email import EmailService
                    
                    email_service = EmailService()
                    email_service.send_email(
                        to_email=user_data.email,
                        subject=f"Cashout Cancelled & Refunded | {cashout_id}",
                        html_content=f"""
                        <h2>🔄 Cashout Cancelled</h2>
                        <p><strong>Cashout ID:</strong> {cashout_id}</p>
                        <p><strong>Status:</strong> Cancelled - Service Unavailable</p>
                        <h3>💰 Full Refund Processed</h3>
                        <p><strong>Amount:</strong> ${refund_amount:.2f} {refund_currency}</p>
                        <h4>ℹ️ What happened?</h4>
                        <p>The external payment service is temporarily unavailable. Your funds have been safely returned to your wallet balance.</p>
                        <h4>✅ Next Steps</h4>
                        <ul>
                            <li>Your ${refund_amount:.2f} is now available in your wallet</li>
                            <li>You can create a new cashout when ready</li>
                            <li>All funds are secure and accessible</li>
                        </ul>
                        """,
                        text_content=f"""
                        Cashout Cancelled
                        
                        Cashout ID: {cashout_id}
                        Status: Cancelled - Service Unavailable
                        
                        Full Refund Processed: ${refund_amount:.2f} {refund_currency}
                        
                        The external payment service is temporarily unavailable. Your funds have been safely returned to your wallet balance.
                        
                        Your ${refund_amount:.2f} is now available in your wallet. You can create a new cashout when ready.
                        """
                    )
                    logger.info(f"✅ Funding cancellation email sent to {user_data.email}")
                    
                except Exception as email_error:
                    logger.error(f"Failed to send funding cancellation email: {email_error}")
            
            logger.info(f"✅ User {user_id} notified of funding cancellation and refund")
            
        except Exception as e:
            logger.error(f"Failed to notify user {user_id} about funding cancellation: {e}")
            raise