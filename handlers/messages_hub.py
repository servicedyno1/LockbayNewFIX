"""
Unified Messages Hub - Modern chat interface for all communication types
Exchange-style design with polished UX for trades, disputes, and notifications
"""

import logging
from typing import Dict
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import desc, func, or_, and_

from database import SessionLocal
from models import (
    User, Escrow, EscrowMessage, Dispute, DisputeMessage,
    EscrowStatus, ExchangeOrder
)
from utils.callback_utils import safe_edit_message_text, safe_answer_callback_query
from utils.helpers import get_user_display_name
from utils.admin_security import is_admin_secure, is_admin_silent
from utils.universal_session_manager import (
    universal_session_manager, SessionType, OperationStatus
)
from utils.comprehensive_audit_logger import (
    ComprehensiveAuditLogger, AuditEventType, RelatedIDs, PayloadMetadata
)
from utils.handler_decorators import audit_conversation_handler
# DATABASE-BACKED STATE CHECKING
from handlers.wallet_direct import has_active_cashout_db_by_telegram
# ONBOARDING PROTECTION
from utils.route_guard import OnboardingProtection

logger = logging.getLogger(__name__)

# Initialize communication audit logger
communication_audit = ComprehensiveAuditLogger("communication")

# Conversation states (use integers directly for clarity)
CHAT_VIEW = 1
MESSAGE_INPUT = 2

# Legacy active chat sessions - migrating to universal_session_manager
active_chat_sessions: Dict[int, Dict] = {}  # Deprecated - use universal_session_manager


