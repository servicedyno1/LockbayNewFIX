"""
Financial Audit Event Relay Job
Background service to process financial audit events from outbox table
"""

import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any
from sqlalchemy import func

from utils.financial_audit_logger import financial_audit_relay
from models import AuditEvent
from database import SessionLocal

logger = logging.getLogger(__name__)


class FinancialAuditRelayJob:
    """Background job to process financial audit events from outbox"""
    
    def __init__(self):
        self.relay = financial_audit_relay
        self.batch_size = 50  # Process events in batches
        self.max_processing_time = 45  # seconds
        
    async def run_financial_audit_relay(self):
        """
        Main job function to process pending audit events
        Called by the scheduler every 2 minutes
        """
        start_time = datetime.utcnow()
        total_stats = {
            'processed': 0,
            'failed': 0,
            'retries': 0,
            'batches': 0
        }
        
        logger.info("🔄 FINANCIAL_AUDIT_RELAY: Starting audit event processing")
        
        try:
            # Process events in batches until time limit or no more events
            while (datetime.utcnow() - start_time).total_seconds() < self.max_processing_time:
                # Check if there are pending events
                pending_count = await self._get_pending_event_count()
                if pending_count == 0:
                    break
                
                # Process a batch
                batch_stats = await self.relay.process_pending_events()
                
                # Update totals
                total_stats['processed'] += batch_stats['processed']
                total_stats['failed'] += batch_stats['failed']
                total_stats['retries'] += batch_stats['retries']
                total_stats['batches'] += 1
                
                # If no events were processed in this batch, break
                if batch_stats['processed'] == 0:
                    break
                
                # Brief pause between batches
                await asyncio.sleep(0.1)
            
            # Log summary
            if total_stats['processed'] > 0 or total_stats['failed'] > 0:
                processing_time = (datetime.utcnow() - start_time).total_seconds()
                logger.info(
                    f"✅ FINANCIAL_AUDIT_RELAY: Completed - "
                    f"Processed: {total_stats['processed']}, "
                    f"Failed: {total_stats['failed']}, "
                    f"Retries: {total_stats['retries']}, "
                    f"Batches: {total_stats['batches']}, "
                    f"Time: {processing_time:.2f}s"
                )
            
            return total_stats
            
        except Exception as e:
            logger.error(f"❌ FINANCIAL_AUDIT_RELAY: Error processing audit events: {e}")
            raise
    
    async def cleanup_old_audit_events(self):
        """
        Cleanup job to remove old processed audit events
        Called by the scheduler daily
        """
        logger.info("🧹 FINANCIAL_AUDIT_CLEANUP: Starting cleanup of old audit events")
        
        try:
            await self.relay.cleanup_old_events(days_to_keep=90)
            logger.info("✅ FINANCIAL_AUDIT_CLEANUP: Completed cleanup")
            
        except Exception as e:
            logger.error(f"❌ FINANCIAL_AUDIT_CLEANUP: Error during cleanup: {e}")
            raise
    
    async def _get_pending_event_count(self) -> int:
        """Get count of pending audit events"""
        try:
            with SessionLocal() as session:
                count = (
                    session.query(AuditEvent)
                    .filter(
                        AuditEvent.processed == False,
                        AuditEvent.retry_count < 3
                    )
                    .count()
                )
                return count
                
        except Exception as e:
            logger.error(f"Error getting pending event count: {e}")
            return 0
    
    async def get_audit_statistics(self) -> Dict[str, Any]:
        """Get audit event statistics for monitoring"""
        try:
            with SessionLocal() as session:
                # Get event counts by status
                pending_count = (
                    session.query(AuditEvent)
                    .filter(AuditEvent.processed == False)
                    .count()
                )
                
                processed_today = (
                    session.query(AuditEvent)
                    .filter(
                        AuditEvent.processed == True,
                        AuditEvent.processed_at >= datetime.utcnow().date()
                    )
                    .count()
                )
                
                failed_count = (
                    session.query(AuditEvent)
                    .filter(
                        AuditEvent.processed == False,
                        AuditEvent.retry_count >= 3
                    )
                    .count()
                )
                
                # Get recent event types  
                recent_events = (
                    session.query(AuditEvent.event_type, func.count(AuditEvent.id).label('count'))
                    .filter(AuditEvent.created_at >= datetime.utcnow() - timedelta(hours=24))
                    .group_by(AuditEvent.event_type)
                    .all()
                )
                
                return {
                    'pending_events': pending_count,
                    'processed_today': processed_today,
                    'failed_events': failed_count,
                    'recent_event_types': dict(recent_events) if recent_events else {}
                }
                
        except Exception as e:
            logger.error(f"Error getting audit statistics: {e}")
            return {
                'pending_events': 0,
                'processed_today': 0,
                'failed_events': 0,
                'recent_event_types': {}
            }


# Global job instance
financial_audit_relay_job = FinancialAuditRelayJob()


# Job handler functions for scheduler integration
async def financial_audit_relay_handler(**kwargs):
    """Handler function for financial audit relay job"""
    return await financial_audit_relay_job.run_financial_audit_relay()


async def financial_audit_cleanup_handler(**kwargs):
    """Handler function for financial audit cleanup job"""
    return await financial_audit_relay_job.cleanup_old_audit_events()


async def financial_audit_stats_handler(**kwargs):
    """Handler function for audit statistics monitoring"""
    stats = await financial_audit_relay_job.get_audit_statistics()
    
    if stats['failed_events'] > 0:
        logger.warning(
            f"⚠️ FINANCIAL_AUDIT_STATS: {stats['failed_events']} failed audit events detected"
        )
    
    if stats['pending_events'] > 1000:
        logger.warning(
            f"⚠️ FINANCIAL_AUDIT_STATS: High pending event count: {stats['pending_events']}"
        )
    
    logger.info(
        f"📊 FINANCIAL_AUDIT_STATS: "
        f"Pending: {stats['pending_events']}, "
        f"Today: {stats['processed_today']}, "
        f"Failed: {stats['failed_events']}"
    )
    
    return stats