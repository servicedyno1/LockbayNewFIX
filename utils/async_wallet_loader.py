"""
Async Wallet Data Loader
Loads heavy wallet data in background after UI is displayed
"""

import logging
import asyncio
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


class AsyncWalletLoader:
    """Loads wallet data asynchronously after UI display"""
    
    @staticmethod
    async def load_earnings_data(user_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Load earnings data in background"""
        try:
            from services.auto_earnings_service import AutoEarningsService
            from config import Config
            
            earnings_summary = await AutoEarningsService.get_user_earnings_summary(user_id)
            total_earnings = earnings_summary.get("total_earnings", 0)
            
            if total_earnings > 0:
                total_fmt = (
                    f"{Config.PLATFORM_CURRENCY_SYMBOL}{total_earnings:.2f}"
                    if Config.PLATFORM_CURRENCY != "JPY"
                    else f"{Config.PLATFORM_CURRENCY_SYMBOL}{int(total_earnings)}"
                )
                
                # Update user_data with earnings
                if not context.user_data:
                    # Fix: Cannot assign new value to user_data
                    if hasattr(context, 'user_data') and context.user_data is not None:
                        context.user_data.clear()
                    # If user_data is None, work with existing state
                context.user_data['total_earnings'] = total_fmt
                
                logger.info(f"Background earnings loaded for user {user_id}: {total_fmt}")
                
        except Exception as e:
            logger.error(f"Error loading background earnings for user {user_id}: {e}")
    
    @staticmethod
    async def schedule_background_load(user_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Schedule background data loading"""
        asyncio.create_task(
            AsyncWalletLoader.load_earnings_data(user_id, context)
        )