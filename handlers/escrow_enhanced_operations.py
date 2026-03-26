"""
Enhanced Escrow Operations with Redis State Management and Financial Locking
Demonstrates proper escrow state transitions with SELECT FOR UPDATE and optimistic locking
"""

import logging
from typing import Optional, Dict, Any
from decimal import Decimal
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes

# Core imports
from models import User, Escrow, EscrowStatus

# Enhanced state management imports
from utils.session_migration_helper import session_migration_helper
from utils.financial_operation_locker import financial_locker, FinancialLockType
from utils.enhanced_db_session_manager import enhanced_db_session_manager

logger = logging.getLogger(__name__)


async def create_escrow_with_enhanced_locking(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    seller_details: str,
    amount: Decimal,
    currency: str,
    description: str,
    delivery_time: str
) -> Dict[str, Any]:
    """
    Create escrow with enhanced state management and financial locking
    
    This demonstrates:
    - Redis-backed escrow session management
    - SELECT FOR UPDATE for escrow creation
    - Atomic escrow state transitions
    - Comprehensive error handling
    
    Args:
        update: Telegram update object
        context: Telegram context object
        seller_details: Seller contact information
        amount: Escrow amount
        currency: Currency (USD/NGN/BTC/etc)
        description: Trade description
        delivery_time: Expected delivery time
        
    Returns:
        Dict with operation result and escrow details
    """
    if not update.effective_user:
        return {'success': False, 'error': 'No user found'}
    
    user_id = update.effective_user.id
    operation_id = f"escrow_create_{user_id}_{int(datetime.utcnow().timestamp())}"
    
    logger.info(f"🏦 Starting enhanced escrow creation: User {user_id}, Amount {amount} {currency}")
    
    try:
        # Use enhanced database session manager
        async with enhanced_db_session_manager.managed_session(
            operation_name=f"escrow_create_{operation_id}",
            timeout_seconds=45
        ):
            
            # Use financial operation locker for escrow creation
            async with financial_locker.atomic_financial_operation(
                operation_id=operation_id,
                lock_type=FinancialLockType.ESCROW_TRANSITION,
                timeout_seconds=30
            ) as locked_session:
                
                # Get user with lock
                user = locked_session.query(User).filter(
                    User.telegram_id == str(user_id)
                ).with_for_update().first()
                
                if not user:
                    return {
                        'success': False,
                        'error': 'User not found',
                        'operation_id': operation_id
                    }
                
                # Generate unique escrow ID
                escrow_id = f"ESC{int(datetime.utcnow().timestamp())}{user.id:04d}"
                
                # Create escrow record with proper initialization
                escrow = Escrow(
                    id=escrow_id,
                    buyer_id=user.id,
                    seller_details=seller_details,
                    amount=amount,
                    currency=currency,
                    description=description,
                    delivery_time=delivery_time,
                    status=EscrowStatus.PENDING_PAYMENT,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                    version=1  # Initialize version for optimistic locking
                )
                
                locked_session.add(escrow)
                locked_session.flush()  # Ensure escrow is persisted
                
                # Create Redis session for escrow flow
                escrow_session_data = {
                    'escrow_id': escrow_id,
                    'operation_id': operation_id,
                    'status': EscrowStatus.PENDING_PAYMENT.value,
                    'amount': float(amount),
                    'currency': currency,
                    'seller_details': seller_details,
                    'description': description,
                    'delivery_time': delivery_time,
                    'created_at': str(datetime.utcnow()),
                    'buyer_id': user.id,
                    'current_step': 'payment_pending',
                    'session_version': '2.0'
                }
                
                await session_migration_helper.set_session_data(
                    user_id, context, escrow_session_data, "escrow_data", "escrow_creation"
                )
                
                # Set user workflow state
                await session_migration_helper.set_session_data(
                    user_id, context, {
                        'active_workflow': 'escrow_payment',
                        'workflow_started': str(datetime.utcnow()),
                        'escrow_id': escrow_id
                    }, "workflow_state", "escrow_workflow"
                )
                
                logger.info(
                    f"✅ Enhanced escrow created successfully: "
                    f"ID {escrow_id}, User {user_id}, Amount {amount} {currency}"
                )
                
                return {
                    'success': True,
                    'escrow_id': escrow_id,
                    'operation_id': operation_id,
                    'status': EscrowStatus.PENDING_PAYMENT.value,
                    'amount': float(amount),
                    'currency': currency,
                    'created_at': str(datetime.utcnow())
                }
    
    except Exception as e:
        logger.error(f"❌ Enhanced escrow creation failed: User {user_id}, Operation {operation_id}, Error: {e}")
        
        # Clear any partial session data on error
        try:
            await session_migration_helper.clear_session_data(
                user_id, context, preserve=['user_preferences', 'admin_status']
            )
        except Exception as cleanup_error:
            logger.error(f"Error during escrow cleanup: {cleanup_error}")
        
        return {
            'success': False,
            'error': str(e),
            'operation_id': operation_id,
            'error_type': type(e).__name__
        }


