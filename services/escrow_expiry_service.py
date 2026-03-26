"""
Escrow Expiry Service - Pure async DB + domain state transitions
Handles expired escrow detection and state management without wallet operations
"""

import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models import Escrow, EscrowStatus
from database import async_managed_session
from services.admin_trade_notifications import admin_trade_notifications

logger = logging.getLogger(__name__)


class EscrowExpiryService:
    """Clean separation of escrow expiry logic from wallet/notification concerns"""
    
    def __init__(self, batch_size: int = 50):
        self.batch_size = batch_size
    
    async def process_expired_escrows(self, session: Optional[AsyncSession] = None) -> Dict[str, Any]:
        """
        Process expired escrows with pure DB operations only
        Returns list of expired escrows for downstream processing
        """
        results = {
            "processed": 0,
            "expired_escrows": [],
            "errors": []
        }
        
        try:
            # Use provided session or create new one
            if session is None:
                async with async_managed_session() as new_session:
                    return await self._process_with_session(new_session)
            else:
                return await self._process_with_session(session)
                
        except Exception as e:
            logger.error(f"❌ ESCROW_EXPIRY_SERVICE_ERROR: {e}")
            results["errors"].append(str(e))
            return results
    
    async def _process_with_session(self, session: AsyncSession) -> Dict[str, Any]:
        """Internal processing with guaranteed async session"""
        results = {
            "processed": 0,
            "expired_escrows": [],
            "errors": []
        }
        
        try:
            cutoff_time = datetime.utcnow()
            
            # PHASE 1: Process escrows that need to be marked as expired
            logger.info("🔍 PHASE_1: Processing escrows that need expiry status update")
            stmt = select(Escrow).where(
                Escrow.expires_at < cutoff_time,
                Escrow.status.in_([
                    EscrowStatus.PAYMENT_PENDING.value,
                    EscrowStatus.PARTIAL_PAYMENT.value,
                    EscrowStatus.PAYMENT_CONFIRMED.value
                ])
            ).limit(self.batch_size)
            
            result = await session.execute(stmt)
            newly_expired_escrows = result.scalars().all()
            
            logger.info(f"🔍 PHASE_1: Found {len(newly_expired_escrows)} escrows to expire")
            
            # Process newly expired escrows
            for escrow in newly_expired_escrows:
                try:
                    # CRITICAL FIX: Set correct status based on payment state
                    # CANCELED = Payment never confirmed (payment timeout)
                    # EXPIRED = Payment confirmed but delivery deadline passed
                    from sqlalchemy import update
                    
                    if escrow.payment_confirmed_at is None:
                        # Payment was NEVER confirmed - this is a CANCELLATION
                        new_status = EscrowStatus.CANCELLED.value
                        status_reason = "payment_timeout"
                        logger.info(f"🚫 AUTO_CANCEL: Escrow {escrow.escrow_id} - payment timeout (never paid)")
                    else:
                        # Payment WAS confirmed but delivery deadline passed - this is EXPIRY
                        new_status = EscrowStatus.EXPIRED.value
                        status_reason = "delivery_timeout"
                        logger.info(f"⏰ AUTO_EXPIRE: Escrow {escrow.escrow_id} - delivery timeout (needs refund)")
                    
                    update_stmt = update(Escrow).where(Escrow.id == escrow.id).values(
                        status=new_status
                    )
                    await session.execute(update_stmt)
                    
                    # Send admin notification for escrow expiry
                    if new_status == EscrowStatus.EXPIRED.value:
                        # Get buyer and seller info for notification
                        from sqlalchemy import select as sql_select
                        from models import User
                        
                        buyer_result = await session.execute(sql_select(User).where(User.id == escrow.buyer_id))
                        buyer = buyer_result.scalar_one_or_none()
                        
                        seller_result = await session.execute(sql_select(User).where(User.id == escrow.seller_id))
                        seller = seller_result.scalar_one_or_none()
                        
                        buyer_info = f"{buyer.first_name} (@{buyer.username})" if buyer and buyer.username else (f"{buyer.first_name}" if buyer else "Unknown")
                        seller_info = f"{seller.first_name} (@{seller.username})" if seller and seller.username else (f"{seller.first_name}" if seller else "Unknown")
                        
                        asyncio.create_task(
                            admin_trade_notifications.notify_escrow_expired({
                                'escrow_id': escrow.escrow_id,
                                'amount': float(str(escrow.amount)) if escrow.amount is not None else 0.0,
                                'buyer_info': buyer_info,
                                'seller_info': seller_info,
                                'currency': escrow.currency,
                                'expired_at': datetime.utcnow(),
                                'expiry_reason': 'delivery_timeout'
                            })
                        )
                    
                    # Add to results for downstream processing
                    escrow_data = {
                        "escrow_id": escrow.escrow_id,
                        "internal_id": escrow.id,
                        "buyer_id": escrow.buyer_id,
                        "seller_id": escrow.seller_id,
                        "amount": float(str(escrow.amount)) if escrow.amount is not None else 0.0,
                        "currency": escrow.currency,
                        "expired_at": escrow.expires_at.isoformat() if escrow.expires_at is not None else None,
                        "payment_confirmed_at": escrow.payment_confirmed_at.isoformat() if escrow.payment_confirmed_at is not None else None,
                        "needs_refund": escrow.payment_confirmed_at is not None,
                        "processing_status": "newly_expired",
                        "status_reason": status_reason
                    }
                    results["expired_escrows"].append(escrow_data)
                    results["processed"] += 1
                    
                    logger.info(f"✅ STATUS_UPDATE: {escrow.escrow_id} → {new_status} ({status_reason})")
                    
                except Exception as escrow_error:
                    logger.error(f"❌ PHASE_1_ERROR: {escrow.id}: {escrow_error}")
                    results["errors"].append(f"Phase1 Escrow {escrow.id}: {str(escrow_error)}")
            
            # PHASE 2: CRITICAL FIX - Process escrows already in expired status for refunds/notifications
            logger.info("🔍 PHASE_2: Processing existing expired escrows for refunds and notifications")
            
            # Get escrows already marked as expired that may need refunds or notifications
            # Use processed_for_refund and notified_buyers fields to track processing state
            already_expired_stmt = select(Escrow).where(
                Escrow.status == EscrowStatus.EXPIRED.value,
                Escrow.expires_at < cutoff_time
            ).limit(self.batch_size)
            
            expired_result = await session.execute(already_expired_stmt)
            already_expired_escrows = expired_result.scalars().all()
            
            logger.info(f"🔍 PHASE_2: Found {len(already_expired_escrows)} existing expired escrows to process")
            
            # Process already expired escrows for refunds and notifications
            for escrow in already_expired_escrows:
                try:
                    # Check if this escrow needs refund processing (has payment but not processed)
                    needs_refund = (escrow.payment_confirmed_at is not None and 
                                   not getattr(escrow, 'processed_for_refund', False))
                    
                    # Check if buyer notification was sent
                    needs_notification = not getattr(escrow, 'notified_buyers', False)
                    
                    if needs_refund or needs_notification:
                        escrow_data = {
                            "escrow_id": escrow.escrow_id,
                            "internal_id": escrow.id,
                            "buyer_id": escrow.buyer_id,
                            "seller_id": escrow.seller_id,
                            "amount": float(str(escrow.amount)) if escrow.amount is not None else 0.0,
                            "currency": escrow.currency,
                            "expired_at": escrow.expires_at.isoformat() if escrow.expires_at is not None else None,
                            "payment_confirmed_at": escrow.payment_confirmed_at.isoformat() if escrow.payment_confirmed_at is not None else None,
                            "needs_refund": needs_refund,
                            "needs_notification": needs_notification,
                            "processing_status": "existing_expired"
                        }
                        results["expired_escrows"].append(escrow_data)
                        results["processed"] += 1
                        
                        logger.info(f"🔄 EXISTING_EXPIRED: {escrow.id} added for processing (refund: {needs_refund}, notification: {needs_notification})")
                    
                except Exception as escrow_error:
                    logger.error(f"❌ PHASE_2_ERROR: {escrow.id}: {escrow_error}")
                    results["errors"].append(f"Phase2 Escrow {escrow.id}: {str(escrow_error)}")
            
            # Flush changes (commit handled by caller)
            await session.flush()
            
            total_processed = len(newly_expired_escrows) + len([e for e in results["expired_escrows"] if e["processing_status"] == "existing_expired"])
            
            if total_processed > 0:
                logger.info(f"✅ ESCROW_EXPIRY_COMPLETE: Phase 1: {len(newly_expired_escrows)} newly expired, Phase 2: {len([e for e in results['expired_escrows'] if e['processing_status'] == 'existing_expired'])} existing expired")
            
            return results
            
        except Exception as e:
            logger.error(f"❌ ESCROW_EXPIRY_SESSION_ERROR: {e}")
            results["errors"].append(str(e))
            return results