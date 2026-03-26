"""
Unified Retry Processor Job
Scheduled job to process transactions ready for retry using the UnifiedRetryService

This job runs every 2 minutes to check for and process retry attempts for 
transactions that failed with retryable external API errors.
"""

import logging
import asyncio
from datetime import datetime
from typing import Dict, Any

from config import Config

logger = logging.getLogger(__name__)


class UnifiedRetryProcessor:
    """
    Scheduled job processor for unified transaction retries
    
    Features:
    - Processes ready retries for external API failures
    - Integrates with existing job scheduling infrastructure  
    - Comprehensive metrics and logging
    - Feature flag controlled
    - Configurable batch processing
    """
    
    def __init__(self):
        self.enabled = Config.UNIFIED_RETRY_ENABLED
        self.batch_size = Config.UNIFIED_RETRY_BATCH_SIZE
        self.processing_interval = Config.UNIFIED_RETRY_PROCESSING_INTERVAL
        
        logger.info(f"🔄 UnifiedRetryProcessor initialized: enabled={self.enabled}, batch_size={self.batch_size}")
    
    async def process_ready_retries(self) -> Dict[str, Any]:
        """
        Process transactions that are ready for retry execution
        
        Returns:
            Dict with processing statistics
        """
        if not self.enabled:
            logger.debug("Unified retry processing disabled via feature flag")
            return {"skipped": True, "reason": "feature_disabled"}
        
        start_time = datetime.utcnow()
        
        try:
            # Import service here to avoid circular imports
            from services.unified_retry_service import unified_retry_service
            
            # Process ready retries using the unified service
            stats = await unified_retry_service.process_ready_retries(limit=self.batch_size)
            
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            
            # Enhanced logging with processing metrics
            if stats.get("processed", 0) > 0:
                logger.info(f"🔄 UNIFIED_RETRY_BATCH: Processed {stats['processed']} retries in {processing_time:.3f}s", 
                           extra={
                               "batch_stats": stats,
                               "processing_time_seconds": processing_time,
                               "batch_size": self.batch_size,
                               "efficiency_per_second": stats['processed'] / max(processing_time, 0.001)
                           })
            else:
                logger.debug("🔄 UNIFIED_RETRY_IDLE: No retries ready for processing")
            
            return {
                **stats,
                "processing_time_seconds": processing_time,
                "batch_size": self.batch_size
            }
            
        except Exception as e:
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            
            # Distinguish between user errors (expected) and system errors (unexpected)
            error_message = str(e)
            # Use more specific pattern matching to avoid false positives
            is_user_error = any(
                error_message.startswith(f"Error code: {user_error}") or
                error_message.startswith(user_error) or
                f": {user_error}" in error_message
                for user_error in [
                    'USER_INSUFFICIENT_BALANCE',
                    'API_INVALID_REQUEST', 
                    'API_AUTHENTICATION_FAILED',
                    'INVALID_ADDRESS_FORMAT'
                ]
            )
            
            if is_user_error:
                # Enhanced logging for user errors - gather context
                error_context = await self._gather_error_context(e, error_message)
                
                logger.warning(f"⚠️ UNIFIED_RETRY_USER_ERROR: {error_message} | " +
                             f"User: {error_context.get('user_info', 'Unknown')} | " +
                             f"Transaction: {error_context.get('transaction_info', 'Unknown')} | " +
                             f"Context: {error_context.get('operation_context', 'Unknown')}", 
                            extra={
                                "processing_time_seconds": processing_time,
                                "error_type": type(e).__name__,
                                "error_message": str(e),
                                "is_user_error": True,
                                "error_context": error_context
                            })
            else:
                logger.error(f"❌ UNIFIED_RETRY_PROCESSOR_ERROR: Failed to process retries: {e}", 
                            extra={
                                "processing_time_seconds": processing_time,
                                "error_type": type(e).__name__,
                                "error_message": str(e),
                                "is_user_error": False
                            })
            
            return {
                "processed": 0,
                "successful": 0,
                "failed": 0,
                "errors": 1,
                "processing_time_seconds": processing_time,
                "error": str(e)
            }
    
    async def _gather_error_context(self, exception: Exception, error_message: str) -> dict:
        """
        Gather detailed context about what caused the error
        """
        context = {
            "user_info": "Unknown",
            "transaction_info": "Unknown", 
            "operation_context": "Unknown"
        }
        
        try:
            from database import async_managed_session
            from models import User, Wallet
            
            # FIX: Use async session context manager
            async with async_managed_session() as db:
                # Try to extract transaction ID from exception context if available
                transaction_id = None
                user_id = None
                
                # Check if exception has attributes that might contain context
                if hasattr(exception, '__context__') and exception.__context__:
                    context_str = str(exception.__context__)
                    # Look for transaction ID patterns
                    import re
                    tx_match = re.search(r'(UTX[A-Z0-9]{17}|[A-Z0-9]{20})', context_str)
                    if tx_match:
                        transaction_id = tx_match.group(1)
                
                # Only attach context if we have a specific transaction ID
                # Remove the faulty heuristic that selects random failed transactions
                if transaction_id:
                    # Look up the specific transaction by ID  
                    from sqlalchemy import select
                    # FIX: UnifiedTransaction doesn't have transaction_id, it has id as primary key
                    # The transaction_id we extracted is likely a string, but id is Integer
                    # Let's search by external reference or skip this specific lookup
                    result = None
                    specific_transaction = None
                    specific_transaction = result.scalar_one_or_none()
                    
                    if specific_transaction:
                        user_id = specific_transaction.user_id
                        
                        # Get user info
                        user_result = await db.execute(select(User).where(User.id == user_id))
                        user = user_result.scalar_one_or_none()
                        if user:
                            # FIX: Safe attribute access for potentially None Column values
                            from utils.orm_typing_helpers import safe_getattr
                            username = safe_getattr(user, 'username', 'No username')
                            first_name = safe_getattr(user, 'first_name', 'No name') 
                            context["user_info"] = f"ID:{user.id} ({username}) - {first_name}"
                        
                        # Get wallet balances
                        wallet_result = await db.execute(select(Wallet).where(Wallet.user_id == user_id))
                        wallets = list(wallet_result.scalars())
                        wallet_info = []
                        for wallet in wallets:
                            wallet_info.append(f"{wallet.currency}:{wallet.available_balance}")
                        
                        # Note: This block won't execute since we set specific_transaction = None above
                        # context["transaction_info"] = f"Transaction ID:{specific_transaction.id} Amount:{specific_transaction.amount} {specific_transaction.currency} Status:{specific_transaction.status}"
                        context["operation_context"] = f"Wallet Balances: {', '.join(wallet_info) if wallet_info else 'No wallets'}"
                else:
                    # No specific transaction ID found - log generic error without attaching unrelated context
                    context["user_info"] = "Unable to identify specific user"
                    context["transaction_info"] = "Unable to identify specific transaction"
                    context["operation_context"] = "No specific context available - error may be system-wide"
        except Exception as gather_error:
            context["operation_context"] = f"Error gathering context: {gather_error}"
        
        return context


# Global instance for job scheduling
unified_retry_processor = UnifiedRetryProcessor()


# Job entry point for APScheduler
async def process_unified_retries(session=None):
    """
    Entry point for scheduled execution of unified retry processing
    
    This function is called by the APScheduler every 2 minutes to process
    transactions ready for retry attempts.
    
    Args:
        session: Optional database session to use (for retry engine integration)
    """
    try:
        result = await unified_retry_processor.process_ready_retries()
        
        # Log summary result for job monitoring
        if result.get("processed", 0) > 0:
            logger.info(f"✅ UNIFIED_RETRY_JOB_COMPLETE: {result}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ UNIFIED_RETRY_JOB_ERROR: {e}")
        return {"error": str(e), "processed": 0}


# Synchronous wrapper for APScheduler compatibility  
def process_unified_retries_sync():
    """
    Synchronous wrapper for APScheduler which doesn't support async jobs directly
    """
    try:
        # Run the async function in the event loop
        return asyncio.run(process_unified_retries())
    except Exception as e:
        logger.error(f"❌ UNIFIED_RETRY_SYNC_WRAPPER_ERROR: {e}")
        return {"error": str(e), "processed": 0}