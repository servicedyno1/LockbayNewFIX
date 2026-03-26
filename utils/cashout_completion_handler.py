"""
Cashout Completion Handler
Automatically releases holds when cashouts complete successfully
"""

import logging
from datetime import datetime
from typing import Dict, Any
from decimal import Decimal
from database import SessionLocal
from models import Cashout, CashoutStatus

logger = logging.getLogger(__name__)


async def auto_release_completed_cashout_hold(
    cashout_id: str,
    user_id: int,
    session=None
) -> Dict[str, Any]:
    """
    Automatically release hold when a cashout completes successfully
    
    Args:
        cashout_id: The cashout ID that completed
        user_id: User ID for the cashout
        session: Optional database session
        
    Returns:
        Dict with success status and details
    """
    use_provided_session = session is not None
    work_session = session if use_provided_session else SessionLocal()
    
    try:
        # Get the cashout record
        cashout = work_session.query(Cashout).filter(
            Cashout.cashout_id == cashout_id,
            Cashout.user_id == user_id
        ).first()
        
        if not cashout:
            return {
                "success": False, 
                "error": f"Cashout {cashout_id} not found for user {user_id}"
            }
        
        # CRITICAL FIX: Use correct metadata source (cashout_metadata model attribute) and consume hold for successful cashouts
        metadata_dict = {}
        if hasattr(cashout, 'cashout_metadata') and cashout.cashout_metadata is not None:
            if isinstance(cashout.cashout_metadata, dict):
                metadata_dict = cashout.cashout_metadata
            else:
                # Handle case where metadata might be a different type
                try:
                    metadata_dict = dict(cashout.cashout_metadata) if cashout.cashout_metadata else {}
                except (TypeError, ValueError):
                    logger.warning(f"Could not convert cashout_metadata to dict for cashout {cashout_id}, metadata type: {type(cashout.cashout_metadata)}")
                    metadata_dict = {}
        
        hold_transaction_id = metadata_dict.get("hold_transaction_id")
        hold_amount = metadata_dict.get("hold_amount")
        cashout_currency = metadata_dict.get("currency") or cashout.currency or "USD"
        
        if not hold_transaction_id or not hold_amount:
            # No hold to consume, but still create CASHOUT transaction for history
            from models import Transaction, TransactionType, Wallet
            
            wallet = work_session.query(Wallet).filter(
                Wallet.user_id == user_id,
                Wallet.currency == cashout_currency
            ).first()
            
            if wallet and cashout.amount:
                cashout_transaction = Transaction(
                    user_id=user_id,
                    transaction_type=TransactionType.CASHOUT.value,
                    amount=Decimal(str(cashout.amount)),
                    currency=cashout_currency,
                    transaction_id=f"CASHOUT_{cashout_id}",
                    description=f"Cashout to {getattr(cashout, 'destination', 'wallet')[:20]}..." if hasattr(cashout, 'destination') else f"Cashout {cashout_id}",
                    status="completed",
                    created_at=cashout.completed_at or datetime.utcnow()
                )
                work_session.add(cashout_transaction)
                
                if not use_provided_session:
                    work_session.commit()
                    
                logger.info(f"✅ Created CASHOUT transaction for user history (no hold): {cashout_id}")
            
            return {
                "success": True,
                "skipped": True,
                "reason": "No hold found - created CASHOUT transaction directly"
            }
        
        # CRITICAL FIX: Consume hold for successful cashouts (don't credit back to available balance)
        from services.crypto import CashoutHoldService
        consume_result = CashoutHoldService.consume_cashout_hold(
            user_id=user_id,
            amount=Decimal(str(hold_amount)),
            currency=cashout_currency,
            cashout_id=cashout_id,
            hold_transaction_id=hold_transaction_id,
            description=f"🔥 Completed cashout hold consumed: ${hold_amount:.2f} {cashout_currency} for {cashout_id}",
            session=work_session
        )
        
        if consume_result["success"]:
            logger.info(f"✅ AUTO_CONSUME: Consumed ${hold_amount:.2f} {cashout_currency} hold for completed cashout {cashout_id} (TX: {consume_result['consume_transaction_id']})")
            
            # CRITICAL FIX: Create user-visible CASHOUT transaction for transaction history
            from models import Transaction, TransactionType, Wallet
            
            # Get user's wallet
            wallet = work_session.query(Wallet).filter(
                Wallet.user_id == user_id,
                Wallet.currency == cashout_currency
            ).first()
            
            if wallet:
                # Create CASHOUT transaction for user's transaction history
                cashout_transaction = Transaction(
                    user_id=user_id,
                    transaction_type=TransactionType.CASHOUT.value,
                    amount=Decimal(str(hold_amount)),  # Positive amount
                    currency=cashout_currency,
                    transaction_id=f"CASHOUT_{cashout_id}",
                    description=f"Cashout to {getattr(cashout, 'destination_address', 'wallet')[:20]}..." if hasattr(cashout, 'destination_address') else f"Cashout {cashout_id}",
                    cashout_id=cashout_id,
                    status="completed",
                    created_at=cashout.completed_at or datetime.utcnow()
                )
                work_session.add(cashout_transaction)
                logger.info(f"✅ Created CASHOUT transaction for user history: {cashout_id}")
            
            if not use_provided_session:
                work_session.commit()
            
            return {
                "success": True,
                "consumed": True,
                "amount": hold_amount,
                "currency": cashout_currency,
                "hold_transaction_id": hold_transaction_id,
                "consume_transaction_id": consume_result["consume_transaction_id"]
            }
        else:
            logger.error(f"❌ Failed to consume hold for completed cashout {cashout_id}: {consume_result.get('error')}")
            return {
                "success": False,
                "error": f"Hold consume failed: {consume_result.get('error')}"
            }
            
    except Exception as e:
        logger.error(f"Error in auto_release_completed_cashout_hold for {cashout_id}: {e}")
        if not use_provided_session:
            work_session.rollback()
        return {
            "success": False,
            "error": f"Exception during hold release: {str(e)}"
        }
    finally:
        if not use_provided_session:
            work_session.close()