@audit_conversation_handler("messages_hub_entry")
async def show_trades_messages_hub(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Modern messages hub with exchange-style interface - ENHANCED WITH STATE CLEANUP"""
    user = update.effective_user
    if not user:
        return ConversationHandler.END

    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💬")
    
    # FIXED: Selective conversation state cleanup - preserve active cashout sessions
    if context.user_data:
        # CRITICAL FIX: Check for active cashout sessions before clearing wallet_data
        cud = context.user_data  # Shorthand for readability
        
        # DATABASE-BACKED + TTL INTEGRATION: Check authoritative database state first
        has_active_db_cashout = await has_active_cashout_db_by_telegram(user.id, context)
        
        has_active_cashout = (
            # ENHANCED: Database-backed check is authoritative (prevents false negatives)
            has_active_db_cashout or
            # Original session flags (for immediate states)
            cud.get('pending_address_save') or 
            cud.get('pending_cashout') or
            # EXPANDED: Check for active cashout data and states
            bool(cud.get('cashout_data', {}).get('active_cashout')) or
            cud.get('wallet_state') in [
                'selecting_amount', 'selecting_amount_crypto', 'selecting_amount_ngn', 
                'entering_crypto_address', 'entering_crypto_details', 'verifying_crypto_otp', 
                'verifying_ngn_otp', 'adding_bank_selecting', 'adding_bank_account_number', 
                'adding_bank_confirming', 'adding_bank_label', 'adding_bank_searching', 
                'entering_custom_amount', 'entering_withdraw_address'
            ] or
            cud.get('current_state') == 'ENTERING_CUSTOM_AMOUNT' or
            cud.get('wallet_data', {}).get('state') in [
                'selecting_amount', 'selecting_amount_crypto', 'selecting_amount_ngn',
                'entering_crypto_address', 'entering_crypto_details', 'verifying_crypto_otp',
                'verifying_ngn_otp', 'entering_custom_amount', 'entering_withdraw_address'
            ] or
            # Check for cashout data presence indicating active flow
            bool(cud.get('cashout_data', {}).get('amount'))
        )
        
        if has_active_db_cashout:
            logger.info(f"🔒 Messages hub: Database shows active cashout - preserving session for user {user.id}")
        
        if has_active_cashout:
            logger.info(f"🔒 Messages hub: Preserving active cashout session for user {user.id}")
            # Clear conversation states but preserve wallet_data for active cashouts
            context.user_data.pop("active_conversation", None)
            context.user_data.pop("exchange_data", None)
            context.user_data.pop("exchange_session_id", None) 
            context.user_data.pop("escrow_data", None)
            context.user_data.pop("contact_data", None)
            # NOTE: Preserving wallet_data to maintain active cashout session
            context.user_data.pop("expecting_funding_amount", None)
            context.user_data.pop("expecting_custom_amount", None)
            logger.debug("🧹 Messages hub: Cleared conversation states (preserved active cashout)")
        else:
            # Clear ALL conversation states when no active cashout
            context.user_data.pop("active_conversation", None)
            context.user_data.pop("exchange_data", None)
            context.user_data.pop("exchange_session_id", None) 
            context.user_data.pop("escrow_data", None)
            context.user_data.pop("contact_data", None)
            context.user_data.pop("wallet_data", None)
            context.user_data.pop("expecting_funding_amount", None)
            context.user_data.pop("expecting_custom_amount", None)
            logger.debug("🧹 Messages hub: Cleared all conversation states")
    
    # ENHANCED: Clear universal session manager sessions (PROTECTED - exclude onboarding)
    if user:
        try:
            from utils.universal_session_manager import universal_session_manager
            
            # ONBOARDING PROTECTION: Check if user has active onboarding before clearing sessions
            should_protect_onboarding = await OnboardingProtection.should_block_processing(user.id, "messages_hub_cleanup")
            
            if should_protect_onboarding:
                logger.info(f"🔒 ONBOARDING PROTECTION: Preserving all sessions for user {user.id} (active onboarding)")
            else:
                user_session_ids = universal_session_manager.get_user_session_ids(user.id)
                if user_session_ids:
                    logger.info(f"🧹 Messages hub: Clearing {len(user_session_ids)} universal sessions")
                    
                    # Filter out onboarding sessions for safety
                    cleared_count = 0
                    for session_id in user_session_ids:
                        try:
                            session_info = universal_session_manager.get_session(session_id)
                            if session_info and session_info.get('type') != SessionType.ONBOARDING:
                                universal_session_manager.terminate_session(session_id, "messages_hub_navigation")
                                cleared_count += 1
                            else:
                                logger.debug(f"🔒 PROTECTION: Preserved onboarding session {session_id}")
                        except Exception as session_error:
                            logger.warning(f"Error checking session {session_id}: {session_error}")
                            # Be conservative - don't terminate if we can't verify type
                    
                    logger.info(f"✅ Messages hub: {cleared_count} universal sessions cleaned (onboarding preserved)")
                else:
                    logger.debug("🧹 Messages hub: No universal sessions to clear")
        except Exception as e:
            logger.warning(f"Could not clear universal sessions in messages hub: {e}")

    # Add retry logic for database connection issues
    import asyncio
    max_retries = 3
    db_user = None
    
    for attempt in range(max_retries):
        session = SessionLocal()
        try:
            # Get user from database
            db_user = session.query(User).filter(User.telegram_id == user.id).first()
            if db_user:
                break  # Success, exit retry loop
            session.close()
        except Exception as e:
            session.close()
            if attempt < max_retries - 1:
                logger.warning(f"Database connection error (attempt {attempt + 1}): {e}")
                await asyncio.sleep(0.5)  # Brief delay before retry
                continue
            else:
                logger.error(f"Failed to connect to database after {max_retries} attempts: {e}")
                if query:
                    await safe_edit_message_text(query, "⚠️ Database connection issue. Please try again.")
                return ConversationHandler.END
    
    # Continue with the session that worked
    try:
        if not db_user:
            if query:
                await safe_edit_message_text(query, "❌ User not found. Please restart with /start")
            return

        # === MODERN DASHBOARD METRICS ===
        now = datetime.utcnow()
        
        # Active trade conversations (only ongoing trades, not completed/finished ones)
        # CRITICAL FIX: Include pending invitations for sellers using typed contact fields
        # UNIFIED LOOKUP: Include both Escrow and ExchangeOrder records
        seller_invitation_filters = []
        
        # Check for seller invitations using typed contact fields
        if db_user.email:
            seller_invitation_filters.append(
                and_(Escrow.seller_contact_type == 'email', 
                     func.lower(Escrow.seller_contact_value) == func.lower(db_user.email))
            )
        if db_user.username:
            seller_invitation_filters.append(
                and_(Escrow.seller_contact_type == 'username', 
                     func.lower(Escrow.seller_contact_value) == func.lower(db_user.username))
            )
        if hasattr(db_user, 'phone') and db_user.phone:
            seller_invitation_filters.append(
                and_(Escrow.seller_contact_type == 'phone', 
                     Escrow.seller_contact_value == db_user.phone)
            )
        
        escrow_trades = session.query(Escrow).filter(
            or_(
                Escrow.buyer_id == db_user.id,
                Escrow.seller_id == db_user.id,
                *seller_invitation_filters
            ),
            Escrow.status.in_(['created', 'payment_pending', 'payment_confirmed', 'partial_payment', 'active', 'disputed'])
        ).count()
        
        # Count exchange orders for the same user
        exchange_trades = session.query(ExchangeOrder).filter(
            ExchangeOrder.user_id == db_user.id
        ).count()
        
        active_trades = escrow_trades + exchange_trades
        
        # Active disputes - FIXED: Include pending invitations using typed contact fields
        active_disputes = session.query(Dispute).join(Escrow, Dispute.escrow_id == Escrow.id).filter(
            or_(
                Escrow.buyer_id == db_user.id,
                Escrow.seller_id == db_user.id,
                *seller_invitation_filters
            ),
            Dispute.status.in_(['open', 'under_review'])
        ).count()
        
        # Unread messages (last 24h) - FIXED: Include pending invitations using typed contact fields
        recent_cutoff = now - timedelta(hours=24)
        unread_trade_msgs = session.query(EscrowMessage).join(Escrow, EscrowMessage.escrow_id == Escrow.id).filter(
            or_(
                Escrow.buyer_id == db_user.id,
                Escrow.seller_id == db_user.id,
                *seller_invitation_filters
            ),
            EscrowMessage.sender_id != db_user.id,
            EscrowMessage.created_at >= recent_cutoff
        ).count()
        
        # Temporarily disable dispute message counting until schema is stable
        unread_dispute_msgs = 0
        # unread_dispute_msgs = session.query(DisputeMessage).join(Dispute).join(Escrow, Dispute.escrow_id == Escrow.id).filter(
        #     or_(Escrow.buyer_id == db_user.id, Escrow.seller_id == db_user.id),
        #     DisputeMessage.sender_id != db_user.id,
        #     DisputeMessage.created_at >= recent_cutoff
        # ).count()
        
        unread_trade_msgs + unread_dispute_msgs
        
        # Get recent activity for unified view - FIXED: Include pending invitations
        # Get recent escrow trades using typed contact fields
        recent_escrow_trades = session.query(Escrow).filter(
            or_(
                Escrow.buyer_id == db_user.id,
                Escrow.seller_id == db_user.id,
                *seller_invitation_filters
            )
        ).order_by(Escrow.updated_at.desc()).limit(3).all()
        
        # Get recent exchange orders
        recent_exchange_trades = session.query(ExchangeOrder).filter(
            ExchangeOrder.user_id == db_user.id
        ).order_by(ExchangeOrder.updated_at.desc()).limit(3).all()
        
        # Combine and sort by updated_at (most recent first)
        all_recent_trades = []
        
        # Add escrow trades with type identifier
        for trade in recent_escrow_trades:
            all_recent_trades.append({
                'type': 'escrow',
                'record': trade,
                'updated_at': trade.updated_at
            })
            
        # Add exchange trades with type identifier  
        for trade in recent_exchange_trades:
            all_recent_trades.append({
                'type': 'exchange',
                'record': trade,
                'updated_at': trade.updated_at
            })
        
        # Sort combined list by updated_at and take top 5 (handle None values safely)
        all_recent_trades.sort(key=lambda x: x['updated_at'] or datetime.min, reverse=True)
        all_recent_trades[:5]
        
        # === COMPREHENSIVE TRADE OVERVIEW ===
        # Count escrow vs exchange breakdown using typed contact fields
        active_escrows = session.query(Escrow).filter(
            or_(
                Escrow.buyer_id == db_user.id,
                Escrow.seller_id == db_user.id,
                *seller_invitation_filters
            ),
            Escrow.status.in_(['created', 'payment_pending', 'payment_confirmed', 'partial_payment', 'active'])
        ).count()
        
        active_exchanges = session.query(ExchangeOrder).filter(
            ExchangeOrder.user_id == db_user.id,
            ExchangeOrder.status.in_(['created', 'payment_pending', 'processing', 'active'])
        ).count()
        
        total_escrows = session.query(Escrow).filter(
            or_(
                Escrow.buyer_id == db_user.id,
                Escrow.seller_id == db_user.id,
                *seller_invitation_filters
            )
        ).count()
        
        total_exchanges = session.query(ExchangeOrder).filter(
            ExchangeOrder.user_id == db_user.id
        ).count()
        
        if active_trades > 0:
            message = f"""💰 My Trading Dashboard

🤝 Escrows: {active_escrows} active • {total_escrows} total
🔄 Exchanges: {active_exchanges} active • {total_exchanges} total

Ready to chat or take action"""
        elif active_disputes > 0:
            message = f"""⚠️ {active_disputes} Open Dispute{'s' if active_disputes != 1 else ''}

Support team is reviewing"""
        else:
            if total_escrows + total_exchanges > 0:
                message = f"""🤝 My Trading Dashboard

🤝 Escrows: {total_escrows} total
🔄 Exchanges: {total_exchanges} total

No active trades • Ready for new transactions"""
            else:
                message = """🤝 Ready to Trade

Start your first secure transaction
• Escrow for P2P trades
• Exchange for crypto conversions"""

        # === CLEAN SIMPLE BUTTONS ===
        keyboard = []
        
        # Primary action - only most important
        if active_trades > 0:
            keyboard.append([
                InlineKeyboardButton(f"💬 My Trades ({active_trades})", callback_data="view_active_trades")
            ])
        else:
            keyboard.append([
                InlineKeyboardButton("🤝 Start Trading", callback_data="menu_create")
            ])
        
        # ALWAYS show disputes button when they exist (separate from primary action)
        if active_disputes > 0:
            keyboard.append([
                InlineKeyboardButton(f"⚠️ Disputes ({active_disputes})", callback_data="view_disputes")
            ])
        
        # Simple secondary actions
        keyboard.append([
            InlineKeyboardButton("📋 History", callback_data="view_trade_history"),
            InlineKeyboardButton("💬 Support", callback_data="contact_support")
        ])
        
        keyboard.append([
            InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")
        ])
        
    finally:
        session.close()
    
    # Use unified message handling
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Handle both callback queries and direct commands
    from utils.message_utils import send_unified_message
    if query:
        try:
            await safe_edit_message_text(query, message, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Error editing message: {e}")
    else:
        await send_unified_message(update, message, reply_markup=reply_markup, parse_mode="Markdown")
    
    # CRITICAL FIX: This is NOT a conversation handler, so should not return ConversationHandler.END
    # Simply return None for standalone callback handlers
    return


async def handle_quick_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle quick message action - show message sending interface"""
    user = update.effective_user
    if not user:
        return
        
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💬")
    
    session = SessionLocal()
    try:
        db_user = session.query(User).filter(User.telegram_id == user.id).first()
        if not db_user:
            return
            
        # Create seller invitation filters for quick message function
        seller_invitation_filters = []
        if db_user.email:
            seller_invitation_filters.append(
                and_(Escrow.seller_contact_type == 'email', 
                     func.lower(Escrow.seller_contact_value) == func.lower(db_user.email))
            )
        if db_user.username:
            seller_invitation_filters.append(
                and_(Escrow.seller_contact_type == 'username', 
                     func.lower(Escrow.seller_contact_value) == func.lower(db_user.username))
            )
        if hasattr(db_user, 'phone') and db_user.phone:
            seller_invitation_filters.append(
                and_(Escrow.seller_contact_type == 'phone', 
                     Escrow.seller_contact_value == db_user.phone)
            )
            
        # Get active trades for messaging (only truly active statuses)
        active_trades = session.query(Escrow).filter(
            or_(
                Escrow.buyer_id == db_user.id,
                Escrow.seller_id == db_user.id,
                *seller_invitation_filters
            ),
            Escrow.status.in_(['created', 'payment_pending', 'payment_confirmed', 'partial_payment', 'active', 'disputed'])
        ).order_by(Escrow.updated_at.desc()).all()
        
        if not active_trades:
            message = """💬 Send Message

❌ No Active Trades
You need an active trade to send messages.

💡 What you can do:
• Create a new trade to start communicating
• Check your trade history for completed trades"""
            
            keyboard = [
                [InlineKeyboardButton("🤝 Create New Trade", callback_data="menu_create")],
                [InlineKeyboardButton("📋 Trade History", callback_data="view_trade_history")],
                [InlineKeyboardButton("⬅️ Back", callback_data="trades_messages_hub")]
            ]
        else:
            # Limit to 5 most recent trades for better UX
            recent_trades = active_trades[:5]
            total_count = len(active_trades)
            
            if total_count > 5:
                message = f"""💬 Send Message

📋 Recent Active Trades ({len(recent_trades)} of {total_count}):

Showing your 5 most recent trades:"""
            else:
                message = """💬 Send Message

💬 Select a trade to chat with:

Choose which trade you'd like to open chat for:"""

            keyboard = []
            
            for trade in recent_trades:
                role = "Buyer" if trade.buyer_id == db_user.id else "Seller"
                amount = float(trade.amount) if trade.amount else 0
                
                # Get counterparty name using typed contact fields
                counterparty_id = trade.seller_id if trade.buyer_id == db_user.id else trade.buyer_id
                counterparty = session.query(User).filter(User.id == counterparty_id).first()
                if counterparty and counterparty.username:
                    counterparty_name = counterparty.username
                elif trade.buyer_id == db_user.id and getattr(trade, 'seller_contact_display', None):
                    # Buyer viewing seller - use seller_contact_display for non-platform sellers
                    counterparty_name = trade.seller_contact_display
                elif trade.buyer_id == db_user.id and getattr(trade, 'seller_username', None):
                    # Fallback to old seller_username field
                    counterparty_name = f"@{trade.seller_username}"
                else:
                    counterparty_name = "User"
                
                # Include trade ID for user identification
                keyboard.append([
                    InlineKeyboardButton(
                        f"#{trade.escrow_id[:12]} • ${amount:.0f} • @{counterparty_name[:8]}{'...' if len(counterparty_name) > 8 else ''}",
                        callback_data=f"trade_chat_open:{trade.id}"
                    )
                ])
            
            # Add "View All Trades" if there are more
            if total_count > 5:
                keyboard.append([
                    InlineKeyboardButton("📋 View All Trades", callback_data="view_active_trades")
                ])
            
            # Navigation
            keyboard.append([
                InlineKeyboardButton("⬅️ Back", callback_data="trades_messages_hub")
            ])
            
    finally:
        session.close()
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        try:
            await safe_edit_message_text(query, message, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Error editing quick message: {e}")
    else:
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")


# REMOVED: handle_send_message_to_trade - consolidated into open_trade_chat
# async def handle_send_message_to_trade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # """Handle sending a message to a specific trade"""
    user = update.effective_user
    if not user:
        return
        
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💬")
        
        # Extract trade ID from callback data
        trade_id = query.data.replace("send_message_", "")
    else:
        return
    
    session = SessionLocal()
    try:
        db_user = session.query(User).filter(User.telegram_id == user.id).first()
        if not db_user:
            return
            
        # Get the specific trade
        trade = session.query(Escrow).filter(
            Escrow.escrow_id == trade_id,
            or_(Escrow.buyer_id == db_user.id, Escrow.seller_id == db_user.id)
        ).first()
        
        if not trade:
            await safe_edit_message_text(query, "❌ Trade not found or you don't have permission to access it.")
            return
            
        # Get trade details
        role = "Buyer" if trade.buyer_id == db_user.id else "Seller"
        amount = float(trade.amount) if trade.amount else 0
        
        # Get counterparty
        counterparty_id = trade.seller_id if trade.buyer_id == db_user.id else trade.buyer_id
        counterparty = session.query(User).filter(User.id == counterparty_id).first()
        counterparty_name = counterparty.username if counterparty and counterparty.username else "User"
        
        message = f"""💬 Send Message

📋 Trade: #{trade.escrow_id[:12]}
👤 To: @{counterparty_name} ({role == 'Buyer' and 'Seller' or 'Buyer'})
💰 Amount: ${amount:.2f}

📝 Type your message below:
Write anything you want to communicate about this trade."""

        keyboard = [
            [InlineKeyboardButton("❌ Cancel", callback_data="quick_message")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]
        
        # Store trade context for message handling
        context.user_data["sending_message_to_trade"] = trade_id
        context.user_data["message_context"] = {
            "trade_id": trade_id,
            "counterparty_name": counterparty_name,
            "amount": amount
        }
            
    finally:
        session.close()
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await safe_edit_message_text(query, message, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Error in send message to trade: {e}")


async def show_active_trades(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show detailed view of active trades with messaging (paginated and with escrow IDs)"""
    user = update.effective_user
    if not user:
        return
        
    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "🔥")
    
    # Get page number from callback data
    if query and query.data.startswith("view_active_trades_page_"):
        try:
            int(query.data.split("_")[-1])
        except (ValueError, IndexError):
            pass
    
    session = SessionLocal()
    try:
        db_user = session.query(User).filter(User.telegram_id == user.id).first()
        if not db_user:
            return
            
        # Get active trades with pagination (UNIFIED APPROACH)
        TRADES_PER_PAGE = 8  # Limit to prevent long pages
        
        # Get ALL trades for the user including cancelled/refunded (unified query)
        # CRITICAL FIX: Include pending invitations for sellers (seller_username/seller_email)
        # Create seller invitation filters for this function
        seller_invitation_filters = []
        if db_user.email:
            seller_invitation_filters.append(
                and_(Escrow.seller_contact_type == 'email', 
                     func.lower(Escrow.seller_contact_value) == func.lower(db_user.email))
            )
        if db_user.username:
            seller_invitation_filters.append(
                and_(Escrow.seller_contact_type == 'username', 
                     func.lower(Escrow.seller_contact_value) == func.lower(db_user.username))
            )
        if hasattr(db_user, 'phone') and db_user.phone:
            seller_invitation_filters.append(
                and_(Escrow.seller_contact_type == 'phone', 
                     Escrow.seller_contact_value == db_user.phone)
            )

        # UNIFIED LOOKUP: Include both Escrow and ExchangeOrder records using typed contact fields
        # Get more records to ensure we can fill TRADES_PER_PAGE after combining and sorting
        escrow_trades = session.query(Escrow).filter(
            or_(
                Escrow.buyer_id == db_user.id,
                Escrow.seller_id == db_user.id,
                *seller_invitation_filters
            ),
            Escrow.status.in_(['created', 'payment_pending', 'payment_confirmed', 'partial_payment', 'active', 'completed', 'disputed', 'cancelled', 'refunded', 'declined'])
        ).order_by(Escrow.updated_at.desc()).limit(TRADES_PER_PAGE * 2).all()
        
        # Get exchange orders for the same user
        exchange_trades = session.query(ExchangeOrder).filter(
            ExchangeOrder.user_id == db_user.id
        ).order_by(ExchangeOrder.updated_at.desc()).limit(TRADES_PER_PAGE * 2).all()
        
        # Combine trades with type identifiers
        combined_trades = []
        
        # Add escrow trades
        for trade in escrow_trades:
            combined_trades.append({
                'type': 'escrow',
                'record': trade,
                'updated_at': trade.updated_at
            })
            
        # Add exchange trades
        for trade in exchange_trades:
            combined_trades.append({
                'type': 'exchange', 
                'record': trade,
                'updated_at': trade.updated_at
            })
        
        # Sort by updated_at and limit to page size (handle None values safely)
        combined_trades.sort(key=lambda x: x['updated_at'] or datetime.min, reverse=True)
        all_active_trades = combined_trades[:TRADES_PER_PAGE]
        
        total_trades = len(all_active_trades)
        
        if total_trades == 0:
            message = """🔥 Active Trades

└─ No active trades

Ready to start trading?"""
            keyboard = [
                [InlineKeyboardButton("🤝 Create New Trade", callback_data="menu_create")],
                [InlineKeyboardButton("⬅️ Back", callback_data="trades_messages_hub")]
            ]
        else:
            # UNIFIED DISPLAY FORMAT for all users (buyers and sellers)
            message = f"""💰 {total_trades} Recent Trade{'s' if total_trades != 1 else ''}

"""
            
            keyboard = []
            
            # UNIFIED trade list with status icons (same for buyers and sellers)
            for i, trade_item in enumerate(all_active_trades, 1):
                trade_type = trade_item['type']
                trade = trade_item['record']
                
                if trade_type == 'escrow':
                    # Handle escrow trades
                    amount = float(trade.amount) if trade.amount else 0
                    
                    # Get counterparty name (with typed contact fields support)
                    counterparty_id = trade.seller_id if trade.buyer_id == db_user.id else trade.buyer_id
                    counterparty = session.query(User).filter(User.id == counterparty_id).first()
                    if counterparty and counterparty.username:
                        counterparty_name = counterparty.username
                    elif trade.buyer_id == db_user.id and getattr(trade, 'seller_contact_display', None):
                        # Buyer viewing seller - use seller_contact_display for typed contact fields
                        counterparty_name = trade.seller_contact_display
                    elif trade.buyer_id == db_user.id and getattr(trade, 'seller_username', None):
                        # Fallback to old seller_username if seller hasn't accepted yet
                        counterparty_name = f"@{trade.seller_username}"
                    else:
                        counterparty_name = "User"
                    
                    # Check if cancelled trades have refunds
                    is_refunded = False
                    if trade.status == 'cancelled':
                        from models import Transaction
                        refund_transaction = session.query(Transaction).filter(
                            Transaction.escrow_id == trade.id,
                            Transaction.transaction_type.in_(["refund", "escrow_refund"]),
                            Transaction.status == "completed"
                        ).first()
                        is_refunded = refund_transaction is not None
                    
                    status_icons = {
                        'created': '🆕',  'payment_pending': '⏳',  'payment_confirmed': '🔔',
                        'partial_payment': '🟡', 'active': '🔵', 'disputed': '⚠️',
                        'completed': '🟢', 'cancelled': '❌💵' if is_refunded else '❌',
                        'refunded': '↩️', 'expired': '⏰'
                    }
                    status_icon = status_icons.get(trade.status, '🔄')
                    trade_display = trade.escrow_id[-6:] if trade.escrow_id else str(trade.id)
                    display_text = f"{status_icon} #{trade_display} • ${amount:.0f} with {counterparty_name}"
                    # Use database ID for callback consistency with handler
                    callback_data = f"view_trade_{trade.id}"
                    
                elif trade_type == 'exchange':
                    # Handle exchange orders
                    amount = float(trade.amount) if hasattr(trade, 'amount') and trade.amount else 0
                    crypto_symbol = trade.from_currency if hasattr(trade, 'from_currency') else 'CRYPTO'
                    fiat_symbol = trade.to_currency if hasattr(trade, 'to_currency') else 'NGN'
                    
                    # Exchange status icons
                    exchange_status_icons = {
                        'created': '🔄', 'awaiting_deposit': '⏳', 'processing': '🔄',
                        'completed': '✅', 'cancelled': '❌', 'expired': '⏰'
                    }
                    status_icon = exchange_status_icons.get(trade.status, '🔄')
                    
                    display_text = f"{status_icon} Exchange: {amount} {crypto_symbol} → {fiat_symbol}"
                    callback_data = f"view_exchange_{trade.id}"
                
                keyboard.append([
                    InlineKeyboardButton(display_text, callback_data=callback_data)
                ])
        
        # Navigation
        keyboard.append([
            InlineKeyboardButton("⬅️ Back", callback_data="trades_messages_hub")
        ])
            
    finally:
        session.close()
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        try:
            await safe_edit_message_text(query, message, reply_markup=reply_markup, parse_mode="Markdown")
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Error editing active trades: {e}")
    else:
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="Markdown")


async def show_trade_chat_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Modern trade chat list with exchange-style design"""
    user = update.effective_user
    if not user:
        return

    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "💬")

    session = SessionLocal()
    try:
        # Get user from database
        db_user = session.query(User).filter(User.telegram_id == user.id).first()
        if not db_user:
            if query:
                await safe_edit_message_text(query, "❌ User not found. Please restart with /start")
            return

        # Get active trades with message counts (including payment_confirmed)
        trades_query = session.query(Escrow).filter(
            or_(Escrow.buyer_id == db_user.id, Escrow.seller_id == db_user.id),
            Escrow.status.in_(['active', 'payment_pending', 'payment_confirmed', 'completed', 'disputed'])
        ).order_by(desc(Escrow.created_at))
        
        trades = trades_query.all()
        
        if not trades:
            message = """💬 Trade Conversations

🔷 No Active Chats
You don't have any trade conversations yet.

Start a new trade to begin communicating with other users!"""
            
            keyboard = [
                [InlineKeyboardButton("🤝 Create Trade", callback_data="menu_create")],
                [InlineKeyboardButton("🔙 Messages Hub", callback_data="messages_hub")]
            ]
        else:
            message = f"""💬 Trade Conversations

🔷 Active Communications ({len(trades)})
Select a trade to start messaging:

"""
            
            keyboard = []
            
            for trade in trades[:10]:  # Limit to 10 most recent
                # Get counterpart info
                if db_user.id == trade.buyer_id:
                    counterpart_id = trade.seller_id
                    role_emoji = "🛒"  # User is buyer
                    counterpart_role = "Seller"
                else:
                    counterpart_id = trade.buyer_id
                    role_emoji = "🛍️"  # User is seller
                    counterpart_role = "Buyer"
                
                # Get counterpart details
                if counterpart_id:
                    counterpart = session.query(User).filter(User.id == counterpart_id).first()
                    if counterpart and counterpart.username:
                        counterpart_display = f"@{counterpart.username}"
                    elif counterpart and counterpart.first_name:
                        counterpart_display = counterpart.first_name
                    else:
                        counterpart_display = counterpart_role
                elif db_user.id == trade.buyer_id and trade.seller_contact_display:
                    # Buyer viewing seller - use seller_contact_display for non-platform sellers
                    counterpart_display = trade.seller_contact_display
                else:
                    counterpart_display = counterpart_role
                
                # Get message count for this trade
                msg_count = session.query(EscrowMessage).filter(
                    EscrowMessage.escrow_id == trade.id
                ).count()
                
                # Status indicator
                {
                    'active': '🔵',
                    'completed': '🟢',
                    'payment_pending': '🟡',
                    'payment_confirmed': '🔔',
                    'disputed': '⚠️',
                    'cancelled': '❌'
                }.get(str(trade.status).lower(), '🔵')
                
                # Trade ID display
                trade_display = trade.escrow_id[-6:] if trade.escrow_id else str(trade.id)
                
                button_text = f"{role_emoji} #{trade_display} • {counterpart_display}"
                if msg_count > 0:
                    button_text += f" ({msg_count})"
                
                keyboard.append([
                    InlineKeyboardButton(
                        button_text,
                        callback_data=f"trade_chat_open:{trade.id}"
                    )
                ])
            
            # Navigation
            keyboard.append([
                InlineKeyboardButton("🔙 Messages Hub", callback_data="messages_hub"),
                InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")
            ])
        
    finally:
        session.close()
    
    if query:
        await safe_edit_message_text(
            query,
            message,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    return ConversationHandler.END


@audit_conversation_handler("trade_chat_open")
async def open_trade_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Open specific trade chat with modern interface"""
    user = update.effective_user
    if not user:
        return

    query = update.callback_query
    if not query or not query.data:
        return
    
    try:
        trade_id = int(query.data.split(':')[1])
        await safe_answer_callback_query(query, "💬")
    except (IndexError, ValueError):
        await safe_answer_callback_query(query, "❌ Invalid trade ID")
        return

    session = SessionLocal()
    try:
        # Check admin access first - using silent check to avoid false security alerts
        is_admin = is_admin_silent(user.id)
        
        # Get user and trade
        db_user = session.query(User).filter(User.telegram_id == user.id).first()
        if not db_user and not is_admin:
            await safe_edit_message_text(query, "❌ User not found. Please restart with /start")
            return
        
        trade = session.query(Escrow).filter(Escrow.id == trade_id).first()
        if not trade:
            await safe_edit_message_text(query, "❌ Trade not found")
            return
        
        # Verify access (buyers, sellers, and admins)
        if not is_admin and not (db_user.id == trade.buyer_id or db_user.id == trade.seller_id):
            await safe_answer_callback_query(query, "❌ Access denied")
            return
        
        # UNIFIED CHAT: Handle disputed trades in the same interface
        # No more routing to separate dispute chat - everything in trade chat
        
        # Check if trade is completed/resolved - disable chat
        trade_status_lower = str(trade.status).lower()
        chat_disabled = False
        chat_disabled_reason = ""
        
        if trade_status_lower in ['completed', 'cancelled']:
            chat_disabled = True
            chat_disabled_reason = trade.status
        elif trade_status_lower == 'disputed':
            # Check if dispute is resolved/closed (Dispute already imported at top)
            dispute = session.query(Dispute).filter(Dispute.escrow_id == trade.id).first()
            if dispute and dispute.status in ['resolved', 'closed']:
                chat_disabled = True
                chat_disabled_reason = "resolved"
        
        if chat_disabled:
            await safe_edit_message_text(query, 
                f"💬 Chat Closed\n\n"
                f"This trade has been {chat_disabled_reason}. "
                f"Messaging is no longer available.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔙 My Trades", callback_data="trades_messages_hub")]
                ])
            )
            return
        
        # Log trade chat opened with detailed communication metadata
        start_time = datetime.utcnow()
        
        # Get message history for metadata
        messages = session.query(EscrowMessage).filter(
            EscrowMessage.escrow_id == trade_id
        ).order_by(EscrowMessage.created_at.desc()).limit(20).all()
        
        # Determine counterpart
        counterpart_id = trade.seller_id if db_user.id == trade.buyer_id else trade.buyer_id
        
        chat_metadata = PayloadMetadata(
            communication_type="escrow",
            message_thread_id=str(trade_id),
            participant_count=2,
            message_sequence_number=len(messages),
            has_attachments=any(getattr(msg, 'attachment_url', None) for msg in messages),
            is_admin_message=is_admin,
            navigation_depth=1
        )
        
        related_ids = RelatedIDs(
            escrow_id=str(trade_id),
            conversation_id=f"escrow_{trade_id}",
            counterpart_user_id=str(counterpart_id) if counterpart_id else None
        )
        
        communication_audit.audit(
            event_type=AuditEventType.COMMUNICATION,
            action="trade_chat_session_opened",
            result="success",
            user_id=user.id,
            is_admin=is_admin,
            related_ids=related_ids,
            payload_metadata=chat_metadata
        )
        
        # Set active chat session in context.user_data for persistence
        context.user_data['active_chat'] = {
            'type': 'trade',
            'id': trade_id,
            'metadata': {'trade': trade, 'start_time': start_time}
        }
        # Create session in universal manager for multi-session support
        session_id = f"trade_chat_{trade.id}_{user.id}"
        universal_session_manager.create_session(
            user_id=user.id,
            session_type=SessionType.TRADE_CHAT,
            session_id=session_id,
            metadata={
                'trade_id': trade.id,
                'trade_ref': trade.escrow_id,
                'is_disputed': str(trade.status).lower() == 'disputed'
            }
        )
        
        # Also keep in global for backwards compatibility
        active_chat_sessions[user.id] = context.user_data['active_chat']
        logger.info(f"🔥 TRADE CHAT SESSION created: {session_id} for user {user.id}")
        
        # Check if admin is a participant in this trade
        is_admin_participant = is_admin and db_user and (db_user.id == trade.buyer_id or db_user.id == trade.seller_id)
        
        # Get counterpart info - handle admin view ONLY if admin is not a participant
        if is_admin and not is_admin_participant:
            # Admin observer sees both parties
            buyer_name = "Buyer"
            seller_name = "Seller"
            
            if trade.buyer_id:
                buyer = session.query(User).filter(User.id == trade.buyer_id).first()
                if buyer and buyer.username:
                    buyer_name = f"@{buyer.username}"
            
            if trade.seller_id:
                seller = session.query(User).filter(User.id == trade.seller_id).first()
                if seller and seller.username:
                    seller_name = f"@{seller.username}"
            elif hasattr(trade, 'seller_username') and trade.seller_username:
                seller_name = f"@{trade.seller_username}"
            
            counterpart_display = f"{buyer_name} vs {seller_name}"
        else:
            # Regular user or admin participant sees counterpart
            if db_user.id == trade.buyer_id:
                counterpart_id = trade.seller_id
                counterpart_role = "🛍️ Seller"
            else:
                counterpart_id = trade.buyer_id
                counterpart_role = "👤 Buyer"
            
            # Get counterpart details
            counterpart_display = counterpart_role
            if counterpart_id:
                counterpart = session.query(User).filter(User.id == counterpart_id).first()
                if counterpart:
                    if counterpart.username:
                        counterpart_display = f"{counterpart_role} (@{counterpart.username})"
                    elif counterpart.first_name:
                        counterpart_display = f"{counterpart_role} ({counterpart.first_name})"
            elif hasattr(trade, 'seller_username') and trade.seller_username:
                counterpart_display = f"{counterpart_role} (@{trade.seller_username})"
        
        # ADMIN OVERSIGHT: Get both trade and dispute messages for FULL visibility
        # Get ALL trade messages (always visible to admin)
        trade_messages = session.query(EscrowMessage).filter(
            EscrowMessage.escrow_id == trade.id
        ).all()
        
        # Get ALL dispute messages if dispute exists (always visible to admin)
        dispute_messages = []
        dispute = session.query(Dispute).filter(Dispute.escrow_id == trade.id).first()
        if dispute:
            dispute_messages = session.query(DisputeMessage).filter(
                DisputeMessage.dispute_id == dispute.id
            ).all()
        
        # Combine and sort all messages by timestamp
        all_messages = []
        for msg in trade_messages:
            all_messages.append({
                'timestamp': msg.created_at,
                'sender_id': msg.sender_id,
                'text': msg.content,
                'type': 'trade'
            })
        for msg in dispute_messages:
            all_messages.append({
                'timestamp': msg.created_at,
                'sender_id': msg.sender_id,
                'text': msg.message,
                'type': 'dispute'
            })
        
        # Sort by timestamp (handle None values safely)
        all_messages.sort(key=lambda x: x['timestamp'] or datetime.min)
        recent_messages = all_messages[-5:]  # Show last 5 messages
        
        # Reviews removed from UI for cleaner interface
        # Review notifications will be sent when reviews are created
        
        # Status display with dispute indicator
        status_emoji = {
            'active': '🔵',
            'completed': '🟢',
            'payment_pending': '🟡',
            'payment_confirmed': '🔔',
            'cancelled': '❌',
            'disputed': '⚖️'
        }.get(str(trade.status).lower(), '🔵')
        
        trade_display = trade.escrow_id[-6:] if trade.escrow_id else str(trade.id)
        
        # === ADMIN ENHANCED VIEW - ONLY FOR ADMIN OBSERVERS (NOT PARTICIPANTS) ===
        # Check if admin is participating in this trade
        is_admin_observer = is_admin and db_user and (db_user.id != trade.buyer_id and db_user.id != trade.seller_id)
        
        admin_indicator = ""
        if is_admin_observer:
            admin_indicator = " (Admin View)"
            # Simplified admin display - avoid complex formatting
            buyer_name = "Buyer"
            seller_name = "Seller"
            
            if trade.buyer_id:
                buyer = session.query(User).filter(User.id == trade.buyer_id).first()
                if buyer and buyer.username:
                    buyer_name = f"@{buyer.username}"
            
            if trade.seller_id:
                seller = session.query(User).filter(User.id == trade.seller_id).first()
                if seller and seller.username:
                    seller_name = f"@{seller.username}"
            elif hasattr(trade, 'seller_username') and trade.seller_username:
                seller_name = f"@{trade.seller_username}"
            
            counterpart_display = f"{buyer_name} vs {seller_name}"
        
        dispute_indicator = ""
        if str(trade.status).lower() == 'disputed' and dispute:
            # Prominent dispute display
            dispute_status_text = "🔓 Resolved" if dispute.status in ['resolved', 'closed'] else "⏳ Under Review"
            dispute_indicator = f"\n\n🔥 <b>DISPUTED TRADE</b>\n⚖️ {dispute_status_text}\n📝 {dispute.reason}"
        
        # FIXED: Proper emoji display format with HTML parse mode
        message = f"""<b>Trade #{trade_display}</b> {status_emoji}{admin_indicator}

🛍️ <b>{counterpart_display}</b> - <b>${float(trade.amount):.0f}</b>{dispute_indicator}"""
        
        if recent_messages:
            import html
            message += "\n\nRecent messages:"
            
            # Telegram has 4096 char limit - reserve space for header and footer
            TELEGRAM_LIMIT = 4096
            RESERVED_SPACE = len(message) + 100  # Current message + footer buffer
            remaining_chars = TELEGRAM_LIMIT - RESERVED_SPACE
            
            messages_to_show = []
            total_chars_used = 0
            
            # Build messages from newest to oldest, tracking character count
            for msg in reversed(recent_messages[-5:]):  # Process last 5 in reverse
                sender_id = msg['sender_id']
                is_msg_admin = is_admin_secure(sender_id)
                
                # FIXED: Prioritize trade role over admin status
                # Get clear sender identity with role
                msg_user = session.query(User).filter(User.id == sender_id).first()
                if msg_user:
                    username = f"@{msg_user.username}" if msg_user.username else msg_user.first_name or "User"
                    if sender_id == trade.buyer_id:
                        sender = f"👤 Buyer ({username})"
                    elif sender_id == trade.seller_id:
                        sender = f"🏪 Seller ({username})"
                    elif is_msg_admin:
                        # Only show admin role if they're not buyer or seller
                        sender = "🛡️ Admin (Support)"
                    else:
                        sender = f"👤 {username}"
                else:
                    # User not found, check if admin
                    if is_msg_admin:
                        sender = "🛡️ Admin (Support)"
                    else:
                        sender = "👤 Unknown User"
                
                # Show full message text with HTML escaping
                msg_text = html.escape(msg['text'] if msg['text'] else "")
                msg_line = f"\n{sender}: {msg_text}"
                msg_line_length = len(msg_line)
                
                # Check if adding this message would exceed limit
                if total_chars_used + msg_line_length <= remaining_chars:
                    messages_to_show.insert(0, msg_line)  # Insert at start to maintain order
                    total_chars_used += msg_line_length
                else:
                    # Can't fit this message - add truncation notice only if it fits
                    if messages_to_show:  # Only if we're showing some messages
                        overflow_notice = "\n<i>[Earlier messages hidden]</i>"
                        if total_chars_used + len(overflow_notice) <= remaining_chars:
                            messages_to_show.insert(0, overflow_notice)
                        # Otherwise skip notice to stay within limit
                    break
            
            # Add all messages that fit
            message += "".join(messages_to_show)
        
        # Customize prompt based on trade status
        if str(trade.status).lower() == 'disputed':
            message += "\n\n💬 <b>Dispute Chat - Provide details to support:</b>"
        else:
            message += "\n\n💬 <b>Type your message below:</b>"
        
        # === SIMPLE CLEAN BUTTONS ===
        keyboard = []
        trade_status = str(trade.status).lower()
        
        # Only essential action based on status and role
        if trade_status == 'active':
            if db_user.id == trade.seller_id:
                keyboard.append([
                    InlineKeyboardButton("✅ Mark Delivered", callback_data=f"mark_delivered_{trade.id}")
                ])
                # Sellers can only dispute when trade is active
                keyboard.append([
                    InlineKeyboardButton("⚠️ Report Issue", callback_data=f"dispute_trade:{trade.id}")
                ])
            else:  # Buyer
                keyboard.append([
                    InlineKeyboardButton("✅ Release Funds", callback_data=f"release_funds_{trade.id}")
                ])
                # Buyers can dispute when trade is active
                keyboard.append([
                    InlineKeyboardButton("⚠️ Report Issue", callback_data=f"dispute_trade:{trade.id}")
                ])
        
        # Show trade-specific actions for other statuses
        elif trade_status in ['payment_pending', 'payment_confirmed']:
            if db_user.id == trade.buyer_id:
                # Only buyers can cancel during payment phase
                keyboard.append([
                    InlineKeyboardButton("❌ Cancel Trade", callback_data=f"cancel_trade:{trade.id}")
                ])
            # Sellers cannot cancel or dispute until trade is active
        elif trade_status == 'completed':
            # No actions available for completed trades
            pass
        
        # Simple navigation - only essentials
        keyboard.append([
            InlineKeyboardButton("🔙 My Trades", callback_data="messages_trade_list")
        ])
        
        # Send the message INSIDE the try block while session is active
        # FIXED: Use HTML parse mode for proper formatting
        await safe_edit_message_text(
            query,
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        
        logger.info(f"🔥 Trade chat opened for user {user.id}, ready for messages")
        
    finally:
        session.close()
    
    # Enter message input state for direct typing like other chats
    logger.info(f"✅ Trade chat opened for user {user.id}, ready for direct message input")
    # Return MESSAGE_INPUT to enable direct message typing
    return MESSAGE_INPUT


async def show_dispute_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Modern dispute list interface"""
    user = update.effective_user
    if not user:
        return

    query = update.callback_query
    if query:
        await safe_answer_callback_query(query, "⚖️")

    session = SessionLocal()
    try:
        # Get user from database
        db_user = session.query(User).filter(User.telegram_id == user.id).first()
        if not db_user:
            if query:
                await safe_edit_message_text(query, "❌ User not found. Please restart with /start")
            return

        # Get user's disputes
        disputes = session.query(Dispute).join(Escrow, Dispute.escrow_id == Escrow.id).filter(
            or_(Escrow.buyer_id == db_user.id, Escrow.seller_id == db_user.id)
        ).order_by(desc(Dispute.created_at)).all()
        
        if not disputes:
            message = """⚖️ Dispute Center

🔷 No Active Disputes
Great news! You don't have any disputes to resolve.

All your trades are running smoothly."""
            
            keyboard = [
                [InlineKeyboardButton("💬 Trade Chats", callback_data="messages_trade_list")],
                [InlineKeyboardButton("🔙 Messages Hub", callback_data="messages_hub")]
            ]
        else:
            active_disputes = [d for d in disputes if d.status in ['open', 'under_review']]
            resolved_disputes = [d for d in disputes if d.status == 'resolved']
            
            message = f"""⚖️ Dispute Center

🔷 Dispute Management
• Active: {len(active_disputes)}
• Resolved: {len(resolved_disputes)}

📋 Select dispute to manage:

"""
            
            keyboard = []
            
            # Show active disputes first
            for dispute in disputes[:8]:  # Limit to 8 most recent
                trade = dispute.escrow
                status_emoji = {
                    'open': '🔴',
                    'under_review': '🟠',
                    'resolved': '✅'
                }.get(dispute.status, '🔵')
                
                trade_display = trade.escrow_id[-6:] if trade.escrow_id else str(trade.id)
                age_days = (datetime.utcnow() - dispute.created_at).days
                
                button_text = f"{status_emoji} #{trade_display} • {dispute.reason}"
                if age_days > 0:
                    button_text += f" ({age_days}d ago)"
                
                keyboard.append([
                    InlineKeyboardButton(
                        button_text,
                        callback_data=f"view_dispute:{dispute.id}"
                    )
                ])
            
            # Navigation
            keyboard.append([
                InlineKeyboardButton("🔙 Messages Hub", callback_data="messages_hub"),
                InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")
            ])
        
    finally:
        session.close()
    
    if query:
        await safe_edit_message_text(
            query,
            message,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    return ConversationHandler.END


# UNIFIED MESSAGING SYSTEM: Dispute chat functionality merged into trade chat
# No separate dispute chat handler needed - everything handled by open_trade_chat


# Message input handler for active chat sessions
@audit_conversation_handler("message_input")
async def handle_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming messages for active chat sessions"""
    user = update.effective_user
    message = update.message
    
    logger.info(f"🔥 handle_message_input called for user {user.id if user else 'None'}")
    logger.info(f"📨 Message received: '{message.text if message else 'None'}' from user {user.id if user else 'None'}")
    
    if not user or not message or not message.text:
        logger.error(f"❌ Missing data: user={user}, message={message}, text={message.text if message else 'None'}")
        return

    # PRIORITY CHECK: Skip onboarding protection if user has active trade chat session
    from utils.universal_session_manager import universal_session_manager, SessionType
    trade_chat_sessions = universal_session_manager.get_user_sessions(
        user_id=user.id,
        session_type=SessionType.TRADE_CHAT
    )
    
    if trade_chat_sessions:
        logger.info(f"✅ TRADE_CHAT ACTIVE: Bypassing onboarding protection for user {user.id} (has active trade chat)")
    else:
        # ONBOARDING PROTECTION: Only block if NO active trade chat session
        should_block = await OnboardingProtection.should_block_processing(user.id, "messages_hub")
        if should_block:
            logger.warning(f"🚫 ONBOARDING PROTECTION: Blocking messages_hub for user {user.id} - active onboarding session")
            
            # Send protection message to user
            protection_message = OnboardingProtection.get_protection_message()
            await message.reply_text(protection_message, parse_mode="Markdown")
            return
    
    # LEGACY GUARD: Skip processing if onboarding conversation is active (kept for compatibility)
    if context.user_data and context.user_data.get('verification_purpose') == 'email_onboarding':
        logger.info(f"🚫 LEGACY ONBOARDING GUARD: Skipping message processing - onboarding conversation active for user {user.id}")
        return

    # CRITICAL FIX: COMPREHENSIVE exclusive state check to prevent false admin security alerts
    # This must happen BEFORE any admin checks to prevent normal users from triggering security alerts
    from handlers.wallet_direct import get_wallet_state
    
    # ENHANCED STATE DETECTION: Multiple detection methods for robustness
    wallet_state = await get_wallet_state(user.id, context)
    
    # FALLBACK: Direct check of context.user_data for state (in case get_wallet_state fails)
    direct_wallet_state = context.user_data.get('wallet_state', 'inactive') if context.user_data else 'inactive'
    
    # Log state check for troubleshooting (only in debug mode)
    if logger.isEnabledFor(logging.DEBUG):
        logger.debug(f"Exclusive state check for user {user.id}: wallet_state='{wallet_state}', direct='{direct_wallet_state}'")
    
    # Use both detection methods for maximum reliability
    effective_wallet_state = wallet_state if wallet_state != 'inactive' else direct_wallet_state
    
    # Define exclusive states where messages should NOT be processed by messages_hub
    exclusive_states = [
        'verifying_crypto_otp',
        'verifying_bank_otp',     # Used for NGN cashout OTP verification
        'verifying_ngn_otp',      # Additional NGN OTP state variant
        'entering_address',
        'entering_amount',
        'entering_otp',           # Generic OTP entry state
        'awaiting_otp',          # Alternative OTP waiting state
        'otp_verification',       # Generic OTP verification state
        'entering_custom_amount', # Wallet cashout custom amount input
        'selecting_method',       # Wallet cashout method selection after amount
        'entering_crypto_amount', # Crypto cashout amount input
        'entering_crypto_address', # Crypto cashout address input
        # Bank addition flow states
        'adding_bank_selecting',    # Bank selection menu
        'adding_bank_account_number', # Account number input
        'adding_bank_confirming',   # Account confirmation
        'adding_bank_label',        # Label/nickname input
        'adding_bank_searching'     # Bank search input
    ]
    
    # ENHANCED: Check multiple indicators of exclusive states with improved logic
    wallet_state_exclusive = effective_wallet_state in exclusive_states
    otp_flags_exclusive = (
        (context.user_data and context.user_data.get('expecting_otp_input')) or
        (context.user_data and context.user_data.get('otp_type')) or
        (context.user_data and context.user_data.get('verification_state') in exclusive_states)
    )
    # DATABASE-BACKED + TTL: Replace session checks with reliable checking
    # CRITICAL FIX: Use Telegram user ID for proper lookup, include context for TTL
    telegram_user_id = update.effective_user.id if update.effective_user else 0
    has_active_db_cashout = await has_active_cashout_db_by_telegram(telegram_user_id, context)
    
    wallet_flow_exclusive = (
        (context.user_data and context.user_data.get('wallet_flow')) or
        has_active_db_cashout  # ENHANCED: Database-backed check replaces session flags
    )
    # CRITICAL FIX: Check for cashout states specifically (hybrid approach)
    cashout_state_exclusive = (
        (context.user_data and context.user_data.get('current_state') == 'ENTERING_CUSTOM_AMOUNT') or
        (context.user_data and context.user_data.get('cashout_balance')) or
        (context.user_data and context.user_data.get('cashout_data')) or
        has_active_db_cashout  # ENHANCED: Database-backed check for any active cashouts
    )
    
    is_in_exclusive_state = wallet_state_exclusive or otp_flags_exclusive or wallet_flow_exclusive or cashout_state_exclusive
    
    if is_in_exclusive_state:
        logger.info(f"🚫 User {user.id} in exclusive state - NOT processing in messages hub")
        logger.info(f"   - Effective wallet state: '{effective_wallet_state}'")
        logger.info(f"   - State exclusive: {wallet_state_exclusive}")
        logger.info(f"   - OTP flags exclusive: {otp_flags_exclusive}")
        logger.info(f"   - Wallet flow exclusive: {wallet_flow_exclusive}")
        logger.info(f"   - Cashout state exclusive: {cashout_state_exclusive}")
        
        # CRITICAL FIX: Smart routing based on input type (amount vs address)
        if cashout_state_exclusive and context.user_data and context.user_data.get('cashout_data', {}).get('current_state') == 'ENTERING_CUSTOM_AMOUNT':
            text_input = message.text.strip()
            
            # Check if input is a crypto address (ETH address pattern: 0x + 40 hex chars)
            is_crypto_address = (
                text_input.startswith('0x') and len(text_input) == 42 and
                all(c in '0123456789abcdefABCDEF' for c in text_input[2:])
            ) or (
                # BTC legacy address patterns (26-35 chars)
                len(text_input) >= 26 and len(text_input) <= 35 and
                (text_input.startswith('1') or text_input.startswith('3'))
            ) or (
                # BTC Bech32 address patterns (39-62 chars)
                text_input.startswith('bc1') and len(text_input) >= 39 and len(text_input) <= 62
            )
            
            if is_crypto_address:
                logger.info(f"🎯 CRYPTO ADDRESS DETECTED: Routing to wallet text handler for user {user.id}")
                # Route to unified text router for crypto address handling
                from utils.unified_text_router import unified_text_router
                try:
                    from handlers.wallet_text_input import handle_wallet_text_input
                    unified_text_router.register_conversation_handler("wallet_input", handle_wallet_text_input)
                    # CRITICAL FIX: Also register cashout_flow to prevent routing issues
                    unified_text_router.register_conversation_handler("cashout_flow", handle_wallet_text_input)
                    await unified_text_router.route_text_message(update, context)
                    return
                except Exception as e:
                    logger.error(f"Error routing crypto address: {e}")
                    await message.reply_text("❌ Error processing crypto address. Please try again.")
                    return
            else:
                logger.info(f"🎯 AMOUNT INPUT DETECTED: Routing to amount handler for user {user.id}")
                # Route to amount handler for numeric inputs
                from handlers.wallet_direct import handle_custom_amount_input
                try:
                    await handle_custom_amount_input(update, context)
                    return
                except Exception as e:
                    logger.error(f"Error in wallet custom amount handler: {e}")
                    await message.reply_text("❌ Error processing cashout amount. Please try again.")
                    return
        
        # CRITICAL FIX: Route all wallet state text input to unified text router
        if wallet_state_exclusive:
            logger.info(f"🎯 ROUTING WALLET TEXT INPUT to unified text router for user {user.id}, state: {effective_wallet_state}")
            from utils.unified_text_router import unified_text_router
            try:
                # Use global unified text router instance and route message
                from handlers.wallet_text_input import handle_wallet_text_input
                unified_text_router.register_conversation_handler("wallet_input", handle_wallet_text_input)
                # CRITICAL FIX: Also register cashout_flow to the same handler to prevent routing issues
                unified_text_router.register_conversation_handler("cashout_flow", handle_wallet_text_input)
                
                # Route the text message
                await unified_text_router.route_text_message(update, context)
                return
            except Exception as e:
                logger.error(f"Error in unified text router for wallet states: {e}")
                await message.reply_text("❌ Error processing input. Please try again.")
                return
        
        return  # Let other handlers (wallet_direct) process this message
    
    # PRIORITY 1: Check if admin is composing broadcast (MUST be before admin reply check)
    from utils.admin_security import is_admin_silent
    if is_admin_silent(user.id):
        from handlers.admin_broadcast_direct import get_admin_broadcast_state
        try:
            broadcast_state = await get_admin_broadcast_state(user.id)
            logger.info(f"🔍 BROADCAST_CHECK: Admin {user.id} state = '{broadcast_state}'")
            if broadcast_state == "composing":
                logger.info(f"📢 BROADCAST_ROUTE: Admin {user.id} is composing broadcast - routing to broadcast handler ONLY (skipping support reply check)")
                from handlers.admin_broadcast import handle_broadcast_message
                await handle_broadcast_message(update, context)
                logger.info(f"✅ BROADCAST_COMPLETE: Admin {user.id} broadcast handled, returning WITHOUT checking support reply")
                return  # CRITICAL: Stop here, do NOT proceed to admin reply check
        except Exception as e:
            logger.error(f"❌ BROADCAST_ERROR: Error checking broadcast state for admin {user.id}: {e}", exc_info=True)
    
    # PRIORITY 2: Check if admin is replying to support ticket
    # CRITICAL FIX: Only check admin reply if user is actually an admin to avoid false security alerts
    if is_admin_silent(user.id):
        logger.info(f"🔍 SUPPORT_CHECK: Admin {user.id} - checking if replying to support ticket")
        from handlers.admin_support import handle_admin_reply_message
        try:
            admin_reply_handled = await handle_admin_reply_message(update, context)
            if admin_reply_handled:
                logger.info(f"✅ SUPPORT_COMPLETE: Admin {user.id} support reply handled")
                return
            else:
                logger.info(f"ℹ️ SUPPORT_SKIP: Admin {user.id} message not a support reply")
        except Exception as e:
            logger.error(f"❌ SUPPORT_ERROR: Error handling admin reply for {user.id}: {e}", exc_info=True)
    
    # CRITICAL FIX: Check if user is trying to continue escrow creation after restart
    # BUT ONLY if they're NOT already in an escrow conversation
    text = message.text.strip()
    
    # Check if user is already in ANY active conversation
    in_escrow_conversation = (
        context.user_data and 
        context.user_data.get("escrow_data") and 
        context.user_data["escrow_data"].get("status") == "creating"
    )
    
    # CRITICAL FIX: If user is in escrow conversation, route to direct handlers instead of conversation handlers
    if in_escrow_conversation:
        logger.info(f"📝 User {user.id} is in escrow conversation - routing to direct escrow handlers")
        # FIXED: Route to direct handlers instead of conversation handlers
        from handlers.escrow_direct import route_text_message_to_escrow_flow
        try:
            result = await route_text_message_to_escrow_flow(update, context)
            if result:  # Successfully handled by direct handlers
                return
            else:
                logger.warning(f"Direct escrow handlers couldn't process message for user {user.id}")
                # Clear corrupted state
                if context.user_data and "escrow_data" in context.user_data:
                    context.user_data.pop("escrow_data", None)
        except Exception as e:
            logger.error(f"Failed to route to direct escrow handlers: {e}")
            # Clear corrupted state
            if context.user_data and "escrow_data" in context.user_data:
                context.user_data.pop("escrow_data", None)
    
    # CRITICAL FIX: Check if user has active support ticket
    has_active_support_ticket = False
    try:
        session = SessionLocal()
        try:
            from models import SupportTicket
            db_user = session.query(User).filter(User.telegram_id == user.id).first()
            if db_user:
                active_ticket = session.query(SupportTicket).filter(
                    SupportTicket.user_id == db_user.id,
                    SupportTicket.status.in_(["open", "assigned"])
                ).first()
                has_active_support_ticket = active_ticket is not None
                if has_active_support_ticket:
                    logger.info(f"🎫 User {user.id} has active support ticket {active_ticket.ticket_id} - skipping messages hub processing")
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Error checking support ticket status: {e}")
    
    # CRITICAL FIX: Check if user is in onboarding flow (ENHANCED)
    in_onboarding = False
    onboarding_step = None
    try:
        session = SessionLocal()
        try:
            from models import OnboardingSession
            from datetime import datetime
            
            db_user = session.query(User).filter(User.telegram_id == user.id).first()
            if db_user:
                # Check conversation_state for legacy onboarding detection
                legacy_onboarding = db_user.conversation_state and db_user.conversation_state.startswith("onboarding_")
                
                # ENHANCED: Check onboarding_sessions table for active sessions
                active_onboarding_session = session.query(OnboardingSession).filter(
                    OnboardingSession.user_id == db_user.id,
                    OnboardingSession.completed_at.is_(None),  # Not completed
                    OnboardingSession.expires_at > datetime.utcnow()  # Not expired
                ).first()
                
                in_onboarding = legacy_onboarding or active_onboarding_session is not None
                
                if in_onboarding:
                    if active_onboarding_session:
                        onboarding_step = active_onboarding_session.current_step
                        logger.info(f"🎯 User {user.id} has active onboarding session at step '{onboarding_step}' - preventing escrow recovery")
                    elif legacy_onboarding:
                        logger.info(f"🎯 User {user.id} is in legacy onboarding state '{db_user.conversation_state}' - preventing escrow recovery")
        finally:
            session.close()
    except Exception as e:
        logger.error(f"Error checking onboarding state: {e}")
    
    # Also check for other active conversations to prevent interference
    in_any_conversation = (
        in_onboarding or  # CRITICAL FIX: Include onboarding state detection
        has_active_support_ticket or  # CRITICAL FIX: Include active support tickets
        (context.user_data and context.user_data.get("state")) or  # Check for any state
        (context.user_data and context.user_data.get("in_conversation")) or
        (context.user_data and context.user_data.get("exchange_data")) or
        (context.user_data and context.user_data.get("wallet_flow")) or
        (context.user_data and context.user_data.get("active_conversation")) or  # FIXED: Check for active conversation states
        (context.user_data and context.user_data.get("trade_acceptance_flow")) or  # FIXED: Check for trade acceptance flow
        (context.user_data and context.user_data.get("email_collection_flow")) or  # FIXED: Check for email collection flow
        (context.user_data and context.user_data.get("pending_trade_acceptance"))  # FIXED: Check for pending trade acceptance
    )
    
    # ENHANCED: Only trigger recovery if NOT already in ANY conversation AND not a simple email pattern
    # Additional safeguards for email vs escrow username detection
    is_simple_email = ('@' in text and '.' in text and len(text.split('@')) == 2 and 
                      len(text.split('.')) >= 2 and not text.startswith('@'))
    is_username_pattern = text.startswith('@') and len(text) > 1 and '.' not in text
    
    # CRITICAL FIX: Extra protection against onboarding email routing
    should_trigger_escrow = (
        not in_any_conversation and 
        not in_onboarding and  # EXPLICIT: Double-check onboarding state
        (is_username_pattern or (not is_simple_email and '@' in text)) and
        len(text.strip()) > 1 and
        not text.strip().count('@') > 1  # Prevent multiple @ symbols
    )
    
    if should_trigger_escrow:
        if onboarding_step:
            logger.warning(f"🚫 ESCROW RECOVERY BLOCKED: User {user.id} in onboarding step '{onboarding_step}' - not routing '{text}' to escrow")
            return  # Explicitly block escrow routing
            
        logger.info(f"🔄 ESCROW RECOVERY: Detected escrow input '{text}' from user {user.id} - routing to escrow flow")
        # Import here to avoid circular imports
        from handlers.escrow import start_secure_trade
        # Simulate a menu_create callback to restart escrow conversation
        
        # Create a synthetic callback to restart escrow flow
        synthetic_query = type('Query', (), {
            'data': 'menu_create',
            'from_user': user,
            'message': message,
            'answer': lambda self, text="": None,  # Fixed: added self parameter
            'edit_message_text': lambda self, *args, **kwargs: None  # Fixed: added self parameter
        })()
        
        # Set up context for escrow restart
        if not context.user_data:
            # Fix: Cannot assign new value to user_data
            if hasattr(context, 'user_data') and context.user_data is not None:
                context.user_data.clear()
            # If user_data is None, work with existing state
            
        # Create synthetic update with callback query
        synthetic_update = type('Update', (), {
            'callback_query': synthetic_query,
            'effective_user': user,
            'message': message
        })()
        
        try:
            await start_secure_trade(synthetic_update, context)
            # Now handle the seller input directly
            from handlers.escrow import handle_seller_input
            await handle_seller_input(update, context)
            return
        except Exception as e:
            logger.error(f"Failed to recover escrow conversation: {e}")
            # ENHANCED: Clear any corrupted state that might have been set
            if context.user_data:
                context.user_data.pop("escrow_data", None)
                context.user_data.pop("active_conversation", None)
            # Fall through to normal message handling
    elif in_escrow_conversation:
        logger.info(f"📝 User {user.id} is already in escrow conversation - routing to direct handlers")
        # FIXED: Route to direct handlers instead of early return
        from handlers.escrow_direct import route_text_message_to_escrow_flow
        try:
            result = await route_text_message_to_escrow_flow(update, context)
            if result:  # Successfully handled
                return
        except Exception as e:
            logger.error(f"Failed to route escrow message to direct handlers: {e}")
    elif in_any_conversation:
        # ENHANCED: Provide specific logging for onboarding vs other conversations
        if in_onboarding:
            if onboarding_step:
                logger.info(f"🎯 ONBOARDING PROTECTION: User {user.id} in step '{onboarding_step}' - message will be processed by onboarding handlers")
            else:
                logger.info(f"🎯 ONBOARDING PROTECTION: User {user.id} in onboarding flow - message will be processed by onboarding handlers")
        else:
            logger.info(f"🔄 User {user.id} is in an active conversation, skipping message handler")
        # Let the conversation handler process this message
        return
    
    # Skip messages if user is in wallet funding flow
    if context.user_data and (
        context.user_data.get("expecting_custom_amount", False) or 
        context.user_data.get("expecting_funding_amount", False)
    ):
        logger.info(f"🚫 Skipping message - user {user.id} in wallet funding flow")
        return
    
    logger.info(f"📨 Message received: '{message.text}' from user {user.id}")
    
    # Check for active chat session - try context.user_data first, then global dict
    chat_session = None
    if context.user_data and 'active_chat' in context.user_data:
        chat_session = context.user_data['active_chat']
        logger.info("💾 Found chat session in context.user_data")
    elif user.id in active_chat_sessions:
        chat_session = active_chat_sessions[user.id]
        logger.info("💾 Found chat session in global dict")
    
    logger.info(f"🔍 Chat session for user {user.id}: {chat_session}")
    
    # Check if user is providing dispute description
    if context.user_data and context.user_data.get('awaiting_dispute_description'):
        trade_id = context.user_data.get('dispute_trade_id')
        if trade_id:
            return await handle_dispute_description(update, context, trade_id, message.text)
    
    # Check if user is sending a message to a specific trade
    if context.user_data and "sending_message_to_trade" in context.user_data:
        trade_id = context.user_data["sending_message_to_trade"]
        logger.info(f"📨 User is sending message to trade {trade_id}")
        
        # Process the message for this specific trade
        session = SessionLocal()
        try:
            db_user = session.query(User).filter(User.telegram_id == user.id).first()
            if not db_user:
                await message.reply_text("❌ User not found. Please use /start to register.")
                return
                
            # Get the trade
            trade = session.query(Escrow).filter(
                Escrow.escrow_id == trade_id,
                or_(Escrow.buyer_id == db_user.id, Escrow.seller_id == db_user.id)
            ).first()
            
            if not trade:
                await message.reply_text("❌ Trade not found or access denied.")
                # Clear the context
                del context.user_data["sending_message_to_trade"]
                return
                
            # UNIFIED MESSAGING: Save to appropriate table based on trade status
            if str(trade.status).lower() == 'disputed':
                # For disputed trades, save to dispute messages
                dispute = session.query(Dispute).filter(Dispute.escrow_id == trade.id).first()
                if dispute:
                    dispute_message = DisputeMessage(
                        dispute_id=dispute.id,
                        sender_id=db_user.id,
                        message=message.text,
                        created_at=datetime.utcnow()
                    )
                    session.add(dispute_message)
                    logger.info(f"💬 Dispute message saved to dispute table for trade {trade.id}")
                else:
                    # Fallback to trade messages if no dispute found
                    trade_message = EscrowMessage(
                        escrow_id=trade.id,
                        sender_id=db_user.id,
                        content=message.text,
                        created_at=datetime.utcnow()
                    )
                    session.add(trade_message)
                    logger.info(f"💬 Message saved to trade table (dispute fallback) for trade {trade.id}")
            else:
                # For non-disputed trades, save to trade messages
                trade_message = EscrowMessage(
                    escrow_id=trade.id,
                    sender_id=db_user.id,
                    content=message.text,
                    created_at=datetime.utcnow()
                )
                session.add(trade_message)
                logger.info(f"💬 Trade message saved to trade table for trade {trade.id}")
            
            session.commit()
            
            # Get counterparty for notification
            counterparty_id = trade.seller_id if trade.buyer_id == db_user.id else trade.buyer_id
            counterparty = session.query(User).filter(User.id == counterparty_id).first()
            
            # Send notification to counterparty (seller/buyer)
            if counterparty and counterparty.telegram_id:
                try:
                    # Determine sender role - prioritize trade role over admin status
                    if trade.buyer_id == db_user.id:
                        sender_role = "buyer"
                        recipient_role = "seller"
                    elif trade.seller_id == db_user.id:
                        sender_role = "seller"
                        recipient_role = "buyer"
                    elif is_admin_silent(db_user.id):
                        # Only show as Admin if not a participant
                        sender_role = "Admin"
                        recipient_role = "buyer/seller"
                    else:
                        sender_role = "user"
                        recipient_role = "other party"
                    
                    notification_text = f"💬 New message from {sender_role}\n\n"
                    notification_text += f"Trade #{trade.escrow_id[:12]}\n\n"
                    notification_text += f"💬 {message.text[:200]}{'...' if len(message.text) > 200 else ''}\n\n"
                    notification_text += f"👤 From: @{db_user.username or 'User'}"
                    
                    # Create reply keyboard for recipient
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                    reply_keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("💬 Reply", callback_data=f"trade_chat_open:{trade.id}")],
                        [InlineKeyboardButton("📋 View Trade", callback_data=f"view_trade_{trade.id}")]
                    ])
                    
                    # Send notification to counterparty
                    await context.bot.send_message(
                        chat_id=counterparty.telegram_id,
                        text=notification_text,
                        reply_markup=reply_keyboard,
                        parse_mode="Markdown"
                    )
                    logger.info(f"📧 Notification sent to {counterparty.username} ({counterparty.telegram_id}) for trade {trade.escrow_id}")
                    
                except Exception as e:
                    logger.error(f"❌ Failed to send notification to {counterparty.username}: {e}")
            
            # Import here to ensure it's in scope
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            
            await message.reply_text(
                f"✅ Message sent successfully!\n\n"
                f"📋 Trade: #{trade.escrow_id[:12]}\n"
                f"👤 To: @{counterparty.username if counterparty and counterparty.username else 'User'}\n"
                f"💬 Message: {message.text[:50]}{'...' if len(message.text) > 50 else ''}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("💬 Send Another", callback_data=f"trade_chat_open:{trade_id}")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ]),
                parse_mode="Markdown"
            )
            
            # Clear the context
            del context.user_data["sending_message_to_trade"]
            
        finally:
            session.close()
        
        return  # Message processed, exit function
    
    # UNIFIED MESSAGING: All dispute messages now handled through trade chat
    # No separate dispute message handling needed
    
    # HANDLE REGULAR TRADE MESSAGING
    if chat_session and chat_session.get('type') == 'trade':
        trade_id = chat_session.get('id')
        logger.info(f"🔥 Processing trade message for trade ID {trade_id}")
        
        session = SessionLocal()
        try:
            # Get user from database
            db_user = session.query(User).filter(User.telegram_id == user.id).first()
            if not db_user:
                await message.reply_text("❌ User not found. Please use /start to register.")
                return
            
            # Get the trade
            trade = session.query(Escrow).filter(
                Escrow.id == trade_id,
                (Escrow.buyer_id == db_user.id) | (Escrow.seller_id == db_user.id)
            ).first()
            
            if not trade:
                await message.reply_text("❌ Trade not found or access denied.")
                return
            
            # UNIFIED MESSAGING: Save to appropriate table based on trade status
            if str(trade.status).lower() == 'disputed':
                # For disputed trades, save to dispute messages
                dispute = session.query(Dispute).filter(Dispute.escrow_id == trade.id).first()
                if dispute:
                    dispute_message = DisputeMessage(
                        dispute_id=dispute.id,
                        sender_id=db_user.id,
                        message=message.text,
                        created_at=datetime.utcnow()
                    )
                    session.add(dispute_message)
                    logger.info(f"💬 Dispute message saved to dispute table for trade {trade.id}")
                else:
                    # Fallback to trade messages if no dispute found
                    trade_message = EscrowMessage(
                        escrow_id=trade.id,
                        sender_id=db_user.id,
                        content=message.text,
                        created_at=datetime.utcnow()
                    )
                    session.add(trade_message)
                    logger.info(f"💬 Message saved to trade table (dispute fallback) for trade {trade.id}")
            else:
                # For non-disputed trades, save to trade messages
                trade_message = EscrowMessage(
                    escrow_id=trade.id,
                    sender_id=db_user.id,
                    content=message.text,
                    created_at=datetime.utcnow()
                )
                session.add(trade_message)
                logger.info(f"💬 Trade message saved to trade table for trade {trade.id}")
            
            session.commit()
            
            # Get counterpart for notification
            counterpart_id = trade.seller_id if trade.buyer_id == db_user.id else trade.buyer_id
            counterpart = session.query(User).filter(User.id == counterpart_id).first()
            
            # Send notification to counterpart with unified messaging
            if counterpart and counterpart.telegram_id:
                try:
                    sender_role = "Buyer" if trade.buyer_id == db_user.id else "Seller"
                    trade_display = trade.escrow_id[-6:] if trade.escrow_id else str(trade.id)
                    
                    # Add dispute indicator for disputed trades
                    if str(trade.status).lower() == 'disputed':
                        notification_text = f"⚖️ New dispute message from {sender_role}\n\n"
                    else:
                        notification_text = f"💬 New message from {sender_role}\n\n"
                    
                    notification_text += f"Trade #{trade_display}\n"
                    notification_text += f"💬 {message.text[:150]}{'...' if len(message.text) > 150 else ''}"
                    
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                    await context.bot.send_message(
                        chat_id=counterpart.telegram_id,
                        text=notification_text,
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("💬 Reply", callback_data=f"trade_chat_open:{trade.id}")],
                            [InlineKeyboardButton("📋 View Trade", callback_data=f"view_trade_{trade.escrow_id}")]
                        ]),
                        parse_mode="Markdown"
                    )
                    logger.info(f"📧 Trade message notification sent to {counterpart.username} ({counterpart.telegram_id})")
                    
                except Exception as e:
                    logger.error(f"❌ Failed to send trade notification to {counterpart.username}: {e}")
            
            # Notify admins if this is a dispute message
            if str(trade.status).lower() == 'disputed':
                logger.info(f"🚨 DISPUTE MESSAGE DETECTED - Starting admin notification process for trade {trade.id}")
                
                # Send EMAIL notification to admin
                try:
                    from services.admin_notification_service import send_admin_email
                    
                    sender_role = "Buyer" if trade.buyer_id == db_user.id else "Seller"
                    sender_username = db_user.username or db_user.first_name or "User"
                    trade_display = trade.escrow_id[:12] if trade.escrow_id else str(trade.id)
                    
                    email_subject = f"⚖️ Dispute Message - Trade #{trade_display}"
                    email_body = f"""
<h2>⚖️ New Dispute Message</h2>

<p><strong>Trade:</strong> #{trade_display}</p>
<p><strong>Amount:</strong> ${float(trade.amount) if trade.amount else 0}</p>
<p><strong>From:</strong> {sender_role} (@{sender_username})</p>

<h3>Message:</h3>
<p>{message.text}</p>

<p>Reply to this dispute via the admin panel.</p>
"""
                    
                    await send_admin_email(
                        subject=email_subject,
                        body=email_body,
                        is_html=True
                    )
                    logger.info(f"📧 Dispute message email sent to admin for trade {trade.id}")
                except Exception as email_error:
                    logger.error(f"Failed to send dispute email to admin: {email_error}")
                
                # Send TELEGRAM notification to admin
                try:
                    from utils.admin_security import AdminSecurityManager
                    admin_manager = AdminSecurityManager()
                    admin_ids = list(admin_manager.get_admin_ids())
                    logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for Telegram notification")
                except Exception as admin_e:
                    logger.error(f"Failed to get admin IDs for notification: {admin_e}")
                    admin_ids = []
                if admin_ids:
                    try:
                        sender_role = "Buyer" if trade.buyer_id == db_user.id else "Seller"
                        trade_display = trade.escrow_id[-6:] if trade.escrow_id else str(trade.id)
                        admin_text = "⚖️ New Dispute Message\n\n"
                        admin_text += f"Trade: #{trade_display}\n"
                        admin_text += f"From: {sender_role} (@{db_user.username or db_user.first_name or 'User'})\n"
                        admin_text += f"Message: {message.text[:200]}{'...' if len(message.text) > 200 else ''}"
                        
                        for admin_id in admin_ids:
                            try:
                                # Handle both string and int admin IDs
                                chat_id = int(admin_id.strip() if isinstance(admin_id, str) else admin_id)
                                await context.bot.send_message(
                                    chat_id=chat_id,
                                    text=admin_text,
                                    reply_markup=InlineKeyboardMarkup([
                                        [InlineKeyboardButton("💬 View Chat", callback_data=f"trade_chat_open:{trade.id}")],
                                        [InlineKeyboardButton("📋 View Trade", callback_data=f"view_trade_{trade.escrow_id}")]
                                    ])
                                )
                                logger.info(f"📧 Dispute Telegram notification sent to admin {admin_id}")
                            except Exception as admin_notify_error:
                                logger.error(f"Failed to notify admin {admin_id}: {admin_notify_error}")
                    except Exception as e:
                        logger.error(f"❌ Failed to send Telegram notifications to admins: {e}")
            
            # Show confirmation to sender with different messages for dispute vs regular trade
            sender_role = "Buyer" if trade.buyer_id == db_user.id else "Seller"
            recipient_role = "Seller" if sender_role == "Buyer" else "Buyer"
            counterpart_username = f"@{counterpart.username}" if counterpart and counterpart.username else counterpart.first_name if counterpart else "User"
            
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            
            # Different confirmation for disputed trades
            if str(trade.status).lower() == 'disputed':
                await message.reply_text(
                    f"✅ Dispute message sent!\n\n"
                    f"📋 Trade: #{trade.escrow_id[:12] if trade.escrow_id else trade.id}\n"
                    f"⚖️ Status: Under Review\n"
                    f"👥 Notified: {recipient_role}, Admin Team\n"
                    f"💬 Message: {message.text[:50]}{'...' if len(message.text) > 50 else ''}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💬 Continue Chat", callback_data=f"trade_chat_open:{trade.id}")],
                        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                    ])
                )
            else:
                await message.reply_text(
                    f"✅ Message sent!\n\n"
                    f"📋 Trade: #{trade.escrow_id[:12] if trade.escrow_id else trade.id}\n"
                    f"👤 To: {recipient_role} ({counterpart_username})\n"
                    f"💬 Message: {message.text[:50]}{'...' if len(message.text) > 50 else ''}",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("💬 Continue Chat", callback_data=f"trade_chat_open:{trade.id}")],
                        [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                    ])
                )
            
        except Exception as e:
            logger.error(f"Error processing trade message: {e}")
            await message.reply_text("❌ Error sending message. Please try again.")
        finally:
            session.close()
        
        return  # Message processed, exit function
    
    if not chat_session:
        # User is not in an active chat session and message handler shouldn't show trade selection
        # Simply log and return - let conversation handlers process the message
        logger.info(f"📝 User {user.id} sent message outside of active chat - ignoring in message handler")
        return


async def handle_start_dispute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle start dispute callback - show dispute creation interface"""
    query = update.callback_query
    if not query:
        return
    
    await safe_answer_callback_query(query, "⚖️ Loading dispute options...")
    user = update.effective_user
    if not user:
        return
    
    # Get user's active trades they can dispute
    session = SessionLocal()
    try:
        db_user = session.query(User).filter(User.telegram_id == user.id).first()
        if not db_user:
            await safe_edit_message_text(query, "❌ User not found. Please use /start to register.")
            return
        
        # Get active trades where user is buyer or seller (including cancelled/refunded)
        active_trades = session.query(Escrow).filter(
            (Escrow.buyer_id == db_user.id) | (Escrow.seller_id == db_user.id)
        ).filter(
            Escrow.status.in_(['active', 'payment_confirmed', 'payment_pending', 'created', 'partial_payment'])
        ).order_by(desc(Escrow.created_at)).all()
        
        if not active_trades:
            await safe_edit_message_text(query, 
                "❌ No Active Trades Available\n\n"
                "You can only dispute active trades. Start a new trade first.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🤝 New Trade", callback_data="menu_create")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ]),
                parse_mode="Markdown"
            )
            return
        
        # Show trades available for dispute
        message = "⚖️ Report an Issue\n\n"
        message += "Select the trade you want to report an issue with:\n\n"
        
        keyboard = []
        for trade in active_trades[:5]:  # Limit to 5 recent trades
            trade_display = trade.escrow_id[:12] if trade.escrow_id else str(trade.id)
            amount = float(trade.amount) if trade.amount else 0
            role = "Buyer" if trade.buyer_id == db_user.id else "Seller"
            
            keyboard.append([
                InlineKeyboardButton(
                    f"⚖️ Trade #{trade_display} - ${amount:.0f} ({role})",
                    callback_data=f"dispute_trade:{trade.id}"
                )
            ])
        
        keyboard.append([InlineKeyboardButton("📞 Contact Support", callback_data="contact_support")])
        keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])
        
        await safe_edit_message_text(query,
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        
    finally:
        session.close()


async def handle_dispute_trade(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle dispute_trade callback - show dispute reason selection"""
    query = update.callback_query
    if not query:
        return
    
    await safe_answer_callback_query(query, "⚖️ Loading dispute form...")
    user = update.effective_user
    if not user:
        return
    
    # Extract trade ID from callback data
    callback_data = query.data
    if not callback_data.startswith("dispute_trade:"):
        await safe_edit_message_text(query, "❌ Invalid dispute request.")
        return
    
    trade_id = callback_data.split(":", 1)[1]
    
    # Get trade details
    session = SessionLocal()
    try:
        db_user = session.query(User).filter(User.telegram_id == user.id).first()
        if not db_user:
            await safe_edit_message_text(query, "❌ User not found. Please use /start to register.")
            return
        
        # Get the specific trade
        trade = session.query(Escrow).filter(
            Escrow.id == int(trade_id),
            (Escrow.buyer_id == db_user.id) | (Escrow.seller_id == db_user.id)
        ).first()
        
        if not trade:
            await safe_edit_message_text(query,
                "❌ Trade Not Found\n\n"
                "This trade doesn't exist or you don't have access to it.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ]),
                parse_mode="Markdown"
            )
            return
        
        # Show dispute reason selection
        message = "⚖️ Report Issue with Trade\n\n"
        message += f"Trade ID: #{trade.escrow_id[:12]}\n"
        message += f"Amount: ${float(trade.amount):.2f}\n"
        message += f"Status: {trade.status.replace('_', ' ').title()}\n\n"
        message += "Please select the type of issue:\n"
        
        keyboard = [
            [InlineKeyboardButton("💰 Payment Issues", callback_data=f"dispute_reason:payment:{trade.id}")],
            [InlineKeyboardButton("📦 Delivery Problems", callback_data=f"dispute_reason:delivery:{trade.id}")],
            [InlineKeyboardButton("⚠️ Service/Quality Issues", callback_data=f"dispute_reason:service:{trade.id}")],
            [InlineKeyboardButton("🚨 Fraud/Scam", callback_data=f"dispute_reason:fraud:{trade.id}")],
            [InlineKeyboardButton("📝 Other Issue", callback_data=f"dispute_reason:other:{trade.id}")],
            [InlineKeyboardButton("⬅️ Back", callback_data=f"view_trade_{trade.id}")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]
        
        await safe_edit_message_text(query,
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
        
    except ValueError:
        await safe_edit_message_text(query,
            "❌ Invalid Trade ID\n\n"
            "Please try again or contact support.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
    finally:
        session.close()


async def handle_dispute_reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle dispute reason selection - create actual dispute"""
    query = update.callback_query
    if not query:
        return
    
    await safe_answer_callback_query(query, "⏳ Creating dispute...")
    await query.edit_message_text("⏳ Creating dispute...")  # Instant visual feedback
    
    user = update.effective_user
    if not user:
        return
    
    # Parse callback data: dispute_reason:reason:trade_id
    callback_data = query.data
    if not callback_data.startswith("dispute_reason:"):
        await safe_edit_message_text(query, "❌ Invalid dispute reason.")
        return
    
    try:
        _, reason, trade_id = callback_data.split(":", 2)
    except ValueError:
        await safe_edit_message_text(query, "❌ Invalid dispute format.")
        return
    
    session = SessionLocal()
    try:
        db_user = session.query(User).filter(User.telegram_id == user.id).first()
        if not db_user:
            await safe_edit_message_text(query, "❌ User not found. Please use /start to register.")
            return
        
        # Get the trade
        trade = session.query(Escrow).filter(
            Escrow.id == int(trade_id),
            (Escrow.buyer_id == db_user.id) | (Escrow.seller_id == db_user.id)
        ).first()
        
        if not trade:
            await safe_edit_message_text(query,
                "❌ Trade Not Found\n\n"
                "This trade doesn't exist or you don't have access to it.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ]),
                parse_mode="Markdown"
            )
            return
        
        # Check if dispute already exists
        from models import Dispute
        existing_dispute = session.query(Dispute).filter(
            Dispute.escrow_id == trade.id
        ).first()
        
        if existing_dispute:
            await safe_edit_message_text(query,
                f"⚠️ Dispute Already Exists\n\n"
                f"A dispute for this trade is already active.\n"
                f"Dispute ID: #{existing_dispute.id}\n"
                f"Status: {existing_dispute.status.replace('_', ' ').title()}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📋 View Dispute", callback_data=f"view_dispute:{existing_dispute.id}")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ]),
                parse_mode="Markdown"
            )
            return
        
        # Create new dispute
        reason_map = {
            "payment": "Payment Issue",
            "delivery": "Delivery Issue", 
            "service": "Service Issue",
            "fraud": "Fraud/Scam",
            "other": "Other Issue"
        }
        
        dispute_reason = reason_map.get(reason, "Other Issue")
        
        # Determine respondent (the other party in the trade)
        respondent_id = trade.seller_id if db_user.id == trade.buyer_id else trade.buyer_id
        
        new_dispute = Dispute(
            escrow_id=trade.id,
            initiator_id=db_user.id,
            respondent_id=respondent_id,
            dispute_type=dispute_reason,
            reason=dispute_reason,
            status="open"
        )
        session.add(new_dispute)
        session.commit()
        
        # Send admin email notification for dispute opened with action buttons
        try:
            from services.admin_email_actions import AdminDisputeEmailService
            
            # Send dispute resolution email with action buttons (Buyer Wins, Seller Wins, Custom Split, Escalate)
            email_sent = AdminDisputeEmailService.send_dispute_resolution_email(dispute_id=new_dispute.id)
            
            if email_sent:
                logger.info(f"✅ Admin dispute resolution email sent with action buttons: Dispute #{new_dispute.id}")
            else:
                logger.warning(f"⚠️ Admin dispute resolution email failed for: Dispute #{new_dispute.id}")
            
        except Exception as admin_error:
            logger.error(f"Failed to send admin dispute resolution email: {admin_error}")
        
        # SEND NOTIFICATIONS TO BUYER AND SELLER using ConsolidatedNotificationService
        try:
            from services.consolidated_notification_service import (
                ConsolidatedNotificationService,
                NotificationRequest,
                NotificationCategory,
                NotificationChannel,
                NotificationPriority
            )
            
            notification_service = ConsolidatedNotificationService()
            await notification_service.initialize()
            
            # Determine who is initiator and respondent
            initiator_role = "buyer" if new_dispute.initiator_id == trade.buyer_id else "seller"
            respondent_role = "seller" if initiator_role == "buyer" else "buyer"
            
            # INITIATOR (buyer) - EMAIL ONLY (no redundant Telegram notification)
            initiator_request = NotificationRequest(
                user_id=new_dispute.initiator_id,
                category=NotificationCategory.DISPUTES,
                priority=NotificationPriority.HIGH,
                title=f"⚖️ Dispute Created - {dispute_reason}",
                message=f"""⚖️ Dispute Created

You've opened a dispute for trade:
#{trade.escrow_id[:12]} • ${float(trade.amount):.2f}

Reason: {dispute_reason}
Status: Under Review

🔒 Funds are held securely
📧 Admin team has been notified
💬 Use dispute chat to provide details""",
                template_data={
                    "dispute_id": new_dispute.id,
                    "escrow_id": trade.escrow_id[:12],
                    "amount": float(trade.amount),
                    "reason": dispute_reason,
                    "role": initiator_role
                },
                channels=[NotificationChannel.EMAIL]  # EMAIL ONLY - no Telegram redundancy
            )
            
            await notification_service.send_notification(initiator_request)
            logger.info(f"✅ Dispute initiator email sent to user {new_dispute.initiator_id}")
            
            # RESPONDENT (seller) - Compact Telegram with button + Email
            # Get respondent user for telegram_id
            respondent = session.query(User).filter(User.id == new_dispute.respondent_id).first()
            
            if respondent and respondent.telegram_id:
                # Send compact Telegram with "Open Dispute Chat" button
                seller_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💬 Open Dispute Chat", callback_data=f"view_dispute:{new_dispute.id}")],
                    [InlineKeyboardButton("📋 Trade Details", callback_data=f"view_trade_{trade.id}")]
                ])
                
                await context.bot.send_message(
                    chat_id=respondent.telegram_id,
                    text=f"⚠️ Dispute Opened\n\n#{trade.escrow_id[:12]} • ${float(trade.amount):.2f}\nReason: {dispute_reason}\n\n🔒 Funds held • Admin reviewing",
                    reply_markup=seller_keyboard
                )
                logger.info(f"✅ Compact Telegram dispute notification sent to respondent {new_dispute.respondent_id}")
            
            # Also send email to respondent for permanent record
            respondent_email_request = NotificationRequest(
                user_id=new_dispute.respondent_id,
                category=NotificationCategory.DISPUTES,
                priority=NotificationPriority.HIGH,
                title=f"⚠️ Dispute Opened - Trade #{trade.escrow_id[:12]}",
                message=f"""⚠️ Dispute Opened

A dispute has been filed for your trade:
#{trade.escrow_id[:12]} • ${float(trade.amount):.2f}

Reason: {dispute_reason}
Status: Under Review

🔒 Funds are held securely
📧 Admin team is reviewing
💬 Use dispute chat to respond""",
                template_data={
                    "dispute_id": new_dispute.id,
                    "escrow_id": trade.escrow_id[:12],
                    "amount": float(trade.amount),
                    "reason": dispute_reason,
                    "role": respondent_role
                },
                channels=[NotificationChannel.EMAIL]  # Email for permanent record
            )
            
            await notification_service.send_notification(respondent_email_request)
            logger.info(f"✅ Dispute respondent email sent to user {new_dispute.respondent_id}")
            
        except Exception as notification_error:
            logger.error(f"Failed to send buyer/seller dispute notifications: {notification_error}")
        
        # Update trade status to disputed
        trade.status = EscrowStatus.DISPUTED.value
        session.commit()
        
        # Success message with immediate chat access
        message = "✅ Dispute Created\n\n"
        message += f"#{new_dispute.id} • #{trade.escrow_id[:12]}\n\n"
        message += "📧 Admin notified\n"
        message += "💬 Start chatting to explain your issue"
        
        await safe_edit_message_text(query,
            message,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Start Chat Now", callback_data=f"view_dispute:{new_dispute.id}")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        
        logger.info(f"🔥 Dispute created: ID {new_dispute.id} for trade {trade.id} by user {db_user.id}")
        
    except ValueError:
        await safe_edit_message_text(query,
            "❌ Invalid Trade ID\n\n"
            "Please try again or contact support.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error creating dispute: {e}")
        await safe_edit_message_text(query,
            "❌ Error Creating Dispute\n\n"
            "Something went wrong. Please try again or contact support.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📞 Contact Support", callback_data="contact_support")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
    finally:
        session.close()


async def handle_dispute_description(update: Update, context: ContextTypes.DEFAULT_TYPE, trade_id: int, description: str) -> None:
    """Handle user's dispute description and create the dispute"""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    
    user = update.effective_user
    message = update.message
    
    if not user or not message:
        return
    
    # Validate description
    if len(description.strip()) < 10:
        await message.reply_text(
            "❌ Description too short\n\n"
            "Please provide at least 10 characters describing your issue.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        return
    
    session = SessionLocal()
    try:
        db_user = session.query(User).filter(User.telegram_id == user.id).first()
        if not db_user:
            await message.reply_text("❌ User not found. Please use /start to register.")
            return
        
        # Get the trade
        trade = session.query(Escrow).filter(
            Escrow.id == trade_id,
            (Escrow.buyer_id == db_user.id) | (Escrow.seller_id == db_user.id)
        ).first()
        
        if not trade:
            await message.reply_text(
                "❌ Trade Not Found\n\n"
                "This trade doesn't exist or you don't have access to it.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ]),
                parse_mode="Markdown"
            )
            return
        
        # Check if dispute already exists
        from models import Dispute
        existing_dispute = session.query(Dispute).filter(
            Dispute.escrow_id == trade.id
        ).first()
        
        if existing_dispute:
            await message.reply_text(
                f"⚠️ Dispute Already Exists\n\n"
                f"A dispute for this trade is already active.\n"
                f"Dispute ID: #{existing_dispute.id}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ]),
                parse_mode="Markdown"
            )
            return
        
        # Determine respondent (the other party in the trade)
        respondent_id = trade.seller_id if db_user.id == trade.buyer_id else trade.buyer_id
        
        # Create new dispute with user description
        new_dispute = Dispute(
            escrow_id=trade.id,
            initiator_id=db_user.id,
            respondent_id=respondent_id,
            dispute_type="user_reported",
            reason=description[:500],  # User's description of the issue
            status="open"
        )
        session.add(new_dispute)
        session.commit()
        
        # Send admin email notification for dispute opened with action buttons
        try:
            from services.admin_email_actions import AdminDisputeEmailService
            
            # Send dispute resolution email with action buttons (Buyer Wins, Seller Wins, Custom Split, Escalate)
            email_sent = AdminDisputeEmailService.send_dispute_resolution_email(dispute_id=new_dispute.id)
            
            if email_sent:
                logger.info(f"✅ Admin dispute resolution email sent with action buttons: Dispute #{new_dispute.id}")
            else:
                logger.warning(f"⚠️ Admin dispute resolution email failed for: Dispute #{new_dispute.id}")
            
        except Exception as admin_error:
            logger.error(f"Failed to send admin dispute resolution email: {admin_error}")
        
        # SEND NOTIFICATIONS TO BUYER AND SELLER using ConsolidatedNotificationService
        try:
            from services.consolidated_notification_service import (
                ConsolidatedNotificationService,
                NotificationRequest,
                NotificationCategory,
                NotificationChannel,
                NotificationPriority
            )
            
            notification_service = ConsolidatedNotificationService()
            await notification_service.initialize()
            
            # Determine who is initiator and respondent
            initiator_role = "buyer" if new_dispute.initiator_id == trade.buyer_id else "seller"
            respondent_role = "seller" if initiator_role == "buyer" else "buyer"
            
            # INITIATOR (buyer) - EMAIL ONLY (no redundant Telegram notification)
            initiator_request = NotificationRequest(
                user_id=new_dispute.initiator_id,
                category=NotificationCategory.DISPUTES,
                priority=NotificationPriority.HIGH,
                title=f"⚖️ Dispute Created - {description[:50]}",
                message=f"""⚖️ Dispute Created

You've opened a dispute for trade:
#{trade.escrow_id[:12]} • ${float(trade.amount):.2f}

Your Issue: {description[:150]}{'...' if len(description) > 150 else ''}
Status: Under Review

🔒 Funds are held securely
📧 Admin team has been notified
💬 Use dispute chat to provide details""",
                template_data={
                    "dispute_id": new_dispute.id,
                    "escrow_id": trade.escrow_id[:12],
                    "amount": float(trade.amount),
                    "reason": description[:500],
                    "role": initiator_role
                },
                channels=[NotificationChannel.EMAIL]  # EMAIL ONLY - no Telegram redundancy
            )
            
            await notification_service.send_notification(initiator_request)
            logger.info(f"✅ Dispute initiator email sent to user {new_dispute.initiator_id}")
            
            # RESPONDENT (seller) - Compact Telegram with button + Email
            # Get respondent user for telegram_id
            respondent = session.query(User).filter(User.id == new_dispute.respondent_id).first()
            
            if respondent and respondent.telegram_id:
                # Send compact Telegram with "Open Dispute Chat" button
                seller_keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💬 Open Dispute Chat", callback_data=f"view_dispute:{new_dispute.id}")],
                    [InlineKeyboardButton("📋 Trade Details", callback_data=f"view_trade_{trade.id}")]
                ])
                
                await context.bot.send_message(
                    chat_id=respondent.telegram_id,
                    text=f"⚠️ Dispute Opened\n\n#{trade.escrow_id[:12]} • ${float(trade.amount):.2f}\nIssue: {description[:100]}{'...' if len(description) > 100 else ''}\n\n🔒 Funds held • Admin reviewing",
                    reply_markup=seller_keyboard
                )
                logger.info(f"✅ Compact Telegram dispute notification sent to respondent {new_dispute.respondent_id}")
            
            # Also send email to respondent for permanent record
            respondent_email_request = NotificationRequest(
                user_id=new_dispute.respondent_id,
                category=NotificationCategory.DISPUTES,
                priority=NotificationPriority.HIGH,
                title=f"⚠️ Dispute Opened - Trade #{trade.escrow_id[:12]}",
                message=f"""⚠️ Dispute Opened

A dispute has been filed for your trade:
#{trade.escrow_id[:12]} • ${float(trade.amount):.2f}

Issue Reported: {description[:150]}{'...' if len(description) > 150 else ''}
Status: Under Review

🔒 Funds are held securely
📧 Admin team is reviewing
💬 Use dispute chat to respond""",
                template_data={
                    "dispute_id": new_dispute.id,
                    "escrow_id": trade.escrow_id[:12],
                    "amount": float(trade.amount),
                    "reason": description[:500],
                    "role": respondent_role
                },
                channels=[NotificationChannel.EMAIL]  # Email for permanent record
            )
            
            await notification_service.send_notification(respondent_email_request)
            logger.info(f"✅ Dispute respondent email sent to user {new_dispute.respondent_id}")
            
        except Exception as notification_error:
            logger.error(f"Failed to send buyer/seller dispute notifications: {notification_error}")
        
        # Update trade status to disputed
        trade.status = EscrowStatus.DISPUTED.value
        session.commit()
        
        # Clear context
        context.user_data.pop('dispute_trade_id', None)
        context.user_data.pop('awaiting_dispute_description', None)
        
        # Success message with immediate chat access
        message_text = "✅ Dispute Created\n\n"
        message_text += f"#{new_dispute.id} • #{trade.escrow_id[:12]}\n\n"
        message_text += "📧 Admin notified\n"
        message_text += "💬 Start chatting to explain your issue"
        
        await message.reply_text(
            message_text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Start Chat Now", callback_data=f"view_dispute:{new_dispute.id}")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
        
        logger.info(f"🔥 Dispute created: ID {new_dispute.id} for trade {trade.id} by user {db_user.id}")
        
    except Exception as e:
        logger.error(f"Error creating dispute: {e}")
        await message.reply_text(
            "❌ Error Creating Dispute\n\n"
            "Something went wrong. Please try again or contact support.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📞 Contact Support", callback_data="contact_support")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]),
            parse_mode="Markdown"
        )
    finally:
        session.close()