async def transition_escrow_status_enhanced(
    escrow_id: str,
    new_status: EscrowStatus,
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    operation_notes: str = None
) -> Dict[str, Any]:
    """
    Enhanced escrow status transition with proper locking and state management
    
    Args:
        escrow_id: Escrow ID to transition
        new_status: New status to transition to
        user_id: User performing the transition
        context: Telegram context
        operation_notes: Optional notes about the transition
        
    Returns:
        Dict with transition result
    """
    operation_id = f"escrow_transition_{escrow_id}_{int(datetime.utcnow().timestamp())}"
    
    logger.info(
        f"🔄 Starting enhanced escrow transition: {escrow_id} → {new_status.value}"
    )
    
    try:
        async with enhanced_db_session_manager.managed_session(
            operation_name=f"escrow_transition_{operation_id}",
            timeout_seconds=30
        ):
            
            async with financial_locker.atomic_financial_operation(
                operation_id=operation_id,
                lock_type=FinancialLockType.ESCROW_TRANSITION,
                timeout_seconds=30
            ) as locked_session:
                
                # Lock escrow for transition
                escrow = await financial_locker.lock_escrow_for_transition(
                    locked_session, escrow_id
                )
                
                if not escrow:
                    return {
                        'success': False,
                        'error': f'Escrow {escrow_id} not found or locked',
                        'operation_id': operation_id
                    }
                
                old_status = escrow.status
                
                # Perform atomic status transition
                transition_success = await financial_locker.transition_escrow_status_atomically(
                    session=locked_session,
                    escrow=escrow,
                    new_status=new_status,
                    operation_notes=operation_notes
                )
                
                if not transition_success:
                    return {
                        'success': False,
                        'error': f'Invalid transition: {old_status} → {new_status}',
                        'operation_id': operation_id
                    }
                
                # Update Redis session state
                escrow_session_data = await session_migration_helper.get_session_data(
                    user_id, context, "escrow_data"
                )
                
                escrow_session_data.update({
                    'status': new_status.value,
                    'previous_status': old_status.value,
                    'last_transition': str(datetime.utcnow()),
                    'transition_notes': operation_notes or '',
                    'version': getattr(escrow, 'version', 1)
                })
                
                await session_migration_helper.set_session_data(
                    user_id, context, escrow_session_data, "escrow_data", "escrow_transition"
                )
                
                logger.info(
                    f"✅ Enhanced escrow transition successful: "
                    f"{escrow_id} {old_status} → {new_status}"
                )
                
                return {
                    'success': True,
                    'escrow_id': escrow_id,
                    'old_status': old_status.value,
                    'new_status': new_status.value,
                    'operation_id': operation_id,
                    'transition_time': str(datetime.utcnow())
                }
    
    except Exception as e:
        logger.error(
            f"❌ Enhanced escrow transition failed: {escrow_id}, "
            f"Operation {operation_id}, Error: {e}"
        )
        
        return {
            'success': False,
            'error': str(e),
            'operation_id': operation_id,
            'error_type': type(e).__name__
        }


async def get_escrow_with_session_data(
    escrow_id: str,
    user_id: int,
    context: ContextTypes.DEFAULT_TYPE
) -> Optional[Dict[str, Any]]:
    """
    Get escrow with combined database and Redis session data
    
    Args:
        escrow_id: Escrow ID to retrieve
        user_id: User ID for session data
        context: Telegram context
        
    Returns:
        Combined escrow and session data or None if not found
    """
    try:
        # Get database escrow data with proper locking
        async with enhanced_db_session_manager.managed_session(
            operation_name=f"escrow_read_{escrow_id}",
            timeout_seconds=10
        ) as db_session:
            
            escrow = db_session.query(Escrow).filter(
                Escrow.id == escrow_id
            ).with_for_update(read=True).first()  # Read lock
            
            if not escrow:
                return None
            
            # Get Redis session data
            session_data = await session_migration_helper.get_session_data(
                user_id, context, "escrow_data"
            )
            
            # Combine database and session data
            combined_data = {
                # Database fields
                'escrow_id': escrow.id,
                'buyer_id': escrow.buyer_id,
                'seller_details': escrow.seller_details,
                'amount': float(escrow.amount),
                'currency': escrow.currency,
                'description': escrow.description,
                'delivery_time': escrow.delivery_time,
                'status': escrow.status.value,
                'created_at': escrow.created_at.isoformat(),
                'updated_at': escrow.updated_at.isoformat() if escrow.updated_at else None,
                'version': getattr(escrow, 'version', 1),
                
                # Session data
                'session_data': session_data,
                'current_step': session_data.get('current_step', 'unknown'),
                'workflow_state': session_data.get('workflow_state', {})
            }
            
            return combined_data
            
    except Exception as e:
        logger.error(f"❌ Error retrieving escrow {escrow_id}: {e}")
        return None
