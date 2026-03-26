"""
Direct handlers for user rating system - replaces ConversationHandler
Maintains exact same UI while fixing conversation routing conflicts
"""

import logging
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters, ApplicationHandlerStop
from models import User, Rating, Escrow
from database import SyncSessionLocal
from utils.callback_utils import safe_answer_callback_query
from utils.conversation_state_helper import set_conversation_state_db_sync

logger = logging.getLogger(__name__)

# State management functions
async def set_user_rating_state(user_id: int, state: str, data: Optional[dict] = None, context: Optional[ContextTypes.DEFAULT_TYPE] = None):
    """Set user rating conversation state in database"""
    session = SyncSessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == int(user_id)).first()
        if user:
            # CRITICAL FIX: Fully clear state if empty, don't set "rating_" prefix
            if not state or state == "":
                set_conversation_state_db_sync(user, "", context)
                logger.debug(f"Cleared rating state for user {user_id}")
            else:
                state_data = {"flow": "rating", "step": state}
                if data:
                    state_data.update(data)
                set_conversation_state_db_sync(user, f"rating_{state}", context)
                logger.debug(f"Set user {user_id} rating state to: {state}")
            session.commit()
    except Exception as e:
        logger.error(f"Error setting user rating state: {e}")
    finally:
        session.close()

async def get_user_rating_state(user_id: int) -> str:
    """Get user rating conversation state from database"""
    session = SyncSessionLocal()
    try:
        user = session.query(User).filter(User.telegram_id == int(user_id)).first()
        if user and user.conversation_state and user.conversation_state.startswith("rating_"):
            return user.conversation_state.replace("rating_", "")
        return ""
    except Exception as e:
        logger.error(f"Error getting user rating state: {e}")
        return ""
    finally:
        session.close()

async def clear_user_rating_state(user_id: int, context: Optional[ContextTypes.DEFAULT_TYPE] = None):
    """Clear user rating conversation state"""
    await set_user_rating_state(user_id, "", {}, context)