async def cleanup_completed_cashout_holds() -> Dict[str, Any]:
    """
    Background job to clean up completed cashouts that still have unreleased holds
    """
    results = {
        "processed": 0,
        "released": 0,
        "errors": 0,
        "total_amount_released": 0.0
    }
    
    session = SessionLocal()
    try:
        # Find completed cashouts with unconsumed holds
        completed_cashouts = session.query(Cashout).filter(
            Cashout.status == CashoutStatus.COMPLETED.value
        ).all()
        
        for cashout in completed_cashouts:
            results["processed"] += 1
            
            # CRITICAL FIX: Use cashout_metadata model attribute
            metadata = cashout.cashout_metadata or {}
            if metadata.get("hold_transaction_id") and metadata.get("hold_amount"):
                # This completed cashout has an unconsumed hold - consume it
                consume_result = await auto_release_completed_cashout_hold(
                    cashout.cashout_id,
                    cashout.user_id,
                    session
                )
                
                if consume_result.get("success") and consume_result.get("consumed"):
                    results["released"] += 1
                    results["total_amount_released"] += Decimal(str(consume_result["amount"]))
                    logger.info(f"🔄 CLEANUP: Consumed ${consume_result['amount']:.2f} {consume_result.get('currency', 'USD')} for completed cashout {cashout.cashout_id}")
                elif not consume_result.get("success"):
                    results["errors"] += 1
                    logger.error(f"❌ CLEANUP_ERROR: Failed to consume hold for {cashout.cashout_id}")
        
        session.commit()
        
        if results["released"] > 0:
            logger.info(f"✅ CLEANUP_COMPLETE: Released {results['released']} holds totaling ${results['total_amount_released']:.2f}")
        
        return results
        
    except Exception as e:
        logger.error(f"Error in cleanup_completed_cashout_holds: {e}")
        session.rollback()
        return {"error": str(e)}
    finally:
        session.close()