@audit_conversation_handler("chat_message_input")
async def handle_chat_message_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text message input in active chat sessions with 3-way communication for disputes"""
    user = update.effective_user
    if not user or not update.message or not update.message.text:
        logger.warning(f"Invalid message input from user {user.id if user else 'unknown'}")
        return ConversationHandler.END
    
    logger.info(f"📝 CHAT MESSAGE INPUT: User {user.id} sent message in conversation state")
    
    # Check for active chat session - prioritize universal session manager
    active_chat = context.user_data.get('active_chat') or active_chat_sessions.get(user.id)
    
    # CRITICAL FIX: Also check universal session manager for active trade chats
    if not active_chat or active_chat.get('type') != 'trade':
        # Check if user has any active trade chat sessions
        user_sessions = universal_session_manager.get_user_sessions(user.id)
        trade_chat_sessions = [s for s in user_sessions if s.session_type == SessionType.TRADE_CHAT and s.status == OperationStatus.ACTIVE]
        
        if trade_chat_sessions:
            # Use the most recent active trade chat session
            latest_session = max(trade_chat_sessions, key=lambda x: x.updated_at)
            trade_id = latest_session.metadata.get('trade_id')
            if trade_id:
                # Restore the active chat context
                active_chat = {'type': 'trade', 'id': trade_id}
                context.user_data['active_chat'] = active_chat
                active_chat_sessions[user.id] = active_chat
                logger.info(f"✅ Restored active trade chat session {latest_session.session_id} for user {user.id}")
    
    if not active_chat or active_chat.get('type') != 'trade':
        # Try to find any active disputed trade for this user
        session = SessionLocal()
        try:
            db_user = session.query(User).filter(User.telegram_id == user.id).first()
            if db_user:
                # Look for any disputed trade involving this user
                disputed_trade = session.query(Escrow).filter(
                    or_(Escrow.buyer_id == db_user.id, Escrow.seller_id == db_user.id),
                    Escrow.status == EscrowStatus.DISPUTED.value
                ).first()
                
                if disputed_trade:
                    # Establish session using universal manager
                    session_id = f"trade_chat_{disputed_trade.id}_{user.id}"
                    universal_session_manager.create_session(
                        user_id=user.id,
                        session_type=SessionType.TRADE_CHAT,
                        session_id=session_id,
                        metadata={'trade_id': disputed_trade.id, 'is_disputed': True}
                    )
                    
                    # Legacy support
                    active_chat_sessions[user.id] = {'type': 'trade', 'id': disputed_trade.id}
                    context.user_data['active_chat'] = {'type': 'trade', 'id': disputed_trade.id}
                    logger.info(f"Auto-established disputed trade session: {session_id}")
                    # Continue processing with the established session
                    active_chat = {'type': 'trade', 'id': disputed_trade.id}
                else:
                    await update.message.reply_text("❌ No active chat session. Please open a trade chat first.")
                    return ConversationHandler.END
            else:
                await update.message.reply_text("❌ No active chat session. Please open a trade chat first.")
                return ConversationHandler.END
        except Exception as e:
            logger.error(f"Failed to auto-establish session: {e}")
            await update.message.reply_text("❌ No active chat session. Please open a trade chat first.")
            return ConversationHandler.END
        finally:
            session.close()
    
    trade_id = active_chat.get('id')
    if not trade_id:
        await update.message.reply_text("❌ Invalid chat session. Please try again.")
        return ConversationHandler.END
    
    session = SessionLocal()
    try:
        # Get user and trade - allow admins to participate without user records
        db_user = session.query(User).filter(User.telegram_id == user.id).first()
        is_admin = is_admin_silent(user.id)  # Use silent check to avoid false security alerts
        
        if not db_user and not is_admin:
            await update.message.reply_text("❌ User not found. Please restart with /start")
            return ConversationHandler.END
        elif not db_user and is_admin:
            # Admin without user record - create a temporary user context for chat participation
            logger.info(f"Admin {user.id} participating in chat without user record")
        
        trade = session.query(Escrow).filter(Escrow.id == trade_id).first()
        if not trade:
            await update.message.reply_text("❌ Trade not found.")
            return ConversationHandler.END
        
        # Check if trade is resolved - read-only mode
        if str(trade.status).lower() in ['completed', 'cancelled', 'resolved']:
            await update.message.reply_text(
                "💬 Chat is Read-Only\n\n"
                "This trade has been resolved. You can view message history but cannot send new messages.",
                parse_mode="Markdown"
            )
            return ConversationHandler.END
        
        # Verify access - handle admin without user record
        if not is_admin and db_user and not (db_user.id == trade.buyer_id or db_user.id == trade.seller_id):
            logger.warning(f"Access denied for user {user.id} to trade {trade.id}")
            await update.message.reply_text("❌ Access denied.")
            return ConversationHandler.END
        
        # For disputed trades, ensure dispute chat session is established
        if str(trade.status).lower() == 'disputed':
            from handlers.dispute_chat import active_dispute_chat
            from handlers.multi_dispute_manager import dispute_manager
            dispute = session.query(Dispute).filter(
                Dispute.escrow_id == trade.id,
                Dispute.status == "open"
            ).first()
            if dispute:
                # Use multi-dispute manager for multiple concurrent disputes
                dispute_manager.add_dispute_session(user.id, dispute.id)
                if user.id not in active_dispute_chat:
                    active_dispute_chat[user.id] = dispute.id  # Legacy support
                logger.info(f"Established dispute chat session for user {user.id} in dispute {dispute.id}")
        
        message_text = update.message.text
        
        # Store message in appropriate table based on trade status
        if str(trade.status).lower() == 'disputed':
            # Store in dispute messages for 3-way communication
            dispute = session.query(Dispute).filter(Dispute.escrow_id == trade.id).first()
            if dispute:
                # Use special admin sender ID for admins without user records
                admin_sender_id = db_user.id if db_user else -int(str(user.id)[-8:])  # Negative ID for admin
                new_message = DisputeMessage(
                    dispute_id=dispute.id,
                    sender_id=admin_sender_id,
                    message=message_text
                )
                session.add(new_message)
            else:
                # Fallback to trade messages if no dispute found
                admin_sender_id = db_user.id if db_user else -int(str(user.id)[-8:])  # Negative ID for admin
                new_message = EscrowMessage(
                    escrow_id=trade.id,
                    sender_id=admin_sender_id,
                    content=message_text
                )
                session.add(new_message)
        else:
            # Store in regular trade messages
            admin_sender_id = db_user.id if db_user else -int(str(user.id)[-8:])  # Negative ID for admin
            new_message = EscrowMessage(
                escrow_id=trade.id,
                sender_id=admin_sender_id,
                content=message_text
            )
            session.add(new_message)
        
        session.commit()
        
        # Send confirmation to sender
        await update.message.reply_text("📤 Message sent!")
        
        # CRITICAL: 3-way notification system for disputed trades
        if str(trade.status).lower() == 'disputed':
            # Get all parties: buyer, seller, and admin
            notification_recipients = []
            
            # Add buyer if not sender
            if not db_user or trade.buyer_id != db_user.id:
                buyer = session.query(User).filter(User.id == trade.buyer_id).first()
                if buyer:
                    notification_recipients.append(('buyer', buyer))
            
            # Add seller if not sender  
            if trade.seller_id and (not db_user or trade.seller_id != db_user.id):
                seller = session.query(User).filter(User.id == trade.seller_id).first()
                if seller:
                    notification_recipients.append(('seller', seller))
            
            # Add admin if not sender
            if not is_admin:
                logger.info(f"🚨 DISPUTE MESSAGE DETECTED - Adding admin notifications for trade {trade.id}")
                try:
                    from utils.admin_security import AdminSecurityManager
                    admin_manager = AdminSecurityManager()
                    admin_ids = list(admin_manager.get_admin_ids())
                    logger.info(f"📋 Retrieved {len(admin_ids)} admin IDs for 3-way notification")
                    
                    for admin_id in admin_ids:
                        # Create pseudo user object for admin notification
                        admin_pseudo_user = type('AdminUser', (), {'telegram_id': str(admin_id)})()
                        notification_recipients.append(('admin', admin_pseudo_user))
                        logger.info(f"📧 Added admin {admin_id} to notification recipients")
                except Exception as admin_e:
                    logger.error(f"Failed to get admin IDs for 3-way notification: {admin_e}")
            
            # Send notifications to all parties
            sender_name = get_user_display_name(db_user) if db_user else f"Admin {user.first_name or user.username or user.id}"
            trade_display = trade.escrow_id[-6:] if trade.escrow_id else str(trade.id)
            
            for role, recipient in notification_recipients:
                try:
                    role_emoji = {'buyer': '👤', 'seller': '🛍️', 'admin': '⚖️'}.get(role, '💬')
                    
                    notification_text = f"""{role_emoji} Dispute Message
                    
