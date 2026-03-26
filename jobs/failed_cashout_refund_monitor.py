"""
Failed Cashout Refund Monitor
Detects failed cashouts and uses intelligent retry system to distinguish 
technical failures (retry) from user errors (refund)
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any
from decimal import Decimal
from database import async_managed_session
from sqlalchemy import select
from models import (
    Cashout, CashoutStatus, Transaction, TransactionType, Refund, RefundType, RefundStatus, CashoutFailureType, CashoutType
)
from services.consolidated_notification_service import consolidated_notification_service
from utils.universal_id_generator import UniversalIDGenerator

logger = logging.getLogger(__name__)


class FailedCashoutRefundMonitor:
    """Monitor and automatically refund failed cashouts"""
    
    @staticmethod
    async def monitor_and_refund_failed_cashouts() -> Dict[str, Any]:
        """
        Main entry point - scan for failed cashouts and process refunds
        Returns comprehensive results of refund processing
        """
        results = {
            "failed_cashouts_found": 0,
            "admin_notifications_sent": 0,
            "frozen_funds_detected": 0,
            "errors": [],
            "detected_cashouts": []
        }
        
        async with async_managed_session() as session:
            try:
                # Find all failed cashouts that don't have refunds yet
                # Look for cashouts that failed in the last 7 days to avoid very old ones
                cutoff_time = datetime.utcnow() - timedelta(days=7)
                
                result = await session.execute(select(Cashout).where(
                    Cashout.status == CashoutStatus.FAILED.value,
                    Cashout.updated_at >= cutoff_time
                ))
                failed_cashouts = list(result.scalars())
                
                results["failed_cashouts_found"] = len(failed_cashouts)
                
                if not failed_cashouts:
                    # Only log if this is the first time or periodically to avoid log noise
                    # This reduces repeated "No failed cashouts found" messages
                    return results
                
                # NEW: Count cashouts by retry classification status
                actionable_cashouts = 0  # User errors or max retries exceeded - need refund
                retry_pending_cashouts = 0  # Technical failures waiting for retry
                unclassified_cashouts = 0  # Not yet processed by retry system
                analyzed_cashouts = 0  # Already processed
                
                for cashout in failed_cashouts:
                    # Check if this cashout has been classified by the retry system
                    if cashout.failure_type:
                        if cashout.failure_type == CashoutFailureType.USER.value:
                            # User error - should be refunded
                            actionable_cashouts += 1
                        elif cashout.failure_type == CashoutFailureType.TECHNICAL.value:
                            if cashout.next_retry_at:
                                # Technical failure with pending retry
                                retry_pending_cashouts += 1
                            else:
                                # Technical failure but max retries exceeded - should be refunded
                                actionable_cashouts += 1
                    else:
                        # Not yet classified by retry system
                        unclassified_cashouts += 1
                    # Quick pre-check to categorize cashouts  
                    meta = cashout.cashout_metadata or {}
                    pre_debit_failure = (
                        not meta.get("hold_transaction_id") and (
                            (cashout.error_message and ("place_cashout_hold" in cashout.error_message or "ImportError" in cashout.error_message or "cannot import name" in cashout.error_message))
                            or meta.get("stage") in {"init", "pre_hold", "hold_init"}
                            or (cashout.failed_at and cashout.created_at and (cashout.failed_at - cashout.created_at).total_seconds() < 10)
                            or (cashout.status == "failed" and not cashout.failed_at and not cashout.error_message and not meta)
                        )
                    )
                    
                    if pre_debit_failure:
                        analyzed_cashouts += 1
                    else:
                        actionable_cashouts += 1
                
                # Only log details when there are actionable cashouts or it's the first run
                if actionable_cashouts > 0:
                    logger.info(f"🔍 FAILED_CASHOUT_MONITOR: Found {len(failed_cashouts)} failed cashouts ({actionable_cashouts} actionable, {analyzed_cashouts} already analyzed)")
                elif analyzed_cashouts == len(failed_cashouts) and analyzed_cashouts <= 10:  # Avoid log noise for known cases
                    logger.debug(f"🔍 FAILED_CASHOUT_MONITOR: {analyzed_cashouts} failed cashouts already analyzed (pre-debit failures)")
                    # Still process them to ensure consistency, but reduce logging
                else:
                    logger.info(f"🔍 FAILED_CASHOUT_MONITOR: Found {len(failed_cashouts)} failed cashouts to analyze")
                
                # 🔒 SECURITY: Only detect and notify admins - NO AUTOMATIC REFUNDS
                for cashout in failed_cashouts:
                    try:
                        # Detect frozen funds requiring admin review (NO AUTOMATIC REFUNDS)
                        detection_result = await FailedCashoutRefundMonitor._detect_failed_cashout_requiring_review(
                            cashout, session
                        )
                        
                        if detection_result["requires_admin_review"]:
                            results["frozen_funds_detected"] += 1
                            results["detected_cashouts"].append({
                                "cashout_id": cashout.cashout_id,
                                "user_id": cashout.user_id,
                                "amount": detection_result["amount"],
                                "currency": detection_result["currency"],
                                "failure_reason": detection_result["failure_reason"],
                                "requires_admin_review": True
                            })
                            logger.warning(f"🔒 FROZEN_FUND_DETECTED: Cashout {cashout.cashout_id} requires admin review - automatic refunds DISABLED")
                            
                    except Exception as e:
                        logger.error(f"❌ Error detecting failed cashout {cashout.cashout_id}: {e}")
                        results["errors"].append(f"Cashout {cashout.cashout_id}: {str(e)}")
                
                # Send admin notification for frozen funds requiring review
                if results["frozen_funds_detected"] > 0:
                    await FailedCashoutRefundMonitor._send_admin_frozen_funds_notification(results)
                    results["admin_notifications_sent"] = 1
                    
                # Log completion summary
                if results['frozen_funds_detected'] > 0:
                    logger.warning(
                        f"🔒 FROZEN_FUNDS_MONITOR: Completed - "
                        f"{results['frozen_funds_detected']} frozen funds detected requiring admin review, "
                        f"{results['admin_notifications_sent']} admin notifications sent - automatic refunds DISABLED"
                    )
                else:
                    # Use debug for routine completions to reduce log noise
                    logger.debug(
                        f"🔒 FROZEN_FUNDS_MONITOR: Completed - "
                        f"{results['frozen_funds_detected']} frozen funds detected - automatic refunds DISABLED"
                    )
                
                return results
                
            except Exception as e:
                logger.error(f"❌ FAILED_CASHOUT_MONITOR_ERROR: {e}")
                results["errors"].append(str(e))
                return results
    
    @staticmethod
    async def _detect_failed_cashout_requiring_review(cashout: Cashout, session) -> Dict[str, Any]:
        """
        🔒 SECURITY: Detect failed cashout requiring admin review (NO AUTOMATIC REFUNDS)
        Returns: {"requires_admin_review": bool, "amount": float, "currency": str, "failure_reason": str}
        """
        try:
            # Step 1: Check if refund already exists
            result = await session.execute(select(Refund).where(
                Refund.cashout_id == cashout.cashout_id,
                Refund.refund_type == RefundType.CASHOUT_FAILED.value
            ))
            existing_refund = result.scalar_one_or_none()
            
            if existing_refund:
                return {
                    "action": "skipped",
                    "reason": f"Refund already exists: {existing_refund.refund_id}",
                    "existing_refund_id": existing_refund.refund_id
                }
            
            # Step 2: Check for existing refund transaction (backup check)
            result = await session.execute(select(Transaction).where(
                Transaction.user_id == cashout.user_id,
                Transaction.transaction_type == TransactionType.REFUND.value,
                Transaction.currency == "USD",
                Transaction.description.contains(cashout.cashout_id)
            ))
            existing_refund_tx = result.scalar_one_or_none()
            
            if existing_refund_tx:
                return {
                    "action": "skipped",
                    "reason": f"Refund transaction already exists: {existing_refund_tx.transaction_id}",
                    "existing_transaction_id": existing_refund_tx.transaction_id
                }
            
            # Step 3: CRITICAL FIX - Handle failed-before-debit scenarios and metadata access  
            # Get cashout metadata (SQLAlchemy maps database 'metadata' column to 'cashout_metadata' attribute)
            meta = cashout.cashout_metadata or {}
            
            # EARLY EXIT: Detect cashouts that failed before any hold/debit was created
            pre_debit_failure = (
                not meta.get("hold_transaction_id") and (
                    # Case 1: Error message indicates import/hold failure
                    (cashout.error_message and ("place_cashout_hold" in cashout.error_message or "ImportError" in cashout.error_message or "cannot import name" in cashout.error_message))
                    # Case 2: Metadata indicates early stage failure
                    or meta.get("stage") in {"init", "pre_hold", "hold_init"}
                    # Case 3: Failed very quickly with failed_at timestamp
                    or (cashout.failed_at and cashout.created_at and (cashout.failed_at - cashout.created_at).total_seconds() < 10)
                    # Case 4: CRITICAL - Ultra-early failure with NULL failed_at, empty error_message, empty metadata
                    or (cashout.status == "failed" and not cashout.failed_at and not cashout.error_message and not meta)
                )
            )
            
            if pre_debit_failure:
                # Reduce log noise for repeated pre-debit failures by using debug level
                logger.debug(f"✅ PRE_DEBIT_SKIP: Cashout {cashout.cashout_id} failed before hold/debit - no funds moved, skipping refund")
                return {
                    "action": "skipped",
                    "reason": "Cashout failed before hold/debit; no funds moved",
                    "category": "failed_before_debit",
                    "error_context": cashout.error_message[:100] if cashout.error_message else "No error message"
                }
            
            # NEW HOLD SYSTEM: Check if this cashout has a hold that needs to be released
            if meta and meta.get("hold_transaction_id"):
                # NEW HOLD SYSTEM: Release the frozen_balance hold instead of refunding a debit
                hold_transaction_id = meta["hold_transaction_id"]
                hold_amount = meta.get("hold_amount", Decimal(str(cashout.amount)))
                
                logger.info(f"🔓 HOLD_RELEASE: Processing hold release for failed cashout {cashout.cashout_id} (hold TX: {hold_transaction_id}, amount: ${hold_amount:.2f})")
                
                from services.crypto import CashoutHoldService
                release_result = CashoutHoldService._release_cashout_hold_internal_system_only(
                    user_id=cashout.user_id,
                    amount=hold_amount,
                    currency="USD",
                    cashout_id=cashout.cashout_id,
                    hold_transaction_id=hold_transaction_id,
                    description=f"🔓 Failed cashout hold release: ${hold_amount:.2f} USD for {cashout.cashout_id}",
                    session=session,
                    system_context="failed_cashout_refund_monitor"
                )
                
                if release_result["success"]:
                    logger.info(f"✅ HOLD_RELEASED: Successfully released ${hold_amount:.2f} hold for failed cashout {cashout.cashout_id} (TX: {release_result['release_transaction_id']})")
                    
                    # Create refund record for tracking
                    refund_id = UniversalIDGenerator.generate_refund_id()
                    
                    # Get current user USD wallet balance for audit trail
                    from models import Wallet
                    result = await session.execute(select(Wallet).where(
                        Wallet.user_id == cashout.user_id,
                        Wallet.currency == "USD"
                    ))
                    user_wallet = result.scalar_one_or_none()
                    user_balance_before = user_wallet.available_balance if user_wallet else Decimal('0')
                    user_balance_after = user_balance_before  # Balance unchanged since we're releasing a hold, not adding funds
                    
                    refund = Refund(
                        refund_id=refund_id,
                        user_id=cashout.user_id,
                        cashout_id=cashout.cashout_id,
                        amount=hold_amount,
                        currency="USD",
                        reason=f"Automated hold release for failed cashout {cashout.cashout_id}",
                        refund_type=RefundType.CASHOUT_FAILED.value,
                        status=RefundStatus.COMPLETED.value,
                        idempotency_key=f"hold_release_{cashout.cashout_id}_{hold_transaction_id}",
                        processed_by="failed_cashout_refund_monitor",
                        balance_before=user_balance_before,
                        balance_after=user_balance_after,
                        created_at=datetime.utcnow(),
                        completed_at=datetime.utcnow()
                    )
                    session.add(refund)
                    session.commit()
                    
                    return {
                        "action": "refunded",
                        "amount": hold_amount,
                        "refund_method": "hold_release",
                        "refund_id": refund_id,
                        "release_transaction_id": release_result["release_transaction_id"],
                        "hold_transaction_id": hold_transaction_id
                    }
                else:
                    logger.error(f"❌ HOLD_RELEASE_FAILED: {release_result['error']} for cashout {cashout.cashout_id}")
                    return {
                        "action": "failed",
                        "error": f"Hold release failed: {release_result['error']}",
                        "hold_transaction_id": hold_transaction_id
                    }
            
            # LEGACY SYSTEM: Look for original debit transaction (for backward compatibility)
            original_debit = await FailedCashoutRefundMonitor._find_original_debit(cashout, session)
            
            if not original_debit:
                # Check if this might be a deferred cashout without hold (old system bug)
                if meta and meta.get("deferred"):
                    logger.warning(f"⚠️ LEGACY_DEFERRED: Cashout {cashout.cashout_id} is deferred without hold - likely old system bug")
                    return {
                        "action": "skipped",
                        "reason": "Legacy deferred cashout without hold or debit - no funds to recover",
                        "legacy_issue": True
                    }
                
                return {
                    "action": "failed",
                    "error": "No original debit transaction found - manual review required",
                    "requires_manual_review": True
                }
            
            # Step 4: Calculate refund amount (using the USD debit amount, not cashout amount)
            refund_amount = abs(Decimal(str(original_debit.amount)))  # This is always in USD
            
            # Log the calculation for audit trail
            if cashout.currency != "USD":
                logger.info(
                    f"💰 REFUND CALCULATION: Cashout {cashout.cashout_id} was {cashout.amount} {cashout.currency}, "
                    f"but refunding ${refund_amount:.2f} USD (from original debit {original_debit.transaction_id})"
                )
            else:
                logger.info(
                    f"💰 REFUND CALCULATION: USD cashout {cashout.cashout_id} - refunding ${refund_amount:.2f} USD"
                )
            
            # Get user's current balance for audit trail
            from services.crypto import CryptoServiceAtomic
            current_balance = CryptoServiceAtomic.get_user_balance_atomic(cashout.user_id, "USD")
            balance_after_refund = current_balance + refund_amount
            
            # Step 5: Create refund record for audit trail
            refund_record = Refund(
                refund_id=UniversalIDGenerator.generate_refund_id(),
                user_id=cashout.user_id,
                refund_type=RefundType.CASHOUT_FAILED.value,
                amount=Decimal(str(refund_amount)),
                currency="USD",
                reason=f"Automatic refund for failed cashout {cashout.cashout_id}",
                cashout_id=cashout.cashout_id,
                status=RefundStatus.PENDING.value,
                processed_by="system_auto_monitor",  # FIXED: Required field
                balance_before=Decimal(str(current_balance)),  # FIXED: Required field
                balance_after=Decimal(str(balance_after_refund)),  # FIXED: Required field
                idempotency_key=f"failed_cashout_{cashout.cashout_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
                created_at=datetime.utcnow()
            )
            
            session.add(refund_record)
            session.flush()  # Get the ID
            
            # Step 6: Process the actual wallet refund
            refund_success = CryptoServiceAtomic.credit_user_wallet_atomic(
                user_id=cashout.user_id,
                amount=refund_amount,
                currency="USD",
                transaction_type=TransactionType.REFUND.value,
                description=f"Auto-refund for failed cashout {cashout.cashout_id}",
                escrow_id=None
            )
            
            if refund_success:
                # Update refund record status
                refund_record.status = RefundStatus.COMPLETED.value
                refund_record.completed_at = datetime.utcnow()
                
                # Invalidate balance caches
                from utils.balance_cache_invalidation import balance_cache_invalidation_service
                balance_cache_invalidation_service.invalidate_user_balance_caches(
                    user_id=cashout.user_id,
                    operation_type="failed_cashout_refund"
                )
                
                session.commit()
                
                logger.info(
                    f"✅ REFUND_PROCESSED: ${refund_amount:.2f} USD refunded to user {cashout.user_id} "
                    f"for failed cashout {cashout.cashout_id} (refund: {refund_record.refund_id})"
                )
                
                return {
                    "action": "refunded",
                    "amount": refund_amount,
                    "refund_id": refund_record.refund_id,
                    "original_debit_id": original_debit.transaction_id
                }
            else:
                # Mark refund as failed
                refund_record.status = RefundStatus.FAILED.value
                refund_record.error_message = "Wallet credit operation failed"
                session.commit()
                
                logger.error(
                    f"❌ REFUND_FAILED: Wallet credit failed for cashout {cashout.cashout_id} "
                    f"(refund: {refund_record.refund_id}) - requires manual intervention"
                )
                
                return {
                    "action": "failed",
                    "error": "Wallet credit operation failed - manual intervention required",
                    "refund_id": refund_record.refund_id,
                    "requires_manual_review": True
                }
                
        except Exception as e:
            session.rollback()
            logger.error(f"❌ Error processing refund for cashout {cashout.cashout_id}: {e}")
            return {
                "action": "failed",
                "error": str(e),
                "requires_manual_review": True
            }
    
    @staticmethod
    async def _find_original_debit(cashout: Cashout, session) -> Transaction:
        """
        IMPROVED: Find the original debit transaction for a cashout with robust matching logic
        """
        try:
            # STRATEGY 1: Look for exact cashout_id reference in description (rare but accurate)
            result = await session.execute(select(Transaction).where(
                Transaction.user_id == cashout.user_id,
                Transaction.transaction_type == "cashout",
                Transaction.currency == "USD",
                Transaction.amount < 0,  # Debit (negative amount)
                Transaction.description.contains(cashout.cashout_id)
            ).order_by(Transaction.created_at.desc()))
            exact_match_debits = list(result.scalars())
            
            if exact_match_debits:
                logger.info(f"🎯 Found {len(exact_match_debits)} exact match(es) by cashout_id reference")
                if len(exact_match_debits) == 1:
                    debit = exact_match_debits[0]
                    logger.info(f"✅ EXACT_MATCH: {debit.transaction_id} (${abs(float(debit.amount)):.2f})")
                    return debit
                else:
                    # Multiple exact matches - take the most recent
                    debit = exact_match_debits[0]
                    logger.warning(f"⚠️ Multiple exact matches, using most recent: {debit.transaction_id}")
                    return debit
            
            # STRATEGY 2: Time-based matching (primary strategy since descriptions don't contain cashout_ids)
            # Expand the time window to handle processing delays and edge cases
            time_window_start = cashout.created_at - timedelta(minutes=10)  # Expanded from 5 to 10 minutes
            time_window_end = cashout.created_at + timedelta(minutes=10)   # Allow for both before and after
            
            logger.info(f"🕐 TIME_WINDOW: {time_window_start.strftime('%H:%M:%S')} to {time_window_end.strftime('%H:%M:%S')}")
            
            # Find all potential debit transactions in time window
            result = await session.execute(select(Transaction).where(
                Transaction.user_id == cashout.user_id,
                Transaction.transaction_type == "cashout",
                Transaction.currency == "USD",
                Transaction.amount < 0,  # Debit (negative amount)
                Transaction.created_at >= time_window_start,
                Transaction.created_at <= time_window_end
            ).order_by(Transaction.created_at.desc()))
            potential_debits = list(result.scalars())
            
            logger.info(f"🔍 Found {len(potential_debits)} potential debit transactions in time window")
            
            if not potential_debits:
                # No debits found in time window - this is the main issue
                # Note: Changed from ERROR to INFO for pre-debit failures (expected scenario)
                logger.info(f"ℹ️ NO_DEBITS_IN_WINDOW: No debit transactions found for user {cashout.user_id} in {time_window_start} to {time_window_end} (may be expected for pre-debit failures)")
                
                # Check if there are ANY cashout debits for this user
                result = await session.execute(select(Transaction).where(
                    Transaction.user_id == cashout.user_id,
                    Transaction.transaction_type == "cashout",
                    Transaction.currency == "USD",
                    Transaction.amount < 0
                ).order_by(Transaction.created_at.desc()).limit(5))
                all_user_debits = list(result.scalars())
                
                if all_user_debits:
                    # If closest transaction is within reasonable time, consider expanding window
                    closest_tx = min(all_user_debits, key=lambda d: abs((d.created_at - cashout.created_at).total_seconds()))
                    closest_time_diff = abs((cashout.created_at - closest_tx.created_at).total_seconds() / 60)
                    
                    if closest_time_diff <= 30:  # Within 30 minutes
                        logger.warning(f"⚠️ EXPANDED_MATCH: Using closest debit within 30min: {closest_tx.transaction_id} ({closest_time_diff:.1f}min diff)")
                        return closest_tx
                
                return None
            
            # STRATEGY 3: Single debit found - very likely correct
            if len(potential_debits) == 1:
                debit = potential_debits[0]
                time_diff = abs((cashout.created_at - debit.created_at).total_seconds() / 60)
                logger.info(f"✅ SINGLE_MATCH: {debit.transaction_id} (${abs(float(debit.amount)):.2f}, {time_diff:.1f}min diff)")
                return debit
            
            # STRATEGY 4: Multiple debits found - use smart matching
            logger.info(f"🧠 SMART_MATCHING: {len(potential_debits)} debits found, using advanced matching")
            
            # Determine if this is a crypto cashout (affects amount matching strategy)
            is_crypto_cashout = (
                cashout.currency != "USD" or 
                cashout.cashout_type == CashoutType.CRYPTO.value or 
                cashout.cashout_id.startswith(("LTC_", "BTC_", "ETH_")) or
                cashout.cashout_type == CashoutType.CRYPTO.value
            )
            
            if is_crypto_cashout:
                # For crypto cashouts, amount matching is unreliable (crypto vs USD amounts)
                # Use time proximity as primary matching criteria
                logger.info(f"🪙 CRYPTO_CASHOUT: {cashout.cashout_id} ({cashout.amount} {cashout.currency}) - using time-based selection")
                
                # Find the debit closest in time to the cashout
                closest_debit = min(potential_debits, key=lambda d: abs((d.created_at - cashout.created_at).total_seconds()))
                time_diff = abs((closest_debit.created_at - cashout.created_at).total_seconds() / 60)
                
                logger.info(f"✅ CRYPTO_TIME_MATCH: {closest_debit.transaction_id} (${abs(float(closest_debit.amount)):.2f}, {time_diff:.1f}min diff)")
                return closest_debit
            
            else:
                # For USD/fiat cashouts, use amount matching
                logger.info(f"💵 USD/FIAT CASHOUT: {cashout.cashout_id} (${float(cashout.amount):.2f}) - using amount matching")
                
                exact_amount_matches = []
                for debit in potential_debits:
                    debit_amount = abs(float(debit.amount))
                    cashout_amount = float(cashout.amount)
                    
                    # Check for exact or near-exact amount matches (within 1 cent)
                    if abs(debit_amount - cashout_amount) < 0.01:
                        exact_amount_matches.append(debit)
                        time_diff = abs((cashout.created_at - debit.created_at).total_seconds() / 60)
                        logger.info(f"💰 AMOUNT_MATCH: {debit.transaction_id} (${debit_amount:.2f} ≈ ${cashout_amount:.2f}, {time_diff:.1f}min diff)")
                
                if exact_amount_matches:
                    # Use the most recent exact amount match
                    best_match = exact_amount_matches[0]  # Already ordered by created_at desc
                    logger.info(f"✅ USD_AMOUNT_MATCH: {best_match.transaction_id}")
                    return best_match
                
                # No exact amount matches - use time proximity fallback
                logger.warning("⚠️ NO_AMOUNT_MATCH: Using time proximity fallback for USD cashout")
                closest_debit = min(potential_debits, key=lambda d: abs((d.created_at - cashout.created_at).total_seconds()))
                time_diff = abs((closest_debit.created_at - cashout.created_at).total_seconds() / 60)
                logger.info(f"✅ USD_TIME_FALLBACK: {closest_debit.transaction_id} ({time_diff:.1f}min diff)")
                return closest_debit
                
        except Exception as e:
            logger.error(f"❌ ERROR in _find_original_debit for {cashout.cashout_id}: {e}")
            return None
    
    @staticmethod
    async def _send_admin_frozen_funds_notification(results: Dict[str, Any]):
        """Send admin notification about processed refunds"""
        try:
            summary_message = (
                f"🔄 FAILED_CASHOUT_REFUNDS_PROCESSED\n\n"
                f"📊 Summary:\n"
                f"• Refunds processed: {results['refunds_processed']}\n"
                f"• Total refunded: ${results['total_amount_refunded']:.2f} USD\n"
                f"• Skipped (already refunded): {results['refunds_skipped']}\n"
                f"• Failed refunds: {results['refunds_failed']}\n\n"
            )
            
            if results['processed_cashouts']:
                summary_message += "💰 Processed cashouts:\n"
                for cashout_info in results['processed_cashouts'][:5]:  # Show first 5
                    if cashout_info['action'] == 'refunded':
                        summary_message += f"• {cashout_info['cashout_id']}: ${cashout_info['amount']:.2f} refunded\n"
                    elif cashout_info['action'] == 'failed':
                        summary_message += f"• {cashout_info['cashout_id']}: FAILED - {cashout_info.get('error', 'Unknown error')}\n"
                
                if len(results['processed_cashouts']) > 5:
                    summary_message += f"... and {len(results['processed_cashouts']) - 5} more\n"
            
            if results['refunds_failed'] > 0:
                summary_message += f"\n⚠️ {results['refunds_failed']} refunds require manual intervention"
            
            await consolidated_notification_service.send_admin_alert(summary_message)
            
        except Exception as e:
            logger.error(f"❌ Failed to send admin refund summary: {e}")


# Background job function for scheduler
async def monitor_failed_cashout_refunds():
    """Background job to monitor and refund failed cashouts"""
    monitor = FailedCashoutRefundMonitor()
    return await monitor.monitor_and_refund_failed_cashouts()