async def process_failed_cashout_hold_lifecycle(
    cashout_id: str,
    user_id: int,
    session=None,
    reason: str = "cashout_failed"
) -> Dict[str, Any]:
    """
    ARCHITECTURAL FIX: Proper lifecycle-aware processing for failed cashout holds
    
    This function correctly handles fund lifecycle by checking WalletHolds status:
    - HELD: Release hold back to available balance (funds never left our system)
    - CONSUMED_SENT: Create refund credit transaction (funds already sent to external provider)
    
    Args:
        cashout_id: The cashout ID that failed
        user_id: User ID for the cashout
        session: Optional database session
        reason: Reason for the failure
        
    Returns:
        Dict with success status and details
    """
    use_provided_session = session is not None
    work_session = session if use_provided_session else SessionLocal()
    
    try:
        from models import WalletHolds, WalletHoldStatus
        
        # Find the wallet hold record for this cashout
        hold_record = work_session.query(WalletHolds).filter(
            WalletHolds.linked_type == "cashout",
            WalletHolds.linked_id == cashout_id,
            WalletHolds.user_id == user_id
        ).first()
        
        if not hold_record:
            logger.info(f"⚠️ NO_HOLD_RECORD: No WalletHolds record found for cashout {cashout_id}, likely pre-migration cashout")
            return {
                "success": True,
                "skipped": True,
                "reason": "No WalletHolds record found - likely pre-migration cashout"
            }
        
        if hold_record.status == WalletHoldStatus.HELD.value:
            # SECURITY: Funds remain frozen for admin review - NEVER auto-release to available balance
            logger.info(f"🔒 FAILED_HELD: Cashout {cashout_id} failed, keeping ${hold_record.amount:.2f} {hold_record.currency} frozen for admin review")
            
            # Update hold record to FAILED_HELD status (funds stay frozen)
            hold_record.status = WalletHoldStatus.FAILED_HELD.value
            hold_record.failed_at = datetime.utcnow()
            hold_record.failure_reason = reason
            
            if not use_provided_session:
                work_session.commit()
            
            # TODO: Send admin notification for frozen funds requiring review
            logger.warning(f"🚨 ADMIN_REVIEW_REQUIRED: Failed cashout {cashout_id} has ${hold_record.amount:.2f} {hold_record.currency} frozen funds requiring admin review")
            
            return {
                "success": True,
                "action": "awaiting_admin",
                "amount": Decimal(str(hold_record.amount)),
                "currency": hold_record.currency,
                "status": "FAILED_HELD",
                "reason": reason,
                "message": "Funds frozen for admin review - admin must manually credit available balance"
            }
                
        elif hold_record.status == WalletHoldStatus.CONSUMED_SENT.value:
            # SECURITY: Even after external send, funds should not auto-refund - admin must review
            logger.info(f"🔒 CONSUMED_FAILED: Cashout {cashout_id} failed after consumption, keeping record for admin review")
            
            # Update hold record to FAILED_HELD status (no auto-refund)
            hold_record.status = WalletHoldStatus.FAILED_HELD.value
            hold_record.failed_at = datetime.utcnow()
            hold_record.failure_reason = reason
            
            if not use_provided_session:
                work_session.commit()
            
            # TODO: Send admin notification for potential refund consideration
            logger.warning(f"🚨 ADMIN_REVIEW_REQUIRED: Failed consumed cashout {cashout_id} - admin must review if external provider returned funds")
            
            return {
                "success": True,
                "action": "awaiting_admin",
                "amount": Decimal(str(hold_record.amount)),
                "currency": hold_record.currency,
                "status": "FAILED_HELD",
                "reason": reason,
                "message": "Failed after consumption - admin must review external provider status and manually credit if needed"
            }
                
        else:
            # Already processed or in invalid state
            logger.info(f"⚠️ HOLD_ALREADY_PROCESSED: Cashout {cashout_id} hold status is {hold_record.status}, no action needed")
            return {
                "success": True,
                "skipped": True,
                "reason": f"Hold already in status {hold_record.status}, no action needed"
            }
            
    except Exception as e:
        if not use_provided_session:
            work_session.rollback()
        logger.error(f"❌ CRITICAL_LIFECYCLE_ERROR: Failed to process hold lifecycle for cashout {cashout_id}: {e}")
        return {
            "success": False,
            "error": f"Critical error in process_failed_cashout_hold_lifecycle: {str(e)}"
        }
    finally:
        if not use_provided_session:
            work_session.close()