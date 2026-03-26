"""
Enhanced Wallet Operations with Financial Locking
Demonstrates proper use of Redis-backed sessions and financial operation locking
for critical wallet operations like balance updates and cashout processing
"""

import logging
from typing import Optional, Dict, Any
from decimal import Decimal
from datetime import datetime
from telegram import Update
from telegram.ext import ContextTypes

# Core imports
from models import Cashout, CashoutStatus

# Enhanced state management imports
from utils.session_migration_helper import session_migration_helper
from utils.financial_operation_locker import financial_locker, FinancialLockType
from utils.enhanced_db_session_manager import enhanced_db_session_manager

logger = logging.getLogger(__name__)


async def process_wallet_cashout_enhanced(
    update: Update, 
    context: ContextTypes.DEFAULT_TYPE,
    amount: Decimal,
    currency: str = "USD",
    cashout_method: str = "crypto"
) -> Dict[str, Any]:
    """
    Enhanced wallet cashout processing with Redis sessions and financial locking
    
    This function demonstrates:
    - Redis-backed session management
    - Distributed financial locking
    - SELECT FOR UPDATE for wallet operations
    - Atomic balance updates with optimistic locking
    - Comprehensive error handling and rollback
    
    Args:
        update: Telegram update object
        context: Telegram context object
        amount: Amount to cash out
        currency: Currency (default: USD)
        cashout_method: Cashout method (crypto/bank)
        
    Returns:
        Dict with operation result and details
    """
    if not update.effective_user:
        return {'success': False, 'error': 'No user found'}
    
    user_id = update.effective_user.id
    operation_id = f"cashout_{user_id}_{int(datetime.utcnow().timestamp())}"
    
    logger.info(f"💰 Starting enhanced wallet cashout: User {user_id}, Amount {amount} {currency}")
    
    try:
        # Use enhanced database session manager
        async with enhanced_db_session_manager.managed_session(
            operation_name=f"wallet_cashout_{operation_id}",
            timeout_seconds=30
        ):
            
            # Use financial operation locker for atomic operations
            async with financial_locker.atomic_financial_operation(
                operation_id=operation_id,
                lock_type=FinancialLockType.WALLET_BALANCE,
                timeout_seconds=30
            ) as locked_session:
                
                # Lock wallet for update (SELECT FOR UPDATE)
                wallet_lock_result = await financial_locker.lock_wallet_for_update(
                    locked_session, user_id, currency
                )
                
                if not wallet_lock_result:
                    return {
                        'success': False, 
                        'error': f'Wallet {currency} not found or locked',
                        'operation_id': operation_id
                    }
                
                user, wallet = wallet_lock_result
                original_balance = wallet.available_balance
                
                # Validate sufficient balance
                if original_balance < amount:
                    logger.warning(
                        f"⚠️ Insufficient balance: User {user_id}, "
                        f"Requested: {amount}, Available: {original_balance}"
                    )
                    return {
                        'success': False,
                        'error': 'Insufficient balance',
                        'available_balance': float(original_balance),
                        'requested_amount': float(amount)
                    }
                
                # Update wallet balance atomically
                balance_update_success = await financial_locker.update_wallet_balance_atomically(
                    session=locked_session,
                    user=user,
                    wallet=wallet,
                    amount_change=-amount,  # Debit
                    operation_type="wallet_cashout",
                    reference_id=operation_id
                )
                
                if not balance_update_success:
                    return {
                        'success': False,
                        'error': 'Failed to update wallet balance',
                        'operation_id': operation_id
                    }
                
                # Create cashout record
                cashout = Cashout(
                    user_id=user.id,
                    amount=amount,
                    currency=currency,
                    method=cashout_method,
                    status=CashoutStatus.PENDING,
                    reference_id=operation_id,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
                locked_session.add(cashout)
                locked_session.flush()  # Get cashout ID
                
                # Update Redis session with cashout details
                await session_migration_helper.set_session_data(
                    user_id, context, {
                        'cashout_id': cashout.id,
                        'operation_id': operation_id,
                        'amount': float(amount),
                        'currency': currency,
                        'method': cashout_method,
                        'status': 'pending',
                        'created_at': str(datetime.utcnow()),
                        'original_balance': float(original_balance),
                        'new_balance': float(original_balance - amount)
                    }, "active_cashout", "wallet_cashout_processing"
                )
                
                logger.info(
                    f"✅ Enhanced cashout created successfully: "
                    f"User {user_id}, Cashout ID {cashout.id}, "
                    f"Amount {amount} {currency}, "
                    f"Balance {original_balance} → {original_balance - amount}"
                )
                
                return {
                    'success': True,
                    'cashout_id': cashout.id,
                    'operation_id': operation_id,
                    'amount': float(amount),
                    'currency': currency,
                    'original_balance': float(original_balance),
                    'new_balance': float(original_balance - amount),
                    'status': 'pending',
                    'method': cashout_method
                }
    
    except Exception as e:
        logger.error(f"❌ Enhanced cashout failed: User {user_id}, Operation {operation_id}, Error: {e}")
        
        # Clear any partial session data on error
        try:
            await session_migration_helper.clear_session_data(
                user_id, context, preserve=['user_preferences', 'admin_status']
            )
        except Exception as cleanup_error:
            logger.error(f"Error during cleanup: {cleanup_error}")
        
        return {
            'success': False,
            'error': str(e),
            'operation_id': operation_id,
            'error_type': type(e).__name__
        }


async def get_wallet_balance_with_locking(
    user_id: int, 
    currency: str = "USD"
) -> Optional[Dict[str, Any]]:
    """
    Get wallet balance with proper locking for consistent reads
    
    Args:
        user_id: Telegram user ID
        currency: Currency to check
        
    Returns:
        Dict with balance information or None if not found
    """
    operation_id = f"balance_check_{user_id}_{int(datetime.utcnow().timestamp())}"
    
    try:
        async with enhanced_db_session_manager.managed_session(
            operation_name=f"balance_check_{operation_id}",
            timeout_seconds=10
        ) as db_session:
            
            # Use read lock for consistent balance reading
            wallet_result = await financial_locker.lock_wallet_for_update(
                db_session, user_id, currency
            )
            
            if not wallet_result:
                return None
            
            user, wallet = wallet_result
            
            return {
                'balance': float(wallet.available_balance),
                'currency': currency,
                'user_id': user_id,
                'wallet_id': wallet.id,
                'last_updated': wallet.updated_at.isoformat() if wallet.updated_at else None
            }
            
    except Exception as e:
        logger.error(f"❌ Error getting wallet balance: User {user_id}, Currency {currency}, Error: {e}")
        return None


async def demonstrate_enhanced_state_management(
    update: Update, 
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Demonstrate enhanced state management capabilities
    Shows how to use Redis-backed sessions, financial locking, and proper cleanup
    """
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    try:
        # 1. Get current session data from Redis
        session_data = await session_migration_helper.get_session_data(user_id, context)
        logger.info(f"📊 Current session data for user {user_id}: {session_data}")
        
        # 2. Demonstrate wallet balance check with locking
        balance_info = await get_wallet_balance_with_locking(user_id, "USD")
        if balance_info:
            logger.info(f"💰 Wallet balance (with locking): {balance_info}")
        
        # 3. Show financial operation metrics
        lock_metrics = financial_locker.get_lock_metrics()
        session_metrics = enhanced_db_session_manager.get_session_metrics()
        
        logger.info(f"📈 Financial lock metrics: {lock_metrics}")
        logger.info(f"📈 Database session metrics: {session_metrics}")
        
        # 4. Update session with demo data
        demo_data = {
            'last_activity': str(datetime.utcnow()),
            'feature_demo': 'enhanced_state_management',
            'session_version': '2.0'
        }
        
        await session_migration_helper.set_session_data(
            user_id, context, demo_data, "demo_data", "feature_demonstration"
        )
        
        if update.effective_chat:
            await update.effective_chat.send_message(
                "🚀 Enhanced State Management Demo Complete\n\n"
                f"📊 Session Data: {len(session_data)} keys\n"
                f"💰 Balance Check: {'Success' if balance_info else 'No wallet'}\n"
                f"🔒 Active Locks: {lock_metrics.get('active_locks', 0)}\n"
                f"📞 DB Sessions: {session_metrics.get('active_sessions', {}).get('total', 0)}"
            )
        
    except Exception as e:
        logger.error(f"❌ Demo error: {e}")
        if update.effective_chat:
            await update.effective_chat.send_message(f"❌ Demo failed: {e}")
