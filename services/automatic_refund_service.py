"""
Automatic Refund Service
Systematic refund mechanism for expired/failed orders and rate locks
"""

import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict
from database import SessionLocal
from models import (
    ExchangeOrder, Refund, RefundType, RefundStatus,
    User, Wallet
)
from services.crypto import CryptoServiceAtomic
from services.consolidated_notification_service import consolidated_notification_service
from utils.universal_id_generator import UniversalIDGenerator

logger = logging.getLogger(__name__)


class AutomaticRefundService:
    """Service for automatic detection and processing of expired/failed order refunds"""
    
    @staticmethod
    async def detect_and_process_expired_orders() -> Dict[str, List]:
        """
        🔒 SECURITY: AUTOMATIC REFUNDS DISABLED
        
        This function has been disabled due to security policy: 
        "frozen funds should never automatically return to available balance"
        
        Instead, this detects expired orders and sends admin notifications for manual review.
        Returns summary of detected orders requiring admin attention.
        """
        results = {
            "exchange_orders_detected": [],
            "direct_exchanges_detected": [],
            "expired_rate_locks_detected": [],
            "failed_orders_detected": [],
            "admin_notifications_sent": 0,
            "errors": []
        }
        
        try:
            # 1. Detect expired exchange orders (NO AUTOMATIC REFUNDS)
            exchange_alerts = await AutomaticRefundService._detect_expired_exchange_orders()
            results["exchange_orders_detected"] = exchange_alerts
            
            # 2. Detect expired direct exchanges (NO AUTOMATIC REFUNDS)
            direct_alerts = await AutomaticRefundService._detect_expired_direct_exchanges()
            results["direct_exchanges_detected"] = direct_alerts
            
            # 3. Detect expired rate locks (NO AUTOMATIC REFUNDS)
            rate_lock_alerts = await AutomaticRefundService._detect_expired_rate_locks()
            results["expired_rate_locks_detected"] = rate_lock_alerts
            
            # 4. Detect failed orders stuck in processing (NO AUTOMATIC REFUNDS)
            failed_alerts = await AutomaticRefundService._detect_failed_stuck_orders()
            results["failed_orders_detected"] = failed_alerts
            
            # Send comprehensive admin notification for manual review
            total_alerts = len(exchange_alerts) + len(direct_alerts) + len(rate_lock_alerts) + len(failed_alerts)
            if total_alerts > 0:
                logger.warning(f"🔒 FROZEN_FUNDS_DETECTED: {total_alerts} orders require admin review - automatic refunds DISABLED")
                
                # Send detailed admin notification for manual review
                await consolidated_notification_service.send_admin_alert(
                    title="🔒 FROZEN FUNDS REQUIRING ADMIN REVIEW",
                    message=(
                        f"⚠️ AUTOMATIC REFUNDS DISABLED FOR SECURITY\n\n"
                        f"📊 ORDERS REQUIRING MANUAL REVIEW:\n"
                        f"• Exchange Orders: {len(exchange_alerts)}\n"
                        f"• Direct Exchanges: {len(direct_alerts)}\n"
                        f"• Rate Locks: {len(rate_lock_alerts)}\n" 
                        f"• Failed Orders: {len(failed_alerts)}\n"
                        f"• Total: {total_alerts} orders\n\n"
                        f"🔒 SECURITY POLICY: Frozen funds require manual admin review.\n"
                        f"Please review each case individually before releasing funds."
                    )
                )
                results["admin_notifications_sent"] = 1
            
            return results
            
        except Exception as e:
            logger.error(f"Error in expired order detection: {e}")
            results["errors"].append(str(e))
            return results
    
    @staticmethod
    async def _detect_expired_exchange_orders() -> List[Dict]:
        """🔒 SECURITY: Detect expired exchange orders for admin review (NO AUTOMATIC REFUNDS)"""
        session = SessionLocal()
        refunded_orders = []
        
        try:
            # Find exchange orders that expired more than 1 hour ago without completion
            cutoff_time = datetime.utcnow() - timedelta(hours=1)
            
            expired_orders = (
                session.query(ExchangeOrder)
                .filter(
                    ExchangeOrder.expires_at < cutoff_time,
                    ExchangeOrder.status.in_(["created", "awaiting_deposit", "payment_received"]),
                    ExchangeOrder.completed_at.is_(None)
                )
                .all()
            )
            
            logger.warning(f"🔒 SECURITY: Found {len(expired_orders)} expired exchange orders requiring admin review - automatic refunds DISABLED")
            
            for order in expired_orders:
                try:
                    # 🔒 SECURITY: Only detect and log - NO AUTOMATIC REFUNDS
                    detected_order = {
                        "order_id": order.id,
                        "utid": getattr(order, "utid", ""),
                        "user_id": order.user_id,
                        "amount": getattr(order, "source_amount", 0),
                        "currency": getattr(order, "source_currency", "USD"),
                        "expired_at": order.expires_at,
                        "requires_admin_review": True
                    }
                    refunded_orders.append(detected_order)
                    
                    # Only mark as expired - DO NOT change to "refunded" or credit wallet
                    setattr(order, "status", "expired")
                    setattr(order, "completed_at", datetime.utcnow())
                    
                    logger.warning(f"🔒 FROZEN_FUND_DETECTED: Order {order.id} marked as expired - requires admin review for refund")
                        
                except Exception as order_error:
                    logger.error(f"Error detecting expired exchange order {order.id}: {order_error}")
                    
            session.commit()
            return refunded_orders
            
        except Exception as e:
            logger.error(f"Error processing expired exchange orders: {e}")
            session.rollback()
            return refunded_orders
        finally:
            session.close()
    
    @staticmethod
    async def _detect_expired_direct_exchanges() -> List[Dict]:
        """🔒 SECURITY: Detect expired direct exchanges for admin review (NO AUTOMATIC REFUNDS)"""
        logger.info("🔒 SECURITY: Direct exchange detection disabled - DirectExchange model not available")
        return []
    
    @staticmethod
    async def _detect_expired_rate_locks() -> List[Dict]:
        """🔒 SECURITY: Detect expired rate locks for admin review (NO AUTOMATIC REFUNDS)"""
        # This handles orphaned rate locks that don't have orders
        # Implementation would depend on rate lock service structure
        logger.info("🔒 SECURITY: Detecting expired rate locks for admin review - automatic cleanup DISABLED")
        return []  # Placeholder for rate lock specific detection
    
    @staticmethod
    async def _detect_failed_stuck_orders() -> List[Dict]:
        """🔒 SECURITY: Detect failed stuck orders for admin review (NO AUTOMATIC REFUNDS)"""
        session = SessionLocal()
        refunded_orders = []
        
        try:
            # Find orders stuck in processing for more than 24 hours
            cutoff_time = datetime.utcnow() - timedelta(hours=24)
            
            stuck_orders = (
                session.query(ExchangeOrder)
                .filter(
                    ExchangeOrder.status == "processing",
                    ExchangeOrder.updated_at < cutoff_time,
                    ExchangeOrder.completed_at.is_(None)
                )
                .all()
            )
            
            logger.warning(f"🔒 SECURITY: Found {len(stuck_orders)} stuck processing orders requiring admin review - automatic refunds DISABLED")
            
            for order in stuck_orders:
                try:
                    # Check if order has been stuck without progress
                    time_stuck = datetime.utcnow() - order.updated_at
                    
                    if time_stuck.total_seconds() > 86400:  # 24 hours
                        refund_result = await AutomaticRefundService._process_order_refund(
                            order=order,
                            order_type="stuck_order",
                            refund_reason=f"Order stuck in processing for {time_stuck}",
                            session=session
                        )
                        
                        if refund_result["success"]:
                            refunded_orders.append({
                                "order_id": order.id,
                                "utid": getattr(order, "utid", ""),
                                "user_id": order.user_id,
                                "amount_refunded": refund_result["amount"],
                                "refund_id": refund_result["refund_id"],
                                "stuck_duration": str(time_stuck)
                            })
                            
                            # Update order status to failed
                            setattr(order, "status", "failed")
                            setattr(order, "completed_at", datetime.utcnow())
                    
                except Exception as stuck_error:
                    logger.error(f"Error refunding stuck order {order.id}: {stuck_error}")
                    
            session.commit()
            return refunded_orders
            
        except Exception as e:
            logger.error(f"Error processing stuck orders: {e}")
            session.rollback()
            return refunded_orders
        finally:
            session.close()
    
    @staticmethod
    async def _process_order_refund(order, order_type: str, refund_reason: str, session) -> Dict:
        """
        Process refund for a specific order with comprehensive validation
        """
        try:
            # CRITICAL FIX: Handle different order types with correct field names
            user_id = None
            refund_amount = None
            currency = "USD"
            
            if order_type == "escrow":
                # Escrows have buyer_id and seller_id, need to determine who paid
                user_id = getattr(order, "buyer_id", None)  # Buyer is who paid initially
                refund_amount = getattr(order, "total_amount", None)
            else:
                # Other order types use user_id
                user_id = getattr(order, "user_id", None)
                
                # Determine refund amount based on order type
                if hasattr(order, "source_amount"):
                    refund_amount = Decimal(str(getattr(order, "source_amount", 0)))
                elif hasattr(order, "from_amount"):
                    refund_amount = Decimal(str(getattr(order, "from_amount", 0)))
                elif hasattr(order, "amount"):
                    refund_amount = Decimal(str(getattr(order, "amount", 0)))
            
            if not user_id:
                return {"success": False, "error": "No user_id found"}
            
            # Convert to Decimal if not already
            if refund_amount is not None and not isinstance(refund_amount, Decimal):
                refund_amount = Decimal(str(refund_amount))
            
            if not refund_amount or refund_amount <= 0:
                return {"success": False, "error": "No valid refund amount found"}
            
            # Generate unique refund ID
            refund_id = UniversalIDGenerator.generate_refund_id()
            idempotency_key = f"{order_type}_{getattr(order, 'id', 'unknown')}_{refund_id}"
            
            # Check if refund already processed
            existing_refund = (
                session.query(Refund)
                .filter(Refund.idempotency_key == idempotency_key)
                .first()
            )
            
            if existing_refund:
                logger.warning(f"Refund already processed for {order_type} {getattr(order, 'id', 'unknown')}")
                return {"success": False, "error": "Refund already processed"}
            
            # Get user wallet balance before refund
            wallet = (
                session.query(Wallet)
                .filter(Wallet.user_id == user_id, Wallet.currency == currency)
                .first()
            )
            
            balance_before = Decimal(str(wallet.available_balance)) if wallet else Decimal("0")
            
            # CRITICAL SECURITY FIX: Validate wallet was actually debited for this order
            from utils.wallet_validation import WalletValidator
            
            # Determine what to verify based on order type
            verification_success = False
            if order_type == "escrow":
                # For escrows, validate buyer paid
                is_valid_debit, error_msg = await WalletValidator.validate_wallet_debit_completed(
                    user_id=user_id,
                    escrow_id=getattr(order, 'id', None),
                    expected_amount=refund_amount,
                    session=session
                )
                verification_success = is_valid_debit
            else:
                # For exchange orders, check for corresponding debit transaction
                is_valid_debit, error_msg = await WalletValidator.validate_wallet_debit_completed(
                    user_id=user_id,
                    expected_amount=refund_amount,
                    transaction_types=["wallet_payment", "deposit", "exchange_payment"],
                    session=session
                )
                verification_success = is_valid_debit
            
            if not verification_success:
                logger.error(
                    f"🚨 SECURITY BLOCK: Attempted automatic refund for {order_type} "
                    f"{getattr(order, 'id', 'unknown')} without corresponding debit: {error_msg}"
                )
                return {
                    "success": False, 
                    "error": f"Security validation failed: {error_msg}",
                    "security_block": True
                }
            
            # Process the refund by crediting wallet (ONLY after validation)
            credit_success = CryptoServiceAtomic.credit_user_wallet_atomic(
                user_id=user_id,
                amount=refund_amount,
                currency=currency,
                transaction_type="automatic_refund",
                description=f"Automatic refund: {refund_reason} (Verified debit)",
                session=session  # Use same session for atomicity
            )
            
            if not credit_success:
                return {"success": False, "error": "Failed to credit wallet"}
            
            # Get updated balance
            session.refresh(wallet)
            balance_after = Decimal(str(wallet.available_balance)) if wallet else Decimal("0")
            
            # Create refund record
            refund_record = Refund(
                refund_id=refund_id,
                user_id=user_id,
                refund_type=RefundType.ERROR_REFUND.value,
                amount=refund_amount,
                currency=currency,
                reason=refund_reason,
                transaction_id=getattr(order, "utid", None) or str(getattr(order, "id", "")),
                status=RefundStatus.COMPLETED.value,
                idempotency_key=idempotency_key,
                processed_by="AutomaticRefundService",
                balance_before=balance_before,
                balance_after=balance_after,
                completed_at=datetime.utcnow()
            )
            
            session.add(refund_record)
            session.flush()
            
            # Send comprehensive user notification
            try:
                user = session.query(User).filter(User.id == user_id).first()
                if user:
                    await AutomaticRefundService._send_comprehensive_refund_notification(
                        user=user,
                        refund_amount=refund_amount,
                        currency=currency,
                        refund_reason=refund_reason,
                        refund_id=refund_id,
                        order_type=order_type,
                        order=order
                    )
            except Exception as notification_error:
                logger.error(f"Failed to send refund notification: {notification_error}")
            
            logger.info(f"✅ AUTOMATIC_REFUND_PROCESSED: {refund_id} - ${refund_amount} refunded to user {user_id}")
            
            return {
                "success": True,
                "refund_id": refund_id,
                "amount": refund_amount,
                "currency": currency
            }
            
        except Exception as e:
            logger.error(f"Error processing refund for {order_type}: {e}")
            session.rollback()
            return {"success": False, "error": str(e)}

    @staticmethod
    async def _send_comprehensive_refund_notification(
        user, refund_amount, currency, refund_reason, refund_id, order_type, order
    ):
        """
        Send comprehensive refund notifications via Telegram and Email
        
        Args:
            user: User object with notification details
            refund_amount: Decimal amount refunded
            currency: Currency of refund (USD)
            refund_reason: Reason for the refund
            refund_id: Unique refund identifier
            order_type: Type of order (escrow, exchange, direct_exchange)
            order: Original order object for context
        """
        try:
            from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
            from config import Config
            from datetime import datetime
            
            # Generate contextual refund message based on order type
            refund_context = AutomaticRefundService._generate_refund_context_message(
                order_type=order_type,
                order=order,
                refund_reason=refund_reason
            )
            
            # Create comprehensive Telegram message
            message = "🔄 **Automatic Refund Processed**\n\n"
            message += f"💰 **Amount Refunded:** ${float(refund_amount):,.2f} {currency}\n"
            message += f"📊 **Refund ID:** {refund_id}\n"
            message += f"⏰ **Date:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\n"
            
            # Add context-specific information
            message += f"📝 **What happened:**\n{refund_context}\n\n"
            
            # Add reassurance and next steps
            message += "✅ **Your funds are safe**\n"
            message += f"The ${float(refund_amount):,.2f} has been automatically credited to your USD wallet.\n\n"
            message += "🔍 You can check your updated balance anytime.\n"
            message += "💬 Questions? Our support team is here to help!"
            
            # Create action buttons for user convenience
            keyboard = [
                [InlineKeyboardButton("💰 Check Wallet", callback_data="check_wallet")],
                [
                    InlineKeyboardButton("📞 Support", callback_data="start_support_chat"),
                    InlineKeyboardButton("📋 History", callback_data="transaction_history")
                ]
            ]
            
            # Send Telegram notification if available
            if hasattr(user, 'telegram_id') and user.telegram_id and Config.BOT_TOKEN:
                try:
                    bot = Bot(token=Config.BOT_TOKEN)
                    await bot.send_message(
                        chat_id=user.telegram_id,
                        text=message,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode="Markdown"
                    )
                    logger.info(f"✅ Comprehensive refund notification sent to user {user.id} via Telegram")
                except Exception as telegram_error:
                    logger.error(f"❌ Failed to send Telegram refund notification to user {user.id}: {telegram_error}")
            
            # Send email notification if available
            if hasattr(user, 'email') and user.email:
                try:
                    await AutomaticRefundService._send_refund_email_notification(
                        user_email=user.email,
                        user_name=getattr(user, 'first_name', None) or getattr(user, 'username', 'Valued Customer'),
                        refund_amount=refund_amount,
                        currency=currency,
                        refund_reason=refund_reason,
                        refund_id=refund_id,
                        order_type=order_type,
                        refund_context=refund_context
                    )
                    logger.info(f"✅ Email refund notification sent to user {user.id} at {user.email}")
                except Exception as email_error:
                    logger.error(f"❌ Failed to send email refund notification to user {user.id}: {email_error}")
            
            # Log comprehensive notification completion
            logger.info(f"📧 Comprehensive refund notifications processed for user {user.id} - "
                       f"Refund: ${float(refund_amount):,.2f}, ID: {refund_id}")
                       
        except Exception as e:
            logger.error(f"❌ Error sending comprehensive refund notification: {e}")

    @staticmethod
    def _generate_refund_context_message(order_type: str, order, refund_reason: str) -> str:
        """Generate context-specific refund message based on order type and reason"""
        try:
            if "expired" in refund_reason.lower():
                if order_type == "escrow":
                    return (
                        f"Your escrow trade #{getattr(order, 'escrow_id', 'N/A')} expired without completion. "
                        f"Since you already made payment, we've automatically refunded your funds to ensure "
                        f"they're not locked up indefinitely."
                    )
                elif order_type in ["exchange", "direct_exchange"]:
                    return (
                        "Your cryptocurrency exchange order expired before completion. "
                        "Since you made payment, we've automatically refunded your funds. "
                        "You can create a new exchange order anytime."
                    )
                else:
                    return (
                        "Your order expired before completion, so we've automatically "
                        "refunded your payment to keep your funds safe."
                    )
            
            elif "failed" in refund_reason.lower():
                return (
                    "There was a technical issue processing your order, so we've automatically "
                    "refunded your payment. You can try again, and our team is working to prevent "
                    "similar issues in the future."
                )
            
            elif "cancelled" in refund_reason.lower():
                return (
                    "Your order was cancelled, but we received your payment after the cancellation. "
                    "Don't worry - we've automatically credited the funds to your wallet."
                )
            
            else:
                return (
                    f"We've processed an automatic refund for your order to ensure your funds remain secure. "
                    f"Reason: {refund_reason}"
                )
                
        except Exception as e:
            logger.error(f"Error generating refund context message: {e}")
            return f"We've processed an automatic refund. Reason: {refund_reason}"

    @staticmethod
    async def _send_refund_email_notification(
        user_email: str, user_name: str, refund_amount, currency: str,
        refund_reason: str, refund_id: str, order_type: str, refund_context: str
    ):
        """Send email notification for automatic refunds"""
        try:
            from services.email import email_service
            from datetime import datetime
            
            if not email_service.enabled:
                logger.debug("Email service not enabled, skipping email refund notification")
                return
            
            subject = f"💰 Automatic Refund Processed - ${float(refund_amount):,.2f} Credited"
            
            email_body = f"""
Dear {user_name},

We've processed an automatic refund for you.

REFUND DETAILS:
• Amount: ${float(refund_amount):,.2f} {currency}
• Refund ID: {refund_id}
• Date: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
• Order Type: {order_type.title()}

WHAT HAPPENED:
{refund_context}

YOUR FUNDS ARE SAFE:
✅ The ${float(refund_amount):,.2f} has been automatically credited to your USD wallet
✅ You can check your balance and transaction history in your account
✅ No action is required from you

If you have any questions about this refund, please don't hesitate to contact our support team.

Best regards,
LockBay Support Team

---
This is an automated notification from LockBay Escrow Platform.
Refund Reference: {refund_id}
            """
            
            success = email_service.send_email(
                to_email=user_email,
                subject=subject,
                text_content=email_body
            )
            
            if success:
                logger.info(f"✅ Refund email notification sent to {user_email}")
            else:
                logger.warning(f"⚠️ Failed to send refund email notification to {user_email}")
                
        except Exception as e:
            logger.error(f"❌ Error sending refund email notification: {e}")


# Schedule this service to run every hour
async def run_automatic_refund_check():
    """
    🔒 SECURITY: AUTOMATIC REFUND CHECKS DISABLED
    
    Entry point for frozen fund detection (AUTOMATIC REFUNDS DISABLED)
    This function now only detects and alerts admins - no automatic wallet crediting
    """
    try:
        logger.warning("🔒 SECURITY: Starting frozen fund detection - automatic refund processing DISABLED")
        results = await AutomaticRefundService.detect_and_process_expired_orders()
        
        total_detected = (
            len(results["exchange_orders_detected"]) +
            len(results["direct_exchanges_detected"]) +
            len(results["expired_rate_locks_detected"]) +
            len(results["failed_orders_detected"])
        )
        
        if total_detected > 0:
            logger.warning(f"🔒 FROZEN_FUNDS_DETECTED: {total_detected} orders require admin review - automatic refunds DISABLED")
        else:
            logger.debug("🔒 SECURITY: No frozen funds detected requiring admin review")
            
        return results
        
    except Exception as e:
        logger.error(f"❌ FROZEN_FUND_DETECTION: Failed - {e}")
        return {"errors": [str(e)]}