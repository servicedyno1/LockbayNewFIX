"""
Multi-Dispute Chat Management System
Allows users and admins to manage multiple concurrent dispute chats
"""

import logging
from typing import Dict, Set, Optional, List, Tuple
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import or_, desc

from database import async_managed_session
from sqlalchemy import select
from models import (
    Dispute, DisputeMessage, Escrow, EscrowStatus, User
)
from utils.admin_security import is_admin_silent

logger = logging.getLogger(__name__)

class MultiDisputeManager:
    """Manages multiple concurrent dispute sessions for users and admins"""
    
    def __init__(self):
        # Maps user_id to set of active dispute_ids
        self.active_disputes: Dict[int, Set[int]] = {}
        # Maps user_id to currently selected dispute_id for messaging
        self.current_dispute: Dict[int, int] = {}
        # Maps user_id to last interaction time per dispute
        self.last_interaction: Dict[int, Dict[int, datetime]] = {}
    
    def add_dispute_session(self, user_id: int, dispute_id: int) -> None:
        """Add a dispute to user's active sessions"""
        if user_id not in self.active_disputes:
            self.active_disputes[user_id] = set()
            self.last_interaction[user_id] = {}
        
        self.active_disputes[user_id].add(dispute_id)
        self.current_dispute[user_id] = dispute_id
        self.last_interaction[user_id][dispute_id] = datetime.utcnow()
        logger.info(f"Added dispute {dispute_id} to user {user_id}'s active sessions")
    
    def remove_dispute_session(self, user_id: int, dispute_id: int) -> None:
        """Remove a dispute from user's active sessions"""
        if user_id in self.active_disputes:
            self.active_disputes[user_id].discard(dispute_id)
            if not self.active_disputes[user_id]:
                del self.active_disputes[user_id]
            
            if user_id in self.current_dispute and self.current_dispute[user_id] == dispute_id:
                # Switch to another active dispute if available
                if user_id in self.active_disputes and self.active_disputes[user_id]:
                    self.current_dispute[user_id] = next(iter(self.active_disputes[user_id]))
                else:
                    del self.current_dispute[user_id]
    
    def set_current_dispute(self, user_id: int, dispute_id: int) -> bool:
        """Set the current active dispute for messaging"""
        if user_id in self.active_disputes and dispute_id in self.active_disputes[user_id]:
            self.current_dispute[user_id] = dispute_id
            self.last_interaction[user_id][dispute_id] = datetime.utcnow()
            return True
        return False
    
    def get_current_dispute(self, user_id: int) -> Optional[int]:
        """Get user's current active dispute"""
        return self.current_dispute.get(user_id)
    
    def get_user_disputes(self, user_id: int) -> Set[int]:
        """Get all active disputes for a user"""
        return self.active_disputes.get(user_id, set())
    
    def clear_user_sessions(self, user_id: int) -> None:
        """Clear all dispute sessions for a user"""
        self.active_disputes.pop(user_id, None)
        self.current_dispute.pop(user_id, None)
        self.last_interaction.pop(user_id, None)
    
    async def auto_establish_session(self, user_id: int, is_admin: bool = False) -> Optional[List[Tuple[int, str]]]:
        """Auto-establish dispute sessions for user based on their active disputes"""
        async with async_managed_session() as session:
            try:
                stmt = select(User).where(User.telegram_id == str(user_id))
                result = await session.execute(stmt)
                db_user = result.scalar_one_or_none()
                
                if not db_user and not is_admin:
                    return None
                
                disputes_info = []
                
                if is_admin:
                    # Admins can access all active disputes
                    disputes_stmt = select(Dispute).where(
                        Dispute.status.in_(["open", "under_review"])
                    ).order_by(desc(Dispute.created_at))
                    disputes_result = await session.execute(disputes_stmt)
                    active_disputes = disputes_result.scalars().all()
                else:
                    # Users can access disputes they're involved in
                    escrows_stmt = select(Escrow).where(
                        or_(Escrow.buyer_id == db_user.id, Escrow.seller_id == db_user.id),
                        Escrow.status == EscrowStatus.DISPUTED.value
                    )
                    escrows_result = await session.execute(escrows_stmt)
                    user_escrows = escrows_result.scalars().all()
                    
                    escrow_ids = [e.id for e in user_escrows]
                    disputes_stmt = select(Dispute).where(
                        Dispute.escrow_id.in_(escrow_ids),
                        Dispute.status.in_(["open", "under_review"])
                    )
                    disputes_result = await session.execute(disputes_stmt)
                    active_disputes = disputes_result.scalars().all()
                
                for dispute in active_disputes:
                    self.add_dispute_session(user_id, dispute.id)
                    escrow_stmt = select(Escrow).where(Escrow.id == dispute.escrow_id)
                    escrow_result = await session.execute(escrow_stmt)
                    escrow = escrow_result.scalar_one_or_none()
                    if escrow:
                        disputes_info.append((dispute.id, f"#{escrow.escrow_id[:12]}"))
                
                return disputes_info if disputes_info else None
                
            except Exception as e:
                logger.error(f"Error auto-establishing dispute sessions: {e}")
                return None
    
    async def get_dispute_selector_keyboard(self, user_id: int) -> Optional[InlineKeyboardMarkup]:
        """Create keyboard for selecting between multiple active disputes"""
        disputes = self.get_user_disputes(user_id)
        if not disputes or len(disputes) <= 1:
            return None
        
        async with async_managed_session() as session:
            try:
                buttons = []
                current = self.get_current_dispute(user_id)
                
                for dispute_id in sorted(disputes):
                    dispute_stmt = select(Dispute).where(Dispute.id == dispute_id)
                    dispute_result = await session.execute(dispute_stmt)
                    dispute = dispute_result.scalar_one_or_none()
                    
                    if dispute:
                        escrow_stmt = select(Escrow).where(Escrow.id == dispute.escrow_id)
                        escrow_result = await session.execute(escrow_stmt)
                        escrow = escrow_result.scalar_one_or_none()
                        
                        if escrow:
                            is_current = "✅ " if dispute_id == current else ""
                            button_text = f"{is_current}#{escrow.escrow_id[:12]} - Dispute #{dispute_id}"
                            buttons.append([InlineKeyboardButton(
                                button_text,
                                callback_data=f"select_dispute_{dispute_id}"
                            )])
                
                if buttons:
                    buttons.append([InlineKeyboardButton("❌ Close Selector", callback_data="close_dispute_selector")])
                    return InlineKeyboardMarkup(buttons)
                
                return None
                
            except Exception as e:
                logger.error(f"Error creating dispute selector: {e}")
                return None


