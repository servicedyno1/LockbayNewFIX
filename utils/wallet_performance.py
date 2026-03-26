"""
Optimized Wallet Performance Module
Fast balance calculations and cached user data for wallet display
"""

import logging
from decimal import Decimal
from typing import Dict, Optional
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Cache for fast wallet display
WALLET_DISPLAY_CACHE: Dict[int, Dict] = {}
CACHE_TTL = timedelta(seconds=10)  # 10-second cache for wallet display


class FastWalletService:
    """High-performance wallet service for UI display"""
    
    @staticmethod
    def get_cached_wallet_data(user_id: int) -> Optional[Dict]:
        """Get cached wallet display data"""
        if user_id in WALLET_DISPLAY_CACHE:
            cached = WALLET_DISPLAY_CACHE[user_id]
            if datetime.utcnow() - cached["timestamp"] < CACHE_TTL:
                logger.debug(f"Wallet cache HIT for user {user_id}")
                return cached["data"]
            else:
                del WALLET_DISPLAY_CACHE[user_id]
        return None
    
    @staticmethod
    def cache_wallet_data(user_id: int, data: Dict):
        """Cache wallet display data"""
        WALLET_DISPLAY_CACHE[user_id] = {
            "data": data,
            "timestamp": datetime.utcnow()
        }
        logger.debug(f"Wallet data cached for user {user_id}")
    
    @staticmethod
    def fast_available_balance(user_id: int, session) -> Decimal:
        """Fast balance calculation without locks"""
        from models import Wallet, Escrow, EscrowStatus
        
        # Get wallet balance
        wallet = session.query(Wallet).filter(
            Wallet.user_id == user_id, 
            Wallet.currency == "USD"
        ).first()
        
        if not wallet:
            return Decimal("0.00")
        
        total_balance = Decimal(str(wallet.available_balance))
        
        # Simple sum query for reserved amounts (much faster than individual escrow parsing)
        from sqlalchemy import func, or_
        reserved_sum = session.query(func.sum(Escrow.total_amount)).filter(
            Escrow.buyer_id == user_id,
            Escrow.status.in_([
                EscrowStatus.PAYMENT_PENDING.value,
                EscrowStatus.PAYMENT_CONFIRMED.value,
                EscrowStatus.ACTIVE.value,
            ]),
            # Use proper payment_method field or fallback to legacy cancelled_reason
            or_(
                Escrow.payment_method == "wallet",
                Escrow.payment_method == "hybrid",
                Escrow.cancelled_reason.like('%payment_method:wallet%'),
                Escrow.cancelled_reason.like('%payment_method:hybrid%')
            )
        ).scalar() or 0
        
        reserved_amount = Decimal(str(reserved_sum))
        available = max(total_balance - reserved_amount, Decimal("0"))
        
        return available