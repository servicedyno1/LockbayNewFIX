"""
User Rating System Handler
Handles user feedback and rating functionality for completed trades
"""

import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import desc, select
from typing import Optional, Any

from database import async_managed_session
from models import Rating, User, Escrow
from utils.callback_utils import safe_edit_message_text, safe_answer_callback_query
from utils.button_handler_async import button_callback_wrapper
from services.admin_trade_notifications import AdminTradeNotificationService

logger = logging.getLogger(__name__)

# Rating conversation states
RATING_SELECT, RATING_COMMENT = range(2)


async def handle_rate_seller(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle seller rating callback"""
    query = update.callback_query
    user = update.effective_user
    
    if not query or not user:
        return ConversationHandler.END
    
    try:
        async with button_callback_wrapper(update, "⭐ Loading rating...") as session:
            # Extract escrow ID from callback data
            if not query.data:
                await query.edit_message_text("❌ Invalid request")
                return ConversationHandler.END
            escrow_id = query.data.split(':')[1]
            # Get user and trade
            result = await session.execute(select(User).where(User.telegram_id == int(user.id)))
            db_user = result.scalar_one_or_none()
            if not db_user:
                await query.edit_message_text("❌ User not found. Please restart with /start")
                return ConversationHandler.END
            
            # Get the completed trade
            result = await session.execute(select(Escrow).where(Escrow.escrow_id == escrow_id))
            trade = result.scalar_one_or_none()
            if not trade:
                await query.edit_message_text("❌ Trade not found")
                return ConversationHandler.END
            
            # SECURITY: Verify trade is COMPLETED or REFUNDED (allow ratings for dispute-resolved trades)
            from models import EscrowStatus
            # Handle both Enum and string comparisons for robustness
            trade_status = trade.status.value if hasattr(trade.status, 'value') else trade.status
            # Allow ratings for COMPLETED (normal) or REFUNDED (dispute resolved)
            allowed_statuses = [EscrowStatus.COMPLETED.value, EscrowStatus.REFUNDED.value]
            if trade_status not in allowed_statuses:
                await query.edit_message_text(
                    f"❌ Can only rate completed trades\n\n"
                    f"This trade is currently: {trade_status}"
                )
                return ConversationHandler.END
            
            # Verify user is buyer (only buyers can rate sellers)
            buyer_id_value = getattr(trade, 'buyer_id', None)
            if buyer_id_value is None or db_user.id != buyer_id_value:
                await query.edit_message_text("❌ Only buyers can rate sellers")
                return ConversationHandler.END
            
            # Check if already rated
            result = await session.execute(
                select(Rating).where(
                    Rating.escrow_id == trade.id,
                    Rating.rater_id == db_user.id,
                    Rating.category == 'seller'
                )
            )
            existing_rating = result.scalar_one_or_none()
            
            if existing_rating:
                comment_text = existing_rating.comment if existing_rating.comment is not None else 'No comment'
                await query.edit_message_text(
                    f"✅ Already Rated\n\n"
                    f"You gave this seller {existing_rating.rating}⭐ stars\n"
                    f"Comment: {comment_text}\n\n"
                    f"Rating submitted on {existing_rating.created_at.strftime('%Y-%m-%d')}"
                )
                return ConversationHandler.END
            
            # Get seller info
            result = await session.execute(select(User).where(User.id == trade.seller_id))
            seller = result.scalar_one_or_none()
            seller_name = "Seller"
            if seller:
                if seller.username is not None:
                    seller_name = f"@{seller.username}"
                elif seller.first_name is not None:
                    seller_name = seller.first_name
            
            # Store IDs in context for later use (avoid detached instances)
            context.user_data['rating_escrow_id'] = trade.id
            context.user_data['rating_escrow_string_id'] = trade.escrow_id
            context.user_data['rating_seller_id'] = seller.id if seller else None
            context.user_data['rating_type'] = 'seller'
            
            # Show rating selection interface
            # Extract Python value from SQLAlchemy column for type safety
            amount_value = float(getattr(trade, 'amount', 0) or 0)
            message = f"⭐ Rate \n\n"
            message += f"Trade #{trade.escrow_id[-6:]} - ${amount_value:.2f}\n\n"
            message += "How was your experience with this seller?"
            
            keyboard = [
                [InlineKeyboardButton("⭐⭐⭐⭐⭐ Excellent (5)", callback_data="rating_5")],
                [InlineKeyboardButton("⭐⭐⭐⭐ Good (4)", callback_data="rating_4")],
                [InlineKeyboardButton("⭐⭐⭐ Average (3)", callback_data="rating_3")],
                [InlineKeyboardButton("⭐⭐ Poor (2)", callback_data="rating_2")],
                [InlineKeyboardButton("⭐ Very Poor (1)", callback_data="rating_1")],
                [InlineKeyboardButton("🚫 Cancel", callback_data="main_menu")]
            ]
            
            await safe_edit_message_text(
                query,
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            return RATING_SELECT
            
    except Exception as e:
        # Handle "Message is not modified" error silently (Telegram API limitation)
        if "Message is not modified" not in str(e):
            logger.error(f"Error in handle_rate_seller: {e}")
            try:
                await query.edit_message_text("❌ Something went wrong. Please try again.")
            except:
                pass
        return ConversationHandler.END


async def handle_rate_dispute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle dispute rating callback"""
    query = update.callback_query
    user = update.effective_user
    
    if not query or not user:
        return ConversationHandler.END
    
    try:
        async with button_callback_wrapper(update, "⭐ Loading dispute rating...") as session:
            # Parse: rate_dispute:escrow_id:outcome:resolution_type
            if not query.data:
                await query.edit_message_text("❌ Invalid request")
                return ConversationHandler.END
            parts = query.data.split(':')
            escrow_id = parts[1]
            dispute_outcome = parts[2]  # 'winner' or 'loser'
            resolution_type = parts[3]  # 'refund' or 'release'
            # Get user and trade
            result = await session.execute(select(User).where(User.telegram_id == int(user.id)))
            db_user = result.scalar_one_or_none()
            if not db_user:
                await query.edit_message_text("❌ User not found. Please restart with /start")
                return ConversationHandler.END
            
            # Get the completed trade
            result = await session.execute(select(Escrow).where(Escrow.escrow_id == escrow_id))
            trade = result.scalar_one_or_none()
            if not trade:
                await query.edit_message_text("❌ Trade not found")
                return ConversationHandler.END
            
            # Determine rating type based on user role
            buyer_id_value = getattr(trade, 'buyer_id', None)
            seller_id_value = getattr(trade, 'seller_id', None)
            is_buyer = (buyer_id_value is not None and db_user.id == buyer_id_value)
            is_seller = (seller_id_value is not None and db_user.id == seller_id_value)
            
            if not is_buyer and not is_seller:
                await query.edit_message_text("❌ You are not part of this trade")
                return ConversationHandler.END
            
            # Check if already rated
            category = 'seller' if is_buyer else 'buyer'
            result = await session.execute(
                select(Rating).where(
                    Rating.escrow_id == trade.id,
                    Rating.rater_id == db_user.id,
                    Rating.category == category
                )
            )
            existing_rating = result.scalar_one_or_none()
            
            if existing_rating:
                comment_text = existing_rating.comment if existing_rating.comment is not None else 'No comment'
                await query.edit_message_text(
                    f"✅ Already Rated\n\n"
                    f"You gave this {category} {existing_rating.rating}⭐ stars\n"
                    f"Comment: {comment_text}\n\n"
                    f"Rating submitted on {existing_rating.created_at.strftime('%Y-%m-%d')}"
                )
                return ConversationHandler.END
            
            # Get counterpart user
            if is_buyer:
                counterpart_id = seller_id_value
            else:
                counterpart_id = buyer_id_value
            
            counterpart = None
            if counterpart_id is not None:
                result = await session.execute(select(User).where(User.id == counterpart_id))
                counterpart = result.scalar_one_or_none()
            
            counterpart_name = category.title()
            if counterpart:
                if counterpart.username is not None:
                    counterpart_name = f"@{counterpart.username}"
                elif counterpart.first_name is not None:
                    counterpart_name = counterpart.first_name
            
            # Store IDs and dispute context in context for later use (avoid detached instances)
            context.user_data['rating_escrow_id'] = trade.id
            context.user_data['rating_escrow_string_id'] = trade.escrow_id
            context.user_data['rating_counterpart_id'] = counterpart.id if counterpart else None
            context.user_data['rating_type'] = category
            context.user_data['is_dispute_rating'] = True
            context.user_data['dispute_outcome'] = dispute_outcome
            context.user_data['dispute_resolution_type'] = resolution_type
            
            # Show rating selection interface with dispute context
            # Extract Python value from SQLAlchemy column for type safety
            amount_value = float(getattr(trade, 'amount', 0) or 0)
            if dispute_outcome == 'winner':
                message = f"⭐ Share Your Feedback\n\n"
                message += f"Trade #{trade.escrow_id[-6:]} - ${amount_value:.2f}\n"
                message += f"Dispute resolved in your favor\n\n"
                message += f"How was your experience with this {category}?"
            else:
                message = f"⭐ Optional Feedback\n\n"
                message += f"Trade #{trade.escrow_id[-6:]} - ${amount_value:.2f}\n"
                message += f"We understand this outcome may be disappointing\n\n"
                message += f"Your feedback helps us improve (completely optional)"
            
            keyboard = [
                [InlineKeyboardButton("⭐⭐⭐⭐⭐ Excellent (5)", callback_data="rating_5")],
                [InlineKeyboardButton("⭐⭐⭐⭐ Good (4)", callback_data="rating_4")],
                [InlineKeyboardButton("⭐⭐⭐ Average (3)", callback_data="rating_3")],
                [InlineKeyboardButton("⭐⭐ Poor (2)", callback_data="rating_2")],
                [InlineKeyboardButton("⭐ Very Poor (1)", callback_data="rating_1")],
                [InlineKeyboardButton("🚫 Cancel", callback_data="main_menu")]
            ]
            
            await safe_edit_message_text(
                query,
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            return RATING_SELECT
            
    except Exception as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Error in handle_rate_dispute: {e}")
            try:
                await query.edit_message_text("❌ Something went wrong. Please try again.")
            except:
                pass
        return ConversationHandler.END


async def handle_rate_buyer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle buyer rating callback"""
    query = update.callback_query
    user = update.effective_user
    
    if not query or not user:
        return ConversationHandler.END
    
    try:
        async with button_callback_wrapper(update, "⭐ Loading rating...") as session:
            # Extract escrow ID from callback data
            if not query.data:
                await query.edit_message_text("❌ Invalid request")
                return ConversationHandler.END
            escrow_id = query.data.split(':')[1]
            # Get user and trade
            result = await session.execute(select(User).where(User.telegram_id == int(user.id)))
            db_user = result.scalar_one_or_none()
            if not db_user:
                await query.edit_message_text("❌ User not found. Please restart with /start")
                return ConversationHandler.END
            
            # Get the completed trade
            result = await session.execute(select(Escrow).where(Escrow.escrow_id == escrow_id))
            trade = result.scalar_one_or_none()
            if not trade:
                await query.edit_message_text("❌ Trade not found")
                return ConversationHandler.END
            
            # SECURITY: Verify trade is COMPLETED or REFUNDED (allow ratings for dispute-resolved trades)
            from models import EscrowStatus
            # Handle both Enum and string comparisons for robustness
            trade_status = trade.status.value if hasattr(trade.status, 'value') else trade.status
            # Allow ratings for COMPLETED (normal) or REFUNDED (dispute resolved)
            allowed_statuses = [EscrowStatus.COMPLETED.value, EscrowStatus.REFUNDED.value]
            if trade_status not in allowed_statuses:
                await query.edit_message_text(
                    f"❌ Can only rate completed trades\n\n"
                    f"This trade is currently: {trade_status}"
                )
                return ConversationHandler.END
            
            # Verify user is seller (only sellers can rate buyers)
            seller_id_value = getattr(trade, 'seller_id', None)
            if seller_id_value is None or db_user.id != seller_id_value:
                await query.edit_message_text("❌ Only sellers can rate buyers")
                return ConversationHandler.END
            
            # Check if already rated
            result = await session.execute(
                select(Rating).where(
                    Rating.escrow_id == trade.id,
                    Rating.rater_id == db_user.id,
                    Rating.category == 'buyer'
                )
            )
            existing_rating = result.scalar_one_or_none()
            
            if existing_rating:
                comment_text = existing_rating.comment if existing_rating.comment is not None else 'No comment'
                await query.edit_message_text(
                    f"✅ Already Rated\n\n"
                    f"You gave this buyer {existing_rating.rating}⭐ stars\n"
                    f"Comment: {comment_text}\n\n"
                    f"Rating submitted on {existing_rating.created_at.strftime('%Y-%m-%d')}"
                )
                return ConversationHandler.END
            
            # Get buyer info
            result = await session.execute(select(User).where(User.id == trade.buyer_id))
            buyer = result.scalar_one_or_none()
            buyer_name = "Buyer"
            if buyer:
                if buyer.username is not None:
                    buyer_name = f"@{buyer.username}"
                elif buyer.first_name is not None:
                    buyer_name = buyer.first_name
            
            # Store IDs in context for later use (avoid detached instances)
            context.user_data['rating_escrow_id'] = trade.id
            context.user_data['rating_escrow_string_id'] = trade.escrow_id
            context.user_data['rating_buyer_id'] = buyer.id if buyer else None
            context.user_data['rating_type'] = 'buyer'
            
            # Show rating selection interface
            # Extract Python value from SQLAlchemy column for type safety
            amount_value = float(getattr(trade, 'amount', 0) or 0)
            message = f"⭐ Rate \n\n"
            message += f"Trade #{trade.escrow_id[-6:]} - ${amount_value:.2f}\n\n"
            message += "How was your experience with this buyer?"
            
            keyboard = [
                [InlineKeyboardButton("⭐⭐⭐⭐⭐ Excellent (5)", callback_data="rating_5")],
                [InlineKeyboardButton("⭐⭐⭐⭐ Good (4)", callback_data="rating_4")],
                [InlineKeyboardButton("⭐⭐⭐ Average (3)", callback_data="rating_3")],
                [InlineKeyboardButton("⭐⭐ Poor (2)", callback_data="rating_2")],
                [InlineKeyboardButton("⭐ Very Poor (1)", callback_data="rating_1")],
                [InlineKeyboardButton("🚫 Cancel", callback_data="main_menu")]
            ]
            
            await safe_edit_message_text(
                query,
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            return RATING_SELECT
            
    except Exception as e:
        # Handle "Message is not modified" error silently (Telegram API limitation)
        if "Message is not modified" not in str(e):
            logger.error(f"Error in handle_rate_buyer: {e}")
            try:
                await query.edit_message_text("❌ Something went wrong. Please try again.")
            except:
                pass
        return ConversationHandler.END


async def handle_rating_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle rating star selection"""
    query = update.callback_query
    user = update.effective_user
    
    if not query or not user:
        return ConversationHandler.END
    
    try:
        async with button_callback_wrapper(update, "💬 Adding comment...") as session:
            # Extract rating from callback
            if not query.data:
                await query.edit_message_text("❌ Invalid request")
                return ConversationHandler.END
            rating = int(query.data.split('_')[1])
            
            # Store rating in context
            context.user_data['rating_stars'] = rating
            
            # Get stored IDs from context
            escrow_string_id = context.user_data.get('rating_escrow_string_id') if context.user_data else None
            rating_type = context.user_data.get('rating_type', 'seller') if context.user_data else 'seller'
            
            if not escrow_string_id:
                await query.edit_message_text("❌ Session expired. Please try again.")
                return ConversationHandler.END
            
            if rating_type == 'buyer':
                callback_data = f"rate_buyer:{escrow_string_id}"
            else:
                callback_data = f"rate_seller:{escrow_string_id}"
            
            # Show comment input interface
            stars = "⭐" * rating
            message = f"⭐ Rating \n\n"
            message += f"You selected: {stars} ({rating}/5)\n\n"
            message += "💬 Add a comment (optional):\n"
            message += "Type your feedback or click Submit to finish"
            
            keyboard = [
                [InlineKeyboardButton("✅ Submit Rating", callback_data="rating_submit")],
                [InlineKeyboardButton("🔙 Change Rating", callback_data=callback_data)],
                [InlineKeyboardButton("🚫 Cancel", callback_data="main_menu")]
            ]
            
            await safe_edit_message_text(
                query,
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            return RATING_COMMENT
            
    except Exception as e:
        logger.error(f"Error in handle_rating_selection: {e}")
        try:
            await query.edit_message_text("❌ Something went wrong. Please try again.")
        except:
            pass
        return ConversationHandler.END


async def handle_rating_submit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Submit the rating to database"""
    query = update.callback_query
    user = update.effective_user
    
    if not query or not user:
        return ConversationHandler.END
    
    try:
        async with button_callback_wrapper(update, "✅ Submitting rating...") as session:
            # Get stored IDs from context
            escrow_id = context.user_data.get('rating_escrow_id') if context.user_data else None
            escrow_string_id = context.user_data.get('rating_escrow_string_id') if context.user_data else None
            rating_stars = context.user_data.get('rating_stars') if context.user_data else None
            comment = context.user_data.get('rating_comment', '') if context.user_data else ''
            rating_type = context.user_data.get('rating_type', 'seller') if context.user_data else 'seller'
            
            # Get dispute context if this is a dispute rating
            is_dispute_rating = context.user_data.get('is_dispute_rating', False) if context.user_data else False
            dispute_outcome = context.user_data.get('dispute_outcome') if context.user_data else None
            dispute_resolution_type = context.user_data.get('dispute_resolution_type') if context.user_data else None
            
            # Validate we have the required IDs
            if not escrow_id or not rating_stars:
                await query.edit_message_text("❌ Session expired. Please try again.")
                return ConversationHandler.END
            
            # Reload escrow/trade from database using stored ID
            result = await session.execute(select(Escrow).where(Escrow.id == escrow_id))
            trade = result.scalar_one_or_none()
            if not trade:
                await query.edit_message_text("❌ Trade not found.")
                return ConversationHandler.END
            
            # Get current user
            result = await session.execute(select(User).where(User.telegram_id == int(user.id)))
            db_user = result.scalar_one_or_none()
            if not db_user:
                await query.edit_message_text("❌ User not found.")
                return ConversationHandler.END
            
            # Reload the rated user from database based on rating type
            if rating_type == 'buyer':
                buyer_id = context.user_data.get('rating_buyer_id') if context.user_data else None
                counterpart_id = context.user_data.get('rating_counterpart_id') if context.user_data else None
                rated_user_id = buyer_id or counterpart_id
                
                if not rated_user_id:
                    await query.edit_message_text("❌ Session data missing.")
                    return ConversationHandler.END
                
                result = await session.execute(select(User).where(User.id == rated_user_id))
                rated_user = result.scalar_one_or_none()
                if not rated_user:
                    await query.edit_message_text("❌ Rated user not found.")
                    return ConversationHandler.END
                category = 'buyer'
            else:
                seller_id = context.user_data.get('rating_seller_id') if context.user_data else None
                counterpart_id = context.user_data.get('rating_counterpart_id') if context.user_data else None
                rated_user_id = seller_id or counterpart_id
                
                if not rated_user_id:
                    await query.edit_message_text("❌ Session data missing.")
                    return ConversationHandler.END
                
                result = await session.execute(select(User).where(User.id == rated_user_id))
                rated_user = result.scalar_one_or_none()
                if not rated_user:
                    await query.edit_message_text("❌ Rated user not found.")
                    return ConversationHandler.END
                category = 'seller'
                
            # Create rating and update stats in same transaction
            try:
                rating = Rating(
                    escrow_id=trade.id,
                    rater_id=db_user.id,
                    rated_id=rated_user.id,
                    rating=rating_stars,
                    comment=comment,
                    category=category,
                    is_dispute_rating=is_dispute_rating,
                    dispute_outcome=dispute_outcome,
                    dispute_resolution_type=dispute_resolution_type,
                    created_at=datetime.utcnow()
                )
                
                session.add(rating)
                await session.flush()
                
                # Update user stats for the rated user (in same transaction)
                from services.user_stats_service import UserStatsService
                if rated_user and rated_user.id:
                    await UserStatsService.update_user_stats(rated_user.id, session)
                    logger.info(f"✅ Updated stats for rated user {rated_user.id}")
                
                # Commit both rating and stats update atomically
                await session.commit()
                
                # Send Telegram group notification for rating submitted
                try:
                    rater_info = f"@{db_user.username}" if db_user.username else db_user.first_name
                    rated_info = f"@{rated_user.username}" if rated_user and rated_user.username else (rated_user.first_name if rated_user else "Unknown")
                    rating_data = {
                        'escrow_id': trade.escrow_id,
                        'rating': rating_stars,
                        'type': category,
                        'comment': comment or 'No comment',
                        'rater_info': rater_info,
                        'rated_user': rated_info,
                        'submitted_at': datetime.utcnow()
                    }
                    import asyncio
                    admin_notif_service = AdminTradeNotificationService()
                    asyncio.create_task(admin_notif_service.send_group_notification_rating_submitted(rating_data))
                    # Broadcast to all registered groups
                    from services.group_event_service import group_event_service
                    asyncio.create_task(group_event_service.broadcast_rating_submitted(rating_data))
                    logger.info(f"📤 Queued group notification for rating submitted: {trade.escrow_id}")
                except Exception as notif_err:
                    logger.error(f"❌ Failed to queue rating submitted group notification: {notif_err}")
                
            except Exception as e:
                await session.rollback()
                logger.error(f"❌ Failed to save rating and update stats: {e}")
                await query.edit_message_text("❌ Failed to submit rating. Please try again.")
                return ConversationHandler.END
            
            # Send notifications about the rating  
            try:
                # Import EmailService at the start of notification block
                from services.email import EmailService
                
                # Get rater and rated user names
                rater_name = "Someone"
                if db_user.username is not None:
                    rater_name = f"@{db_user.username}"
                elif db_user.first_name is not None:
                    rater_name = db_user.first_name
                
                rated_name = "you"
                if rated_user and rated_user.username is not None:
                    rated_name = f"@{rated_user.username}"
                elif rated_user and rated_user.first_name is not None:
                    rated_name = rated_user.first_name
                
                stars_text = "⭐" * (rating_stars or 0)
                
                # Notification to the person who was rated
                if rated_user and rated_user.telegram_id:
                    rating_notification = f"🌟 New Rating Received\n\n"
                    rating_notification += f"{rater_name} rated you {stars_text} ({rating_stars}/5)\n"
                    if comment:
                        rating_notification += f"💬 \"{comment}\"\n"
                    rating_notification += f"\nTrade: #{trade.escrow_id[-6:]}"
                    
                    # Send Telegram notification to rated user
                    try:
                        await query.get_bot().send_message(
                            chat_id=rated_user.telegram_id,
                            text=rating_notification
                        )
                        logger.info(f"✅ Rating notification sent to rated user {rated_user.id}")
                    except Exception as e:
                        logger.error(f"❌ Failed to send rating notification: {e}")
                
                # Email notification to rated user if they have email
                if rated_user and rated_user.email and getattr(rated_user, 'email_verified', True):
                    try:
                        email_service = EmailService()
                        
                        subject = f"🌟 New {rating_stars}-Star Rating Received"
                        html_body = f"""
                        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                            <h2 style="color: #4CAF50;">🌟 New Rating Received</h2>
                            <p><strong>{rater_name}</strong> rated you <strong>{stars_text} ({rating_stars}/5)</strong></p>
                            {f'<p style="font-style: italic; background: #f5f5f5; padding: 10px; border-radius: 5px;">"{comment}"</p>' if comment else ''}
                            <p><strong>Trade:</strong> #{trade.escrow_id[-6:]}</p>
                            <hr>
                            <p style="color: #666;">Thank you for using our platform!</p>
                        </div>
                        """
                        
                        email_sent = email_service.send_email(
                            to_email=rated_user.email,
                            subject=subject,
                            text_content=f"New {rating_stars}-star rating from {rater_name}",
                            html_content=html_body
                        )
                        
                        if email_sent:
                            logger.info(f"✅ Rating email notification sent to {rated_user.email}")
                        else:
                            logger.error(f"❌ Failed to send rating email to {rated_user.email} - email service returned False")
                            logger.error(f"   🔧 Check BREVO_API_KEY configuration")
                    except Exception as e:
                        logger.error(f"❌ Failed to send rating email notification: {e}")
                        
            except Exception as e:
                logger.error(f"❌ Rating notification error: {e}")
            
            # Check if trade rating already exists
            result = await session.execute(
                select(Rating).where(
                    Rating.escrow_id == trade.id,
                    Rating.rater_id == db_user.id,
                    Rating.category == 'trade'
                )
            )
            existing_trade_rating = result.scalar_one_or_none()
            
            if existing_trade_rating:
                # Trade already rated, show completion message
                clear_rating_context(context)
                
                user_name = f"{category.title()}"
                if rated_user and rated_user.username is not None:
                    user_name = f"@{rated_user.username}"
                elif rated_user and rated_user.first_name is not None:
                    user_name = rated_user.first_name
                
                stars = "⭐" * (rating_stars or 0)
                message = f"✅ Rating \n\n"
                message += f"You rated {user_name}: {stars} ({rating_stars}/5)\n"
                if comment:
                    message += f"Comment: {comment}\n"
                message += f"\nThank you for your feedback!"
                
                keyboard = [
                    [InlineKeyboardButton("📋 My Trades", callback_data="trades_messages_hub")],
                    [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
                ]
                
                await safe_edit_message_text(
                    query,
                    message,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
                return ConversationHandler.END
            else:
                # No trade rating yet, show trade rating prompt
                stars = "⭐" * (rating_stars or 0)
                message = f"✅ {category.title()} Rated!\n\n"
                message += f"You gave {stars} ({rating_stars}/5)\n\n"
                message += f"Now, how was your overall trade experience with #{trade.escrow_id[:12]}?\n"
                
                keyboard = [
                    [InlineKeyboardButton("⭐⭐⭐⭐⭐ Excellent (5)", callback_data="rating_trade_5")],
                    [InlineKeyboardButton("⭐⭐⭐⭐ Good (4)", callback_data="rating_trade_4")],
                    [InlineKeyboardButton("⭐⭐⭐ Average (3)", callback_data="rating_trade_3")],
                    [InlineKeyboardButton("⭐⭐ Poor (2)", callback_data="rating_trade_2")],
                    [InlineKeyboardButton("⭐ Very Poor (1)", callback_data="rating_trade_1")],
                    [InlineKeyboardButton("Skip Trade Rating", callback_data="skip_trade_rating")]
                ]
                
                await safe_edit_message_text(
                    query,
                    message,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
                # Keep trade in context for trade rating
                logger.info(f"✅ {category.title()} rating saved, showing trade rating prompt for escrow {trade.escrow_id}")
                return RATING_SELECT
            
    except Exception as e:
        logger.error(f"Error in handle_rating_submit: {e}")
        try:
            await query.edit_message_text("❌ Failed to submit rating. Please try again.")
        except:
            pass
        return ConversationHandler.END


async def handle_trade_rating_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle trade experience rating selection"""
    query = update.callback_query
    user = update.effective_user
    
    if not query or not user:
        return ConversationHandler.END
    
    try:
        async with button_callback_wrapper(update, "✅ Saving trade rating...") as session:
            # Extract rating from callback data (rating_trade_5 -> 5)
            if not query.data:
                await query.edit_message_text("❌ Invalid request")
                return ConversationHandler.END
            rating_value = int(query.data.split('_')[2])
            
            # Get stored IDs from context
            escrow_id = context.user_data.get('rating_escrow_id') if context.user_data else None
            escrow_string_id = context.user_data.get('rating_escrow_string_id') if context.user_data else None
            
            if not escrow_id:
                await query.edit_message_text("❌ Session expired. Please try again.")
                return ConversationHandler.END
            # Reload escrow/trade from database using stored ID
            result = await session.execute(select(Escrow).where(Escrow.id == escrow_id))
            trade = result.scalar_one_or_none()
            if not trade:
                await query.edit_message_text("❌ Trade not found.")
                return ConversationHandler.END
            
            # Get current user
            result = await session.execute(select(User).where(User.telegram_id == int(user.id)))
            db_user = result.scalar_one_or_none()
            if not db_user:
                await query.edit_message_text("❌ User not found.")
                return ConversationHandler.END
            
            # Create trade rating with category='trade' and rated_id=NULL
            trade_rating = Rating(
                escrow_id=trade.id,
                rater_id=db_user.id,
                rated_id=None,
                rating=rating_value,
                comment=None,
                category='trade',
                created_at=datetime.utcnow()
            )
            
            session.add(trade_rating)
            await session.commit()
            
            logger.info(f"✅ Trade rating saved: escrow={escrow_string_id}, user={db_user.id}, rating={rating_value}")
            
            # Clear context
            clear_rating_context(context)
            
            # Show completion message
            stars = "⭐" * rating_value
            message = f"✅ All Ratings Complete!\n\n"
            message += f"Trade Experience: {stars} ({rating_value}/5)\n\n"
            if escrow_string_id:
                message += f"Thank you for rating your trade experience with #{escrow_string_id[:12]}!\n"
            else:
                message += "Thank you for rating your trade experience!\n"
            message += f"Your feedback helps improve our platform."
            
            keyboard = [
                [InlineKeyboardButton("📋 My Trades", callback_data="trades_messages_hub")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]
            
            await safe_edit_message_text(
                query,
                message,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            return ConversationHandler.END
            
    except Exception as e:
        logger.error(f"Error in handle_trade_rating_selection: {e}")
        try:
            await query.edit_message_text("❌ Failed to submit trade rating. Please try again.")
        except:
            pass
        return ConversationHandler.END


async def handle_skip_trade_rating(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle skipping trade rating"""
    query = update.callback_query
    user = update.effective_user
    
    if not query or not user:
        return ConversationHandler.END
    
    try:
        async with button_callback_wrapper(update, "👌 Skipping...") as session:
            # Get stored escrow_string_id from context
            escrow_string_id = context.user_data.get('rating_escrow_string_id') if context.user_data else None
            
            # Clear context
            clear_rating_context(context)
            
            # Show completion message
            message = "✅ Rating Complete!\n\n"
            message += "You've skipped the trade experience rating.\n"
            message += "Thank you for your feedback on this trade!"
            
            keyboard = [
                [InlineKeyboardButton("📋 My Trades", callback_data="trades_messages_hub")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]
            
            # CRITICAL FIX: Delete and resend instead of edit to avoid Telegram rate limiting
            # When skipping after multiple rapid rating edits, Telegram silently ignores the edit
            try:
                # Delete the old message
                await query.message.delete()  # type: ignore
                # Send new message with completion
                await query.message.chat.send_message(  # type: ignore
                    message,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                if escrow_string_id:
                    logger.info(f"✅ User skipped trade rating for escrow {escrow_string_id} (delete+send)")
            except Exception as delete_error:
                logger.warning(f"Failed to delete/resend skip message, falling back to edit: {delete_error}")
                # Fallback to edit if delete fails
                await safe_edit_message_text(
                    query,
                    message,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                if escrow_string_id:
                    logger.info(f"✅ User skipped trade rating for escrow {escrow_string_id}")
            
            return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Error in handle_skip_trade_rating: {e}")
        try:
            await query.edit_message_text("❌ Something went wrong. Returning to main menu.")
        except:
            pass
        return ConversationHandler.END


async def handle_rating_comment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle text input for rating comment"""
    message = update.message
    user = update.effective_user
    
    if not message or not message.text or not user:
        return RATING_COMMENT
    
    # Store comment
    context.user_data['rating_comment'] = message.text[:500]  # Limit comment length
    
    # Get stored IDs from context
    escrow_string_id = context.user_data.get('rating_escrow_string_id') if context.user_data else None
    rating_stars = context.user_data.get('rating_stars') if context.user_data else None
    rating_type = context.user_data.get('rating_type', 'seller') if context.user_data else 'seller'
    
    if not escrow_string_id or not rating_stars:
        await message.reply_text("❌ Session expired. Please try again.")
        return ConversationHandler.END
    
    if rating_type == 'buyer':
        callback_data = f"rate_buyer:{escrow_string_id}"
    else:
        callback_data = f"rate_seller:{escrow_string_id}"
    
    stars = "⭐" * (rating_stars or 0)
    reply_message = f"⭐ Rating \n\n"
    reply_message += f"Rating: {stars} ({rating_stars}/5)\n"
    reply_message += f"Comment: {message.text[:100]}{'...' if len(message.text) > 100 else ''}\n\n"
    reply_message += "Ready to submit your rating?"
    
    keyboard = [
        [InlineKeyboardButton("✅ Submit Rating", callback_data="rating_submit")],
        [InlineKeyboardButton("🔙 Change Rating", callback_data=callback_data)],
        [InlineKeyboardButton("🚫 Cancel", callback_data="main_menu")]
    ]
    
    await message.reply_text(
        reply_message,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    return RATING_COMMENT


def clear_rating_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear all rating-related context data to prevent stale state leakage"""
    if not context.user_data:
        return
    
    rating_keys = [
        'rating_escrow_id',
        'rating_escrow_string_id',
        'rating_seller_id',
        'rating_buyer_id',
        'rating_counterpart_id',
        'rating_type',
        'rating_stars',
        'rating_comment',
        'is_dispute_rating',
        'dispute_outcome',
        'dispute_resolution_type'
    ]
    for key in rating_keys:
        context.user_data.pop(key, None)


async def _handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle main menu callback"""
    clear_rating_context(context)
    return ConversationHandler.END


# Create the conversation handler
def create_rating_conversation_handler():
    """Create and return the rating conversation handler"""
    from telegram.ext import CallbackQueryHandler, MessageHandler, filters
    
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_rate_seller, pattern="^rate_seller:"),
            CallbackQueryHandler(handle_rate_buyer, pattern="^rate_buyer:"),
            CallbackQueryHandler(handle_rate_dispute, pattern="^rate_dispute:")
        ],
        states={
            RATING_SELECT: [
                CallbackQueryHandler(handle_rating_selection, pattern="^rating_[1-5]$"),
                CallbackQueryHandler(handle_trade_rating_selection, pattern="^rating_trade_[1-5]$"),
                CallbackQueryHandler(handle_skip_trade_rating, pattern="^skip_trade_rating$")
            ],
            RATING_COMMENT: [
                CallbackQueryHandler(handle_rating_submit, pattern="^rating_submit$"),
                CallbackQueryHandler(handle_rate_seller, pattern="^rate_seller:"),
                CallbackQueryHandler(handle_rate_buyer, pattern="^rate_buyer:"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_rating_comment)
            ]
        },
        fallbacks=[
            CallbackQueryHandler(_handle_main_menu, pattern="^main_menu$")
        ],
        name="user_rating",
        per_message=False,
        per_chat=True,
        per_user=True,
        # TIMEOUT: Auto-cleanup abandoned rating sessions after 5 minutes
        conversation_timeout=300  # 5 minutes for quick rating flows
    )