# Global instance
dispute_manager = MultiDisputeManager()


async def handle_dispute_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle dispute selection from keyboard"""
    query = update.callback_query
    user = update.effective_user
    
    if not query or not user:
        return ConversationHandler.END
    
    # CRITICAL FIX: Do NOT answer callback here - it's already answered by direct_select_dispute
    # Answering twice causes Telegram API error and makes button unresponsive
    # await safe_answer_callback_query(query)  # REMOVED - causes double-answer bug
    
    if query.data == "close_dispute_selector":
        await query.message.delete()
        return ConversationHandler.END
    
    if query.data.startswith("select_dispute_") or query.data.startswith("view_dispute:"):
        try:
            # Handle both formats: select_dispute_123 and view_dispute:123
            if ":" in query.data:
                dispute_id = int(query.data.split(":")[-1])
            else:
                dispute_id = int(query.data.split("_")[2])
            
            if dispute_manager.set_current_dispute(user.id, dispute_id):
                from database import async_managed_session
                from sqlalchemy import select
                
                async with async_managed_session() as session:
                    dispute_stmt = select(Dispute).where(Dispute.id == dispute_id)
                    dispute_result = await session.execute(dispute_stmt)
                    dispute = dispute_result.scalar_one_or_none()
                    
                    escrow = None
                    if dispute:
                        escrow_stmt = select(Escrow).where(Escrow.id == dispute.escrow_id)
                        escrow_result = await session.execute(escrow_stmt)
                        escrow = escrow_result.scalar_one_or_none()
                    
                    if dispute and escrow:
                        try:
                            # Fetch recent dispute messages (last 10)
                            messages_stmt = select(DisputeMessage).where(
                                DisputeMessage.dispute_id == dispute_id
                            ).order_by(desc(DisputeMessage.created_at)).limit(10)
                            messages_result = await session.execute(messages_stmt)
                            recent_messages = messages_result.scalars().all()
                            
                            # Build enhanced message with better visual hierarchy
                            # Header section with dispute info
                            message_text = f"⚠️ DISPUTE #{dispute_id}\n"
                            message_text += "━━━━━━━━━━━━━━━━━━━━━━\n"
                            message_text += f"📦 Trade: #{escrow.escrow_id}\n"
                            message_text += f"💰 Amount: ${float(escrow.amount):.2f} USD\n"
                            
                            # Dispute status
                            status_emoji = "🔴" if dispute.status == "open" else "🟢" if dispute.status == "resolved" else "🟡"
                            status_text = dispute.status.upper() if dispute.status else "OPEN"
                            message_text += f"{status_emoji} Status: {status_text}\n"
                            
                            # Created date
                            created_date = dispute.created_at.strftime("%b %d, %Y") if dispute.created_at else "Unknown"
                            message_text += f"📅 Created: {created_date}\n"
                            message_text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                            
                            if recent_messages:
                                message_text += f"📜 Recent Messages ({len(recent_messages)})\n\n"
                                # Reverse to show oldest first (chronological order)
                                for msg in reversed(recent_messages):
                                    # Determine sender role with emoji
                                    if msg.sender_id == escrow.buyer_id:
                                        sender_icon = "🛒"
                                        sender_role = "Buyer"
                                    elif msg.sender_id == escrow.seller_id:
                                        sender_icon = "🏪"
                                        sender_role = "Seller"
                                    else:
                                        sender_icon = "👔"
                                        sender_role = "Admin"
                                    
                                    # Format timestamp
                                    timestamp = msg.created_at.strftime("%m/%d %H:%M") if msg.created_at else ""
                                    
                                    # Truncate long messages (plain text - no escaping needed)
                                    message_preview = msg.message[:60] + "..." if len(msg.message) > 60 else msg.message
                                    
                                    message_text += f"{sender_icon} {sender_role} • {timestamp}\n"
                                    message_text += f"   {message_preview}\n\n"
                            else:
                                message_text += "📭 No messages yet\n\n"
                            
                            message_text += "━━━━━━━━━━━━━━━━━━━━━━\n"
                            message_text += "💬 Dispute Chat Active\n"
                            message_text += "✍️ Type your message below"
                            
                            try:
                                await query.edit_message_text(message_text)
                                logger.info(f"✅ User {user.id} switched to dispute {dispute_id} - chat interface displayed")
                            except Exception as edit_error:
                                # Telegram raises error if message content is identical - this is harmless
                                if "Message is not modified" in str(edit_error):
                                    logger.debug(f"Dispute {dispute_id} already displayed (no update needed)")
                                elif "message to edit not found" in str(edit_error).lower():
                                    logger.error(f"❌ CRITICAL: Message was deleted before edit could complete for dispute {dispute_id}")
                                    # Send a new message since the original was deleted
                                    try:
                                        await context.bot.send_message(
                                            chat_id=user.id,
                                            text=message_text,
                                            parse_mode=None
                                        )
                                        logger.info(f"✅ Sent new message for dispute {dispute_id} after original was deleted")
                                    except Exception as send_error:
                                        logger.error(f"❌ Failed to send new message: {send_error}")
                                else:
                                    logger.error(f"❌ BUTTON_UNRESPONSIVE_ERROR: Failed to edit message for dispute {dispute_id}: {edit_error}")
                                    # Try to notify user of the issue
                                    try:
                                        await context.bot.send_message(
                                            chat_id=user.id,
                                            text=f"⚠️ Error loading dispute chat. Please try clicking the button again.\n\nError: {str(edit_error)[:100]}"
                                        )
                                    except Exception:
                                        pass  # Silent fail if we can't even send error message
                        except Exception as msg_error:
                            logger.error(f"❌ OUTER_ERROR: Exception in dispute message handling: {msg_error}")
                    else:
                        try:
                            await query.edit_message_text("❌ Dispute not found.")
                        except Exception as msg_error:
                            logger.error(f"Error editing error message: {msg_error}")
            else:
                await query.edit_message_text("❌ Cannot switch to this dispute.")
        except Exception as e:
            logger.error(f"Error selecting dispute: {e}")
            await query.edit_message_text("❌ Error selecting dispute.")
    
    return ConversationHandler.END


async def show_active_disputes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show user's active disputes with ability to switch between them"""
    user = update.effective_user
    if not user:
        return ConversationHandler.END
    
    is_admin = is_admin_silent(user.id)  # Use silent check for UI display
    
    # Auto-establish sessions
    disputes_info = await dispute_manager.auto_establish_session(user.id, is_admin)
    
    if not disputes_info:
        message = "📭 You have no active disputes."
        if update.callback_query:
            await update.callback_query.edit_message_text(message)
        else:
            await update.message.reply_text(message)
        return ConversationHandler.END
    
    current = dispute_manager.get_current_dispute(user.id)
    
    message = f"📋 Your Active Disputes ({len(disputes_info)})\n\n"
    for dispute_id, trade_ref in disputes_info:
        is_current = "✅ " if dispute_id == current else "   "
        message += f"{is_current}Dispute #{dispute_id} - Trade {trade_ref}\n"
    
    if len(disputes_info) > 1:
        message += "\n💡 Use the buttons below to switch between disputes."
        keyboard = await dispute_manager.get_dispute_selector_keyboard(user.id)
    else:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("💬 Open Chat", callback_data=f"view_dispute:{disputes_info[0][0]}")
        ]])
    
    if update.callback_query:
        await update.callback_query.edit_message_text(message, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update.message.reply_text(message, parse_mode="Markdown", reply_markup=keyboard)
    
    return ConversationHandler.END