💰 Trade #{trade_display} • ${float(trade.amount):.0f}
📝 From: {sender_name}

"{message_text}"

⚖️ This trade is under dispute review."""
                    
                    # Use the unified trade_chat_open handler for all messages
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("💬 Reply in Dispute", callback_data=f"trade_chat_open:{trade.id}")],
                        [InlineKeyboardButton("📋 Trade Details", callback_data=f"track_status_{trade.escrow_id}")]
                    ])
                    
                    await context.bot.send_message(
                        chat_id=recipient.telegram_id,
                        text=notification_text,
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                    logger.info(f"✅ 3-way notification sent to {role} {recipient.telegram_id} for disputed trade {trade.id}")
                    
                except Exception as e:
                    logger.error(f"Failed to notify {role} {recipient.telegram_id}: {e}")
        
        else:
            # Regular 2-way notification for non-disputed trades
            # Notify counterparty only
            counterpart_id = trade.seller_id if db_user.id == trade.buyer_id else trade.buyer_id
            if counterpart_id:
                counterpart = session.query(User).filter(User.id == counterpart_id).first()
                if counterpart:
                    try:
                        sender_name = get_user_display_name(db_user)
                        trade_display = trade.escrow_id[-6:] if trade.escrow_id else str(trade.id)
                        
                        notification_text = f"""💬 New Message
                        
💰 Trade #{trade_display} • ${float(trade.amount):.0f}
📝 From: {sender_name}

"{message_text}"""
                        
                        keyboard = InlineKeyboardMarkup([
                            [InlineKeyboardButton("💬 Reply", callback_data=f"trade_chat_open:{trade.id}")]
                        ])
                        
                        await context.bot.send_message(
                            chat_id=counterpart.telegram_id,
                            text=notification_text,
                            reply_markup=keyboard,
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Failed to notify counterparty: {e}")
        
        logger.info(f"💬 Message sent in trade {trade_id} by user {user.id} ({'disputed' if str(trade.status).lower() == 'disputed' else 'normal'} trade)")
        
    except Exception as e:
        logger.error(f"Error handling chat message: {e}")
        await update.message.reply_text(
            "❌ Failed to send message. Please try again."
        )
    finally:
        session.close()
    
    return MESSAGE_INPUT  # Stay in message input mode


async def clear_chat_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Clear active chat session and return to main menu"""
    user = update.effective_user
    if not user:
        return ConversationHandler.END
    
    # Clear any active chat sessions
    if 'active_chat' in context.user_data:
        del context.user_data['active_chat']
    if user.id in active_chat_sessions:
        del active_chat_sessions[user.id]
        
    logger.info(f"🗑️ Cleared chat session for user {user.id}")
    
    # Return to main menu
    from handlers.start import show_main_menu
    return await show_main_menu(update, context)
