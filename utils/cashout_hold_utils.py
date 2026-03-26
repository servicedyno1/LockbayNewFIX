"""
Utility functions for automatic cashout hold management.
Prevents frozen balance issues by ensuring holds are always released when cashouts finish.
"""

import logging
from typing import Dict, Any
from decimal import Decimal

logger = logging.getLogger(__name__)


def auto_release_cashout_hold(
    cashout: Any,
    session: Any = None,
    reason: str = "cashout_completed"
) -> Dict[str, Any]:
    """
    Automatically release cashout hold when cashout completes or fails.
    
    Args:
        cashout: The cashout object with metadata containing hold info
        session: Database session (optional)
        reason: Reason for release ("cashout_completed" or "cashout_failed")
        
    Returns:
        Dict with success status and details
    """
    try:
        from services.crypto import CashoutHoldService
        
        # Check if this cashout has a hold that needs to be released
        metadata = getattr(cashout, 'metadata', {}) or {}
        
        # Skip if no hold was placed
        if not metadata.get("hold_transaction_id") or not metadata.get("funds_held"):
            logger.debug(f"No hold to release for cashout {cashout.cashout_id}")
            return {
                "success": True,
                "skipped": True,
                "reason": "no_hold_placed"
            }
        
        # Extract hold information from metadata
        hold_amount = metadata.get("hold_amount")
        hold_transaction_id = metadata.get("hold_transaction_id")
        user_id = getattr(cashout, 'user_id', None)
        
        if not all([hold_amount, hold_transaction_id, user_id]):
            logger.warning(f"Incomplete hold info for cashout {cashout.cashout_id}: amount={hold_amount}, tx_id={hold_transaction_id}, user_id={user_id}")
            return {
                "success": False,
                "error": "Incomplete hold information in cashout metadata"
            }
        
        # Release the hold using internal system function (not admin-only function)
        description = f"Auto-release hold for {reason}: {cashout.cashout_id}"
        
        release_result = CashoutHoldService._release_cashout_hold_internal_system_only(
            user_id=user_id,
            amount=Decimal(str(hold_amount)),
            currency="USD",
            cashout_id=cashout.cashout_id,
            hold_transaction_id=hold_transaction_id,
            description=description,
            session=session,
            system_context=f"auto_release_cashout_hold_{reason}"
        )
        
        if release_result.get("success"):
            logger.info(
                f"✅ HOLD_AUTO_RELEASED: Cashout {cashout.cashout_id} - "
                f"${hold_amount:.2f} USD hold released for user {user_id} "
                f"(reason: {reason})"
            )
            
            # Update cashout metadata to mark hold as released
            if not metadata.get("hold_released"):
                metadata["hold_released"] = True
                metadata["hold_release_reason"] = reason
                metadata["hold_release_transaction_id"] = release_result.get("release_transaction_id")
                cashout.metadata = metadata
            
            return {
                "success": True,
                "released_amount": hold_amount,
                "release_transaction_id": release_result.get("release_transaction_id"),
                "reason": reason
            }
        else:
            logger.error(
                f"❌ HOLD_RELEASE_FAILED: Cashout {cashout.cashout_id} - "
                f"Failed to release ${hold_amount:.2f} USD hold: {release_result.get('error')}"
            )
            return {
                "success": False,
                "error": f"Hold release failed: {release_result.get('error')}",
                "hold_amount": hold_amount
            }
            
    except Exception as e:
        logger.error(f"❌ CRITICAL_HOLD_RELEASE_ERROR: Cashout {getattr(cashout, 'cashout_id', 'unknown')}: {e}")
        return {
            "success": False,
            "error": f"Critical error in auto_release_cashout_hold: {str(e)}"
        }


def bulk_release_orphaned_holds(session: Any = None, dry_run: bool = True) -> Dict[str, Any]:
    """
    Find and release orphaned cashout holds for completed/failed cashouts.
    Used for cleanup of existing frozen balances.
    
    Args:
        session: Database session (optional) 
        dry_run: If True, only report what would be released without actually doing it
        
    Returns:
        Dict with cleanup results
    """
    try:
        from models import Cashout, CashoutStatus
        from database import SessionLocal
        
        use_provided_session = session is not None
        cleanup_session = session if use_provided_session else SessionLocal()
        
        results = {
            "found_orphaned_holds": 0,
            "released_holds": 0,
            "failed_releases": 0,
            "total_amount_released": 0.0,
            "dry_run": dry_run,
            "details": []
        }
        
        try:
            # Find completed/failed cashouts that still have unreleased holds
            cashouts_with_holds = cleanup_session.query(Cashout).filter(
                Cashout.status.in_([
                    CashoutStatus.COMPLETED.value,
                    CashoutStatus.FAILED.value
                ])
            ).all()
            
            for cashout in cashouts_with_holds:
                metadata = cashout.metadata or {}
                
                # Check if this cashout has an unreleased hold
                if (metadata.get("hold_transaction_id") and 
                    metadata.get("funds_held") and 
                    not metadata.get("hold_released")):
                    
                    results["found_orphaned_holds"] += 1
                    hold_amount = metadata.get("hold_amount", 0)
                    
                    cashout_info = {
                        "cashout_id": cashout.cashout_id,
                        "user_id": cashout.user_id,
                        "amount": hold_amount,
                        "status": cashout.status,
                        "hold_transaction_id": metadata.get("hold_transaction_id")
                    }
                    
                    if not dry_run:
                        # Actually release the hold
                        reason = "cleanup_orphaned_hold"
                        release_result = auto_release_cashout_hold(
                            cashout=cashout,
                            session=cleanup_session,
                            reason=reason
                        )
                        
                        if release_result.get("success"):
                            results["released_holds"] += 1
                            results["total_amount_released"] += hold_amount
                            cashout_info["released"] = True
                            cashout_info["release_transaction_id"] = release_result.get("release_transaction_id")
                        else:
                            results["failed_releases"] += 1
                            cashout_info["released"] = False
                            cashout_info["error"] = release_result.get("error")
                    else:
                        cashout_info["would_release"] = True
                        results["total_amount_released"] += hold_amount
                    
                    results["details"].append(cashout_info)
            
            if not use_provided_session and not dry_run:
                cleanup_session.commit()
                
            logger.info(
                f"🔍 ORPHANED_HOLDS_CLEANUP: Found {results['found_orphaned_holds']} orphaned holds, "
                f"{'would release' if dry_run else 'released'} ${results['total_amount_released']:.2f}"
            )
            
            return results
            
        except Exception as e:
            if not use_provided_session and not dry_run:
                cleanup_session.rollback()
            logger.error(f"Error in bulk hold cleanup: {e}")
            raise
        finally:
            if not use_provided_session:
                cleanup_session.close()
                
    except Exception as e:
        logger.error(f"Critical error in bulk_release_orphaned_holds: {e}")
        return {
            "success": False,
            "error": str(e)
        }