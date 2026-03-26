"""
Auto-refresh service to replace manual refresh buttons across LockBay
Handles automatic status updates for exchanges, trades, and wallets
"""

import logging
from datetime import datetime, timedelta
from typing import Dict
from sqlalchemy.orm import sessionmaker
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import engine
from models import User, ExchangeOrder, Escrow
# Removed imports - formatting done inline to avoid circular imports

logger = logging.getLogger(__name__)

class AutoRefreshService:
    """Service to manage automatic refresh of dynamic content"""
    
    def __init__(self):
        self.active_sessions: Dict[str, Dict] = {}
        self.refresh_intervals = {
            'exchange_order': 30,  # 30 seconds
            'trade_status': 45,    # 45 seconds
            'wallet_balance': 60,  # 60 seconds
        }
        self.Session = sessionmaker(bind=engine)
        
    def register_auto_refresh(
        self, 
        chat_id: int, 
        message_id: int, 
        content_type: str,
        content_id: str,
        user_id: int,
        callback_data: str = None
    ):
        """Register a message for auto-refresh"""
        session_key = f"{chat_id}_{message_id}"
        
        self.active_sessions[session_key] = {
            'chat_id': chat_id,
            'message_id': message_id,
            'content_type': content_type,
            'content_id': content_id,
            'user_id': user_id,
            'callback_data': callback_data,
            'last_refresh': datetime.utcnow(),
            'refresh_count': 0,
            'max_refreshes': 120  # Stop after 120 refreshes (1 hour at 30s intervals)
        }
        
        logger.info(f"🔄 Auto-refresh registered: {content_type} #{content_id} for chat {chat_id}")

    def unregister_auto_refresh(self, chat_id: int, message_id: int):
        """Remove a message from auto-refresh"""
        session_key = f"{chat_id}_{message_id}"
        if session_key in self.active_sessions:
            del self.active_sessions[session_key]
            logger.info(f"🔄 Auto-refresh unregistered for chat {chat_id}, message {message_id}")

    async def refresh_exchange_order(self, session_info: Dict, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Refresh exchange order status"""
        try:
            with self.Session() as db_session:
                exchange = db_session.query(ExchangeOrder).filter(
                    ExchangeOrder.id == int(session_info['content_id'])
                ).first()
                
                if not exchange:
                    logger.warning(f"Exchange order {session_info['content_id']} not found")
                    return False
                
                # Format updated exchange details (recreate the same format as view handler)
                status_emoji = {
                    'created': '📋',
                    'awaiting_deposit': '⏳',
                    'payment_received': '💰',
                    'processing': '⚡',
                    'completed': '✅',
                    'failed': '❌',
                    'cancelled': '🚫'
                }.get(exchange.status, '📋')
                
                # Format amounts
                source_amount = getattr(exchange, 'source_amount', 0)
                final_amount = getattr(exchange, 'final_amount', 0)
                source_currency = getattr(exchange, 'source_currency', 'USD')
                target_currency = getattr(exchange, 'target_currency', 'NGN')
                
                details_text = f"{status_emoji} *Exchange Order Details*\n\n"
                details_text += f"*Order:* #{getattr(exchange, 'utid', 'N/A')}\n"
                
                # Escape underscores in status to prevent Markdown conflicts
                status_display = exchange.status.replace('_', ' ').title()
                details_text += f"*Status:* {status_display}\n"
                details_text += f"*Amount:* {source_amount:.4f} {source_currency} → "
                
                if target_currency == 'NGN':
                    details_text += f"₦{final_amount:,.2f}\n"
                else:
                    details_text += f"{final_amount:.4f} {target_currency}\n"
                    
                # Add timestamps (handle None values)
                created_at = getattr(exchange, 'created_at', None)
                if created_at:
                    details_text += f"*Created:* {created_at.strftime('%Y-%m-%d %H:%M')}\n"
                    
                completed_at = getattr(exchange, 'completed_at', None)  
                if completed_at:
                    details_text += f"*Completed:* {completed_at.strftime('%Y-%m-%d %H:%M')}\n"
                
                # Add bank reference if available
                if hasattr(exchange, 'bank_reference') and exchange.bank_reference:
                    details_text += f"*Bank Ref:* {exchange.bank_reference}\n"
                
                # Create keyboard without refresh button
                keyboard = [
                    [InlineKeyboardButton("⬅️ Back to Messages", callback_data="trades_messages_hub")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ]
                
                # Add status-specific buttons
                if exchange.status == 'completed':
                    keyboard.insert(0, [
                        InlineKeyboardButton("📄 View Receipt", callback_data=f"exchange_receipt_{exchange.id}")
                    ])
                elif exchange.status in ['failed', 'expired']:
                    keyboard.insert(0, [
                        InlineKeyboardButton("🔄 New Exchange", callback_data="start_exchange")
                    ])
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Update the message using bot instance
                try:
                    await context.bot.edit_message_text(
                        chat_id=session_info['chat_id'],
                        message_id=session_info['message_id'],
                        text=details_text,
                        reply_markup=reply_markup,
                        parse_mode='Markdown'
                    )
                except Exception as edit_error:
                    logger.warning(f"Failed to edit message in auto-refresh: {edit_error}")
                    return False  # Stop refreshing if we can't edit the message
                
                # Check if we should stop auto-refreshing
                if exchange.status in ['completed', 'failed', 'expired', 'cancelled']:
                    logger.info(f"🔄 Stopping auto-refresh for completed exchange {exchange.id}")
                    return False  # Stop refreshing
                    
                return True  # Continue refreshing
                
        except Exception as e:
            logger.error(f"Error refreshing exchange order: {e}")
            return True  # Continue refreshing despite error

    async def refresh_trade_status(self, session_info: Dict, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Refresh trade/escrow status"""
        try:
            with self.Session() as db_session:
                trade = db_session.query(Escrow).filter(
                    Escrow.id == int(session_info['content_id'])
                ).first()
                
                if not trade:
                    logger.warning(f"Trade {session_info['content_id']} not found")
                    return False
                
                # Format updated trade display (recreate inline to avoid circular imports)
                user_role = "buyer" if trade.buyer_id == session_info['user_id'] else "seller"
                counterpart = trade.seller if user_role == "buyer" else trade.buyer
                counterpart_name = getattr(counterpart, 'name', 'Unknown') if counterpart else 'Unknown'
                
                status_emoji = {
                    'created': '📋',
                    'awaiting_payment': '💰',
                    'payment_confirmed': '✅',
                    'active': '🔄',
                    'completed': '✅',
                    'cancelled': '🚫',
                    'disputed': '⚠️'
                }.get(trade.status, '📋')
                
                trade_text = f"{status_emoji} *Trade #{trade.id}*\n\n"
                trade_text += f"*Status:* {trade.status.replace('_', ' ').title()}\n"
                trade_text += f"*Amount:* ${trade.amount:.2f} USD\n"
                trade_text += f"*Your Role:* {user_role.title()}\n"
                trade_text += f"*Partner:* {counterpart_name}\n"
                if trade.description:
                    trade_text += f"*Description:* {trade.description[:50]}{'...' if len(trade.description) > 50 else ''}\n"
                
                # Create keyboard without refresh button
                keyboard = [
                    [InlineKeyboardButton("⬅️ Back to Messages", callback_data="trades_messages_hub")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ]
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Update the message using bot instance
                try:
                    await context.bot.edit_message_text(
                        chat_id=session_info['chat_id'],
                        message_id=session_info['message_id'],
                        text=trade_text,
                        reply_markup=reply_markup,
                        parse_mode='Markdown'
                    )
                except Exception as edit_error:
                    logger.warning(f"Failed to edit trade message in auto-refresh: {edit_error}")
                    return False  # Stop refreshing if we can't edit the message
                
                # Check if we should stop auto-refreshing
                if trade.status in ['completed', 'cancelled', 'failed']:
                    logger.info(f"🔄 Stopping auto-refresh for completed trade {trade.id}")
                    return False  # Stop refreshing
                    
                return True  # Continue refreshing
                
        except Exception as e:
            logger.error(f"Error refreshing trade status: {e}")
            return True  # Continue refreshing despite error

    async def refresh_wallet_balance(self, session_info: Dict, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Refresh wallet balance display"""
        try:
            with self.Session() as db_session:
                user = db_session.query(User).filter(
                    User.id == session_info['user_id']
                ).first()
                
                if not user:
                    logger.warning(f"User {session_info['user_id']} not found")
                    return False
                
                # Import wallet handler to format balance display
                from handlers.wallet import WalletHandler
                wallet_text = await WalletHandler.format_wallet_display(user)
                
                # Create keyboard without refresh button
                keyboard = [
                    [InlineKeyboardButton("💰 Deposit", callback_data="wallet_deposit")],
                    [InlineKeyboardButton("💸 Withdraw", callback_data="wallet_withdraw")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ]
                
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                # Update the message using bot instance
                try:
                    await context.bot.edit_message_text(
                        chat_id=session_info['chat_id'],
                        message_id=session_info['message_id'],
                        text=wallet_text,
                        reply_markup=reply_markup,
                        parse_mode='Markdown'
                    )
                except Exception as edit_error:
                    logger.warning(f"Failed to edit wallet message in auto-refresh: {edit_error}")
                    return False  # Stop refreshing if we can't edit the message
                
                return True  # Continue refreshing
                
        except Exception as e:
            logger.error(f"Error refreshing wallet balance: {e}")
            return True  # Continue refreshing despite error

    async def process_auto_refreshes(self, context: ContextTypes.DEFAULT_TYPE):
        """Process all active auto-refresh sessions"""
        current_time = datetime.utcnow()
        sessions_to_remove = []
        
        for session_key, session_info in list(self.active_sessions.items()):
            try:
                # Check if it's time to refresh
                time_since_refresh = (current_time - session_info['last_refresh']).total_seconds()
                refresh_interval = self.refresh_intervals.get(session_info['content_type'], 60)
                
                if time_since_refresh < refresh_interval:
                    continue
                
                # Check if we've exceeded max refreshes
                if session_info['refresh_count'] >= session_info['max_refreshes']:
                    logger.info(f"🔄 Max refreshes reached for {session_key}")
                    sessions_to_remove.append(session_key)
                    continue
                
                # Process refresh based on content type
                should_continue = True
                if session_info['content_type'] == 'exchange_order':
                    should_continue = await self.refresh_exchange_order(session_info, context)
                elif session_info['content_type'] == 'trade_status':
                    should_continue = await self.refresh_trade_status(session_info, context)
                elif session_info['content_type'] == 'wallet_balance':
                    should_continue = await self.refresh_wallet_balance(session_info, context)
                
                if not should_continue:
                    sessions_to_remove.append(session_key)
                else:
                    # Update refresh tracking
                    session_info['last_refresh'] = current_time
                    session_info['refresh_count'] += 1
                    
            except Exception as e:
                logger.error(f"Error processing auto-refresh for {session_key}: {e}")
                # Remove problematic sessions
                sessions_to_remove.append(session_key)
        
        # Clean up completed sessions
        for session_key in sessions_to_remove:
            self.unregister_auto_refresh(*session_key.split('_', 1))
        
        logger.debug(f"🔄 Processed {len(self.active_sessions)} auto-refresh sessions")

    def get_active_sessions_count(self) -> int:
        """Get count of active auto-refresh sessions"""
        return len(self.active_sessions)

    def cleanup_old_sessions(self):
        """Clean up sessions older than 2 hours"""
        current_time = datetime.utcnow()
        cutoff_time = current_time - timedelta(hours=2)
        
        sessions_to_remove = []
        for session_key, session_info in self.active_sessions.items():
            if session_info['last_refresh'] < cutoff_time:
                sessions_to_remove.append(session_key)
        
        for session_key in sessions_to_remove:
            del self.active_sessions[session_key]
        
        if sessions_to_remove:
            logger.info(f"🧹 Cleaned up {len(sessions_to_remove)} old auto-refresh sessions")


# Global auto-refresh service instance
auto_refresh_service = AutoRefreshService()