"""
Auto Cashout Monitor
Monitors and processes automatic cashout requests using unified PaymentProcessor architecture
"""

import logging
from typing import Dict, Any
from datetime import datetime
from database import managed_session
from models import Cashout, CashoutStatus
from sqlalchemy.exc import OperationalError
from sqlalchemy import or_, and_
import time

# MIGRATION: Use unified auto-cashout service instead of direct service imports
from services.auto_cashout_migration import auto_cashout_bridge
from utils.cashout_state_validator import CashoutStateValidator

logger = logging.getLogger(__name__)


class AutoCashoutMonitor:
    """Monitor for automatic cashout processing"""
    
    @staticmethod
    async def check_and_process_auto_cashouts() -> Dict[str, Any]:
        """Check for pending auto cashouts and process them with robust SSL error handling"""
        max_retries = 3
        retry_delay = 2  # seconds
        
        for attempt in range(max_retries):
            try:
                logger.debug(f"🔄 Checking for auto cashouts to process... (attempt {attempt + 1})")
                
                # Use managed session for robust error handling
                with managed_session() as session:
                    # Import config here to avoid circular imports
                    from config import Config
                    
                    # Find cashouts that need processing:
                    # 1. Standard pending cashouts (automatic)
                    # 2. Admin-pending cashouts that are admin-approved AND OTP-verified (manual with admin approval)
                    pending_cashouts = session.query(Cashout).filter(
                        or_(
                            # Standard automatic cashouts
                            Cashout.status == CashoutStatus.PENDING.value,
                            # Admin-approved manual cashouts with global toggle checks
                            and_(
                                or_(
                                    Cashout.status == CashoutStatus.ADMIN_PENDING.value,  # Standard admin pending
                                    Cashout.status == 'manual_processing'  # Legacy manual processing status
                                ),
                                Cashout.admin_approved == True,
                                Cashout.otp_verified == True,
                                or_(
                                    # NGN cashouts: check AUTO_CASHOUT_ENABLED_NGN (case-insensitive)
                                    and_(
                                        Cashout.cashout_type.in_(['ngn_bank', 'NGN_BANK']),
                                        Config.AUTO_CASHOUT_ENABLED_NGN
                                    ),
                                    # Crypto cashouts: check AUTO_CASHOUT_ENABLED_CRYPTO (case-insensitive)
                                    and_(
                                        Cashout.cashout_type.in_(['crypto', 'CRYPTO']),
                                        Config.AUTO_CASHOUT_ENABLED_CRYPTO
                                    )
                                )
                            )
                        )
                    ).limit(10).all()
                    
                    processed_count = 0
                    errors = []
                    
                    for cashout in pending_cashouts:
                        try:
                            # Log the processing type for observability
                            if cashout.status == CashoutStatus.ADMIN_PENDING.value or cashout.status == 'manual_processing':
                                logger.info(f"🔄 Processing admin-approved manual cashout {cashout.cashout_id} (status: {cashout.status}, type: {cashout.cashout_type}, admin_approved: {cashout.admin_approved}, otp_verified: {cashout.otp_verified})")
                            else:
                                logger.info(f"🔄 Processing automatic cashout {cashout.cashout_id} (type: {cashout.cashout_type})")
                            
                            # Atomic claiming: set status to 'processing' to prevent double-processing
                            # SECURITY: Validate state transition to prevent overwriting terminal states
                            original_status = cashout.status
                            try:
                                current_status = CashoutStatus(cashout.status)
                                CashoutStateValidator.validate_transition(
                                    current_status, 
                                    CashoutStatus.PROCESSING, 
                                    str(cashout.cashout_id)
                                )
                                cashout.status = 'processing'
                                session.commit()  # Commit the status change immediately
                            except Exception as validation_error:
                                logger.error(
                                    f"🚫 CASHOUT_CLAIM_BLOCKED: {current_status}→PROCESSING for {cashout.cashout_id}: {validation_error}"
                                )
                                continue  # Skip this cashout - invalid transition
                            
                            try:
                                # Process each cashout
                                result = await AutoCashoutMonitor._process_single_cashout(cashout, session)
                                if result.get('success'):
                                    processed_count += 1
                                    logger.info(f"✅ Successfully auto-processed cashout {cashout.cashout_id}")
                                else:
                                    # Revert status on failure
                                    cashout.status = original_status
                                    session.commit()
                                    errors.append(f"Cashout {cashout.cashout_id}: {result.get('error', 'Unknown error')}")
                                    logger.error(f"❌ Failed to process cashout {cashout.cashout_id}: {result.get('error')}")
                            except Exception as processing_error:
                                # Revert status on exception
                                cashout.status = original_status
                                session.commit()
                                raise processing_error
                                
                        except Exception as e:
                            errors.append(f"Cashout {cashout.cashout_id}: {str(e)}")
                            logger.error(f"❌ Error processing auto cashout {cashout.cashout_id}: {e}")
                    
                    result = {
                        'success': True,
                        'processed_count': processed_count,
                        'total_pending': len(pending_cashouts),
                        'errors': errors,
                        'timestamp': datetime.utcnow().isoformat()
                    }
                    
                    if processed_count > 0:
                        logger.info(f"✅ Auto-processed {processed_count} cashouts")
                    
                    return result
                    
            except OperationalError as e:
                if "SSL connection has been closed unexpectedly" in str(e):
                    logger.warning(f"🔌 SSL connection error on attempt {attempt + 1}: {e}")
                    if attempt < max_retries - 1:
                        logger.info(f"🔄 Retrying in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                        # Invalidate connection pool to force new connections
                        from database import engine
                        engine.dispose()
                        continue
                    else:
                        logger.error(f"❌ SSL connection failed after {max_retries} attempts")
                        return {
                            'success': False,
                            'error': f'SSL connection failed after {max_retries} attempts: {str(e)}',
                            'processed_count': 0
                        }
                else:
                    # Other database errors
                    logger.error(f"❌ Database error in auto cashout monitoring: {e}")
                    return {
                        'success': False,
                        'error': str(e),
                        'processed_count': 0
                    }
            except Exception as e:
                logger.error(f"❌ Unexpected error in auto cashout monitoring: {e}")
                return {
                    'success': False,
                    'error': str(e),
                    'processed_count': 0
                }
        
        # This should never be reached, but just in case
        return {
            'success': False,
            'error': 'Unexpected code path reached',
            'processed_count': 0
        }
    
    @staticmethod
    async def _process_single_cashout(cashout: Cashout, session) -> Dict[str, Any]:
        """
        Process a single auto cashout using unified PaymentProcessor architecture
        
        MIGRATION: Replaced direct service calls with unified auto-cashout bridge
        which routes to PaymentProcessor with fallback to legacy services.
        """
        try:
            logger.info(
                f"🔄 UNIFIED_PROCESSING: Processing cashout {cashout.cashout_id} "
                f"({cashout.cashout_type}) via auto-cashout bridge"
            )
            
            # Use unified auto-cashout bridge for processing
            # This automatically routes to PaymentProcessor with legacy fallback
            result = await auto_cashout_bridge.process_automatic_cashout(cashout, session)
            
            if result.get('success'):
                logger.info(
                    f"✅ UNIFIED_SUCCESS: Cashout {cashout.cashout_id} processed successfully "
                    f"via {result.get('provider', 'unknown')} provider"
                )
            else:
                logger.warning(
                    f"⚠️ UNIFIED_FAILED: Cashout {cashout.cashout_id} failed: "
                    f"{result.get('error', 'unknown error')}"
                )
            
            return result
                
        except Exception as e:
            logger.error(f"❌ Error processing cashout {cashout.cashout_id} via unified bridge: {e}")
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    async def get_auto_cashout_stats() -> Dict[str, Any]:
        """Get statistics about auto cashout processing with robust SSL error handling"""
        max_retries = 3
        retry_delay = 1  # seconds
        
        for attempt in range(max_retries):
            try:
                # Use managed session for robust error handling
                with managed_session() as session:
                    # Import config here to avoid circular imports
                    from config import Config
                    
                    # Count automatic cashouts (pending status)
                    auto_pending_count = session.query(Cashout).filter(
                        Cashout.status == CashoutStatus.PENDING.value,
                        Cashout.otp_verified == False,  # Only count auto-eligible cashouts
                        Cashout.processing_method != 'manual'  # CRITICAL: Only count automatic cashouts
                    ).count()
                    
                    # Count admin-approved manual cashouts that would be auto-processed
                    manual_pending_count = session.query(Cashout).filter(
                        or_(
                            Cashout.status == CashoutStatus.ADMIN_PENDING.value,  # Standard admin pending
                            Cashout.status == 'manual_processing'  # Legacy manual processing status
                        ),
                        Cashout.admin_approved == True,
                        Cashout.otp_verified == True,
                        or_(
                            # NGN cashouts: check AUTO_CASHOUT_ENABLED_NGN
                            and_(
                                Cashout.cashout_type == 'ngn_bank',
                                Config.AUTO_CASHOUT_ENABLED_NGN
                            ),
                            # Crypto cashouts: check AUTO_CASHOUT_ENABLED_CRYPTO
                            and_(
                                Cashout.cashout_type == 'crypto',
                                Config.AUTO_CASHOUT_ENABLED_CRYPTO
                            )
                        )
                    ).count()
                    
                    processing_count = session.query(Cashout).filter(
                        Cashout.status == 'processing'
                    ).count()
                    
                    completed_today = session.query(Cashout).filter(
                        Cashout.status.in_(['completed', 'success']),  # Include both legacy and new success status
                        Cashout.updated_at >= datetime.utcnow().date()
                    ).count()
                    
                    return {
                        'pending_auto_cashouts': auto_pending_count,
                        'pending_admin_approved_cashouts': manual_pending_count,
                        'total_eligible_for_auto_processing': auto_pending_count + manual_pending_count,
                        'processing_auto_cashouts': processing_count,
                        'completed_today': completed_today,
                        'timestamp': datetime.utcnow().isoformat(),
                        'global_controls': {
                            'AUTO_CASHOUT_ENABLED_NGN': Config.AUTO_CASHOUT_ENABLED_NGN,
                            'AUTO_CASHOUT_ENABLED_CRYPTO': Config.AUTO_CASHOUT_ENABLED_CRYPTO
                        }
                    }
                    
            except OperationalError as e:
                if "SSL connection has been closed unexpectedly" in str(e):
                    logger.warning(f"🔌 SSL connection error on stats attempt {attempt + 1}: {e}")
                    if attempt < max_retries - 1:
                        logger.info(f"🔄 Retrying stats in {retry_delay} seconds...")
                        time.sleep(retry_delay)
                        # Invalidate connection pool to force new connections
                        from database import engine
                        engine.dispose()
                        continue
                    else:
                        logger.error(f"❌ SSL connection failed after {max_retries} attempts for stats")
                        return {'error': f'SSL connection failed after {max_retries} attempts: {str(e)}'}
                else:
                    # Other database errors
                    logger.error(f"❌ Database error getting auto cashout stats: {e}")
                    return {'error': str(e)}
            except Exception as e:
                logger.error(f"❌ Unexpected error getting auto cashout stats: {e}")
                return {'error': str(e)}
        
        # This should never be reached, but just in case
        return {'error': 'Unexpected code path reached in stats'}


# Global instance for background job
auto_cashout_monitor = AutoCashoutMonitor()