# Direct handler implementations
async def direct_start_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for starting rating process"""
    query = update.callback_query
    if not query:
        return
    
    await safe_answer_callback_query(query, "⭐ Rating")
    
    # Extract escrow_id from callback data (format: rate_escrow_123)
    escrow_id_str = None
    if query.data and "_" in query.data:
        escrow_id_str = query.data.split("_")[-1]
    
    if not escrow_id_str:
        logger.error("No escrow ID provided for rating")
        await query.edit_message_text("❌ Error: No trade ID found. Please try again.")
        return
    
    try:
        escrow_id = int(escrow_id_str)
    except ValueError:
        logger.error(f"Invalid escrow ID format: {escrow_id_str}")
        await query.edit_message_text("❌ Error: Invalid trade ID. Please try again.")
        return
    
    # Get escrow and participants from database
    from models import User
    from sqlalchemy import select
    from database import async_managed_session
    
    if not update.effective_user:
        return
    
    async with async_managed_session() as session:
        # Get escrow
        escrow_stmt = select(Escrow).where(Escrow.id == escrow_id)
        escrow_result = await session.execute(escrow_stmt)
        escrow = escrow_result.scalar_one_or_none()
        
        if not escrow:
            await query.edit_message_text("❌ This trade cannot be found.")
            return
        
        # Check status - use getattr to get the actual instance value
        escrow_status = getattr(escrow, 'status', None)
        if escrow_status != "completed":
            await query.edit_message_text("❌ This trade cannot be rated (must be completed).")
            return
        
        # Determine who is being rated - use getattr to get actual instance values
        user_id = update.effective_user.id
        buyer_id_val = getattr(escrow, 'buyer_id', None)
        seller_id_val = getattr(escrow, 'seller_id', None)
        buyer_id = int(buyer_id_val) if buyer_id_val is not None else None
        seller_id = int(seller_id_val) if seller_id_val is not None else None
        
        if buyer_id == user_id:
            # Buyer is rating seller
            rated_user_id = seller_id
            role = "seller"
        elif seller_id == user_id:
            # Seller is rating buyer
            rated_user_id = buyer_id
            role = "buyer"
        else:
            await query.edit_message_text("❌ You are not a participant in this trade.")
            return
        
        # Get rated user info
        if rated_user_id is not None:
            rated_user_stmt = select(User).where(User.id == rated_user_id)
            rated_user_result = await session.execute(rated_user_stmt)
            rated_user = rated_user_result.scalar_one_or_none()
            rated_username = f"@{rated_user.username}" if rated_user and rated_user.username else "user"
        else:
            rated_username = "user"
    
    # Store rating context in user_data
    if context.user_data is not None:
        context.user_data['rating_escrow_id'] = escrow_id
        context.user_data['rating_role'] = role
        context.user_data['rated_user_id'] = rated_user_id
    
    # Set state with escrow context
    await set_user_rating_state(update.effective_user.id, "select", {"escrow_id": escrow_id}, context)
    
    # Show rating selection UI (use plain text to avoid Markdown parsing issues)
    message = f"⭐ Rate {rated_username}\n\n"
    message += "How would you rate this trading experience?\n"
    message += "Select a rating below:"
    
    keyboard = [
        [
            InlineKeyboardButton("⭐⭐⭐⭐⭐ (5)", callback_data="rating_5"),
            InlineKeyboardButton("⭐⭐⭐⭐ (4)", callback_data="rating_4")
        ],
        [
            InlineKeyboardButton("⭐⭐⭐ (3)", callback_data="rating_3"),
            InlineKeyboardButton("⭐⭐ (2)", callback_data="rating_2")
        ],
        [
            InlineKeyboardButton("⭐ (1)", callback_data="rating_1")
        ],
        [InlineKeyboardButton("🚫 Cancel", callback_data="main_menu")]
    ]
    
    await query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def direct_handle_rating_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for rating selection - self-contained without ConversationHandler dependencies"""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    
    if not query.data:
        return
    
    logger.info(f"✅ DIRECT HANDLER: Processing rating selection for user {update.effective_user.id} - callback: {query.data}")
    
    # CRITICAL FIX: Handle specific rating patterns before trying to parse as integer
    if query.data == "rating_discovery":
        # Rating system discovery menu - route to discovery handler
        from handlers.rating_ui_enhancements import handle_rating_discovery_menu
        await handle_rating_discovery_menu(update, context)
        raise ApplicationHandlerStop
    elif query.data == "rating_guidelines":
        # Rating guidelines page - route to guidelines handler
        from handlers.rating_ui_enhancements import handle_rating_guidelines
        await handle_rating_guidelines(update, context)
        raise ApplicationHandlerStop
    elif query.data == "rating_submit":
        # User clicked submit - route to direct submit handler
        await direct_handle_rating_submit(update, context)
        raise ApplicationHandlerStop
    elif query.data.startswith("rating_trade_"):
        # Trade rating (overall experience) - route to trade rating handler
        from handlers.user_rating import handle_trade_rating_selection
        await handle_trade_rating_selection(update, context)
        raise ApplicationHandlerStop
    elif query.data.startswith("rating_"):
        parts = query.data.split('_')
        if len(parts) > 1 and parts[1].isdigit():
            # Numeric rating (1-5 stars)
            try:
                # Extract rating number
                rating = int(parts[1])
                logger.info(f"🌟 DIRECT RATING: User {update.effective_user.id} selected {rating} stars")
                
                # Store rating in context
                if context.user_data is not None:
                    context.user_data['rating_stars'] = rating
                
                # CRITICAL FIX: Set state to "comment" BEFORE showing UI
                # This ensures the next text message routes to rating handler
                await set_user_rating_state(update.effective_user.id, "comment", None, context)
                logger.info(f"✅ STATE SAVED: User {update.effective_user.id} state set to 'comment'")
            
                # Show comment input interface (self-contained, no ConversationHandler dependency)
                await safe_answer_callback_query(query, "💬")
                
                stars = "⭐" * rating
                message = "⭐ Rating \n\n"
                message += f"You selected: {stars} ({rating}/5)\n\n"
                message += "💬 Add a comment (optional):\n"
                message += "Type your feedback or click Submit to finish"
                
                keyboard = [
                    [InlineKeyboardButton("✅ Submit Rating", callback_data="rating_submit")],
                    [InlineKeyboardButton("🚫 Cancel", callback_data="main_menu")]
                ]
                
                await query.edit_message_text(
                    message,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
            except Exception as e:
                logger.error(f"❌ DIRECT RATING ERROR: {e}")
                await safe_answer_callback_query(query, "⭐ Please try rating again")

async def direct_handle_rating_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for rating comment input - self-contained implementation"""
    if not update.message or not update.message.text or not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    # Check state (get_user_rating_state returns a string like "comment" or "select")
    rating_state = await get_user_rating_state(user_id)
    if not rating_state or rating_state != "comment":
        return  # Not in the right state
    
    logger.info(f"✅ DIRECT HANDLER: Processing rating comment for user {user_id}")
    
    # Get rating context from user_data
    if context.user_data is None or 'rating_stars' not in context.user_data:
        await update.message.reply_text("❌ Session expired. Please try again.")
        await clear_user_rating_state(user_id, context)
        raise ApplicationHandlerStop
    
    # Store comment
    comment = update.message.text[:500]  # Limit length
    context.user_data['rating_comment'] = comment
    rating_stars = context.user_data.get('rating_stars', 5)
    
    # Show confirmation with submit button
    stars = "⭐" * (rating_stars if rating_stars else 5)
    message = "⭐ Rating \n\n"
    message += f"Rating: {stars} ({rating_stars}/5)\n"
    message += f"Comment: {comment[:100]}{'...' if len(comment) > 100 else ''}\n\n"
    message += "Ready to submit your rating?"
    
    keyboard = [
        [InlineKeyboardButton("✅ Submit Rating", callback_data="rating_submit")],
        [InlineKeyboardButton("🚫 Cancel", callback_data="main_menu")]
    ]
    
    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    
    # CRITICAL FIX: Stop update propagation to prevent unified text router from processing
    raise ApplicationHandlerStop

async def direct_handle_rating_submit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for submitting rating - self-contained implementation"""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    
    await safe_answer_callback_query(query, "💾 Saving...")
    
    user_id = update.effective_user.id
    
    # Get rating data from context
    if context.user_data is None:
        await query.edit_message_text("❌ Session expired. Please try again.")
        return
    
    escrow_id = context.user_data.get('rating_escrow_id')
    rating_stars = context.user_data.get('rating_stars')
    rating_comment = context.user_data.get('rating_comment', '')
    role = context.user_data.get('rating_role')  # "buyer" or "seller"
    rated_user_id = context.user_data.get('rated_user_id')
    
    if not all([escrow_id, rating_stars, role, rated_user_id]):
        await query.edit_message_text("❌ Session data missing. Please try rating again.")
        await clear_user_rating_state(user_id, context)
        return
    
    # Save rating to database
    from models import User
    from sqlalchemy import select
    from database import async_managed_session
    
    try:
        async with async_managed_session() as session:
            # Get rater user
            rater_stmt = select(User).where(User.telegram_id == user_id)
            rater_result = await session.execute(rater_stmt)
            rater = rater_result.scalar_one_or_none()
            
            if not rater:
                await query.edit_message_text("❌ User not found.")
                await clear_user_rating_state(user_id, context)
                return
            
            # Get rated user
            rated_user_stmt = select(User).where(User.id == rated_user_id)
            rated_user_result = await session.execute(rated_user_stmt)
            rated_user = rated_user_result.scalar_one_or_none()
            
            if not rated_user:
                await query.edit_message_text("❌ Rated user not found.")
                await clear_user_rating_state(user_id, context)
                return
            
            # Create rating
            new_rating = Rating(
                escrow_id=escrow_id,
                rater_id=rater.id,
                rated_id=rated_user_id,  # Field is 'rated_id' in the Rating model
                rating=rating_stars,
                comment=rating_comment,
                category=role  # "buyer" or "seller"
            )
            session.add(new_rating)
            
            # Reputation is calculated dynamically from ratings, no need to update separately
            await session.commit()
            
            # Get escrow data for notifications
            escrow_stmt = select(Escrow).where(Escrow.id == escrow_id)
            escrow_result = await session.execute(escrow_stmt)
            escrow = escrow_result.scalar_one_or_none()
            
            # Send notifications to rated user
            try:
                # Get rater name for notification
                rater_name = "Someone"
                if rater.username:
                    rater_name = f"@{rater.username}"
                elif rater.first_name:
                    rater_name = rater.first_name
                
                # Get rated user name for logs
                rated_name = "user"
                if rated_user.username:
                    rated_name = f"@{rated_user.username}"
                elif rated_user.first_name:
                    rated_name = rated_user.first_name
                
                stars_text = "⭐" * (rating_stars if rating_stars else 5)
                
                # Send Telegram notification to rated user
                if rated_user.telegram_id:
                    try:
                        notification = "🌟 <b>New Rating Received</b>\n\n"
                        notification += f"{rater_name} rated you {stars_text} ({rating_stars}/5)\n"
                        if rating_comment:
                            notification += f"💬 \"{rating_comment}\"\n"
                        if escrow:
                            notification += f"\nTrade: #{escrow.escrow_id[-6:]}"
                        
                        await context.bot.send_message(
                            chat_id=rated_user.telegram_id,
                            text=notification,
                            parse_mode='HTML'
                        )
                        logger.info(f"✅ Rating notification sent to {rated_name} (telegram_id: {rated_user.telegram_id})")
                    except Exception as e:
                        logger.error(f"❌ Failed to send Telegram rating notification to {rated_name}: {e}")
                
                # Send email notification to rated user if email verified
                if rated_user.email and getattr(rated_user, 'email_verified', True):
                    try:
                        from services.email import EmailService
                        email_service = EmailService()
                        
                        subject = f"🌟 New {rating_stars}-Star Rating Received"
                        html_body = f"""
                        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                            <h2 style="color: #4CAF50;">🌟 New Rating Received</h2>
                            <p><strong>{rater_name}</strong> rated you <strong>{stars_text} ({rating_stars}/5)</strong></p>
                            {f'<p style="font-style: italic; background: #f5f5f5; padding: 10px; border-radius: 5px;">"{rating_comment}"</p>' if rating_comment else ''}
                            {f'<p><strong>Trade:</strong> #{escrow.escrow_id[-6:]}</p>' if escrow else ''}
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
                            logger.error("   🔧 Check BREVO_API_KEY configuration")
                    except Exception as e:
                        logger.error(f"❌ Failed to send rating email notification to {rated_name}: {e}")
                        
            except Exception as e:
                logger.error(f"❌ Rating notification error: {e}")
            
            # Show success message
            stars = "⭐" * (rating_stars if rating_stars else 5)
            success_message = "✅ <b>Rating Submitted!</b>\n\n"
            success_message += f"Rating: {stars} ({rating_stars}/5)\n"
            if rating_comment:
                success_message += f"Comment: {rating_comment[:100]}{'...' if len(rating_comment) > 100 else ''}\n"
            success_message += "\nThank you for your feedback!"
            
            keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]
            
            await query.edit_message_text(
                success_message,
                parse_mode='HTML',
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            logger.info(f"✅ Rating submitted: User {user_id} rated {rated_user_id} with {rating_stars} stars")
            
    except Exception as e:
        logger.error(f"Error submitting rating: {e}", exc_info=True)
        await query.edit_message_text(
            "⚠️ <b>Rating Error</b>\n\n"
            "There was an issue processing your rating feedback.\n"
            "Please try again or contact support if the problem persists.",
            parse_mode='HTML'
        )
    finally:
        # Clear state
        await clear_user_rating_state(user_id, context)

async def direct_handle_skip_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct handler for skipping rating comment"""
    query = update.callback_query
    if not query or not update.effective_user:
        return
    
    await safe_answer_callback_query(query, "✅ Rating submitted")
    
    # Clear state
    await clear_user_rating_state(update.effective_user.id, context)
    
    # Submit rating without comment (redirect to submit handler)
    await direct_handle_rating_submit(update, context)

# Router for rating-related text input
async def rating_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route rating text messages to correct handler based on state"""
    if not update.message or not update.message.text or not update.effective_user:
        return
    
    user_id = update.effective_user.id
    state = await get_user_rating_state(user_id)
    
    logger.debug(f"🔀 RATING ROUTER: User {user_id} in state '{state}', message: {update.message.text}")
    
    if state == "comment":
        # This will raise ApplicationHandlerStop to prevent propagation to group 0
        await direct_handle_rating_comment(update, context)

# Import the specific rating handlers from user_rating.py
from handlers.user_rating import handle_rate_seller, handle_rate_buyer

# Direct handlers list for registration
DIRECT_RATING_HANDLERS = [
    # CRITICAL FIX: Add missing rate_seller and rate_buyer handlers
    CallbackQueryHandler(handle_rate_seller, pattern="^rate_seller:"),
    CallbackQueryHandler(handle_rate_buyer, pattern="^rate_buyer:"),
    
    # Start rating process
    CallbackQueryHandler(direct_start_rating, pattern="^rate_escrow_.*$"),
    CallbackQueryHandler(direct_start_rating, pattern="^rating_menu.*$"),
    
    # Rating selection
    CallbackQueryHandler(direct_handle_rating_selection, pattern="^rate_[1-5].*$"),
    CallbackQueryHandler(direct_handle_rating_selection, pattern="^rating_.*$"),
    
    # Skip comment
    CallbackQueryHandler(direct_handle_skip_comment, pattern="^skip_rating_comment$"),
    CallbackQueryHandler(direct_handle_skip_comment, pattern="^rating_skip.*$"),
    
    # Text input for comments
    MessageHandler(filters.TEXT & ~filters.COMMAND, rating_text_router),
]