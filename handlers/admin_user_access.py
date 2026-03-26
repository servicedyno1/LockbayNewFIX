"""
Admin handlers for managing user access control
"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.admin_security import admin_required
from utils.user_access_control import access_controller
from utils.callback_utils import safe_answer_callback_query
from database import SessionLocal
from models import User

logger = logging.getLogger(__name__)


class AdminUserAccessHandler:
    """Admin handlers for user access control management"""
    
    @staticmethod
    @admin_required
    async def check_user_permissions(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Admin command to check specific user's permissions"""
        query = update.callback_query
        message = update.message
        
        # Handle callback query format: admin_check_permissions:TELEGRAM_ID
        if query and query.data and query.data.startswith("admin_check_permissions:"):
            telegram_id = query.data.split(":")[-1]
            await safe_answer_callback_query(query)
        # Handle direct command with argument
        elif message and context.args and len(context.args) > 0:
            telegram_id = context.args[0]
        # Handle interactive mode
        else:
            instructions_text = """🔍 **Check User Permissions**

To check a user's access permissions, use:
`/admin_permissions <telegram_id>`

Or for @onarrival1 specifically:
`/admin_permissions 5590563715`

This will show:
• User access level (admin/unrestricted/standard)
• Feature-by-feature access breakdown
• Whether they can access exchange features"""

            if query:
                await query.edit_message_text(instructions_text, parse_mode="Markdown")
            elif message:
                await message.reply_text(instructions_text, parse_mode="Markdown")
            return
        
        try:
            # Get user permissions summary
            permissions = access_controller.get_user_permissions_summary(telegram_id)
            
            # Get user info from database for display
            session = SessionLocal()
            try:
                user = session.query(User).filter(User.telegram_id == telegram_id).first()
                user_display = f"{getattr(user, 'username', None) or 'N/A'} ({getattr(user, 'first_name', None) or 'N/A'})" if user else "User not found in database"
            finally:
                session.close()
            
            # Format the response
            text = f"""👤 **User Permissions Report**

**User:** `{telegram_id}`  
**Name:** {user_display}  
**Access Level:** {permissions['access_level'].title()}  
**Description:** {permissions['access_description']}

**Feature Access:**"""

            for feature, has_access in permissions['feature_access'].items():
                emoji = "✅" if has_access else "❌"
                text += f"\n{emoji} {feature.title()}"
            
            if permissions.get('restricted_features'):
                text += f"\n\n**Restricted Features:** {', '.join(permissions['restricted_features'])}"
            
            text += "\n\n**Special Status:**"
            text += f"\n{'✅' if permissions['is_admin'] else '❌'} Admin"
            text += f"\n{'✅' if permissions['is_unrestricted'] else '❌'} Unrestricted User"
            text += f"\n{'✅' if permissions['can_access_all'] else '❌'} Full Access"
            
            # Create keyboard with quick actions
            keyboard = [[
                InlineKeyboardButton("🔄 Check Another User", callback_data="admin_user_access_menu"),
                InlineKeyboardButton("🏠 Admin Menu", callback_data="admin_menu")
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            if query:
                await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
            elif message:
                await message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
                
        except Exception as e:
            logger.error(f"Error checking user permissions: {e}")
            error_text = f"❌ Error checking permissions for user {telegram_id}: {str(e)}"
            
            if query:
                await query.edit_message_text(error_text)
            elif message:
                await message.reply_text(error_text)
    
    @staticmethod
    @admin_required
    async def show_access_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show summary of current access control settings"""
        query = update.callback_query
        if query:
            await safe_answer_callback_query(query)
        
        # Get current configuration
        unrestricted_users = list(access_controller.UNRESTRICTED_TELEGRAM_IDS)
        restricted_features = list(access_controller.RESTRICTED_FEATURES)
        
        # Get user info for unrestricted users
        session = SessionLocal()
        try:
            unrestricted_display = []
            for telegram_id in unrestricted_users:
                user = session.query(User).filter(User.telegram_id == telegram_id).first()
                if user:
                    display = f"• {getattr(user, 'username', None) or 'N/A'} ({telegram_id})"
                    unrestricted_display.append(display)
                else:
                    unrestricted_display.append(f"• Unknown User ({telegram_id})")
        finally:
            session.close()
        
        text = f"""🔐 **Access Control Configuration**

**Unrestricted Users** (Full access):
{chr(10).join(unrestricted_display) if unrestricted_display else '• None configured'}

**Restricted Features:**
{chr(10).join(f'• {feature}' for feature in restricted_features)}

**Access Levels:**
• **Admin**: Full access to everything including admin features
• **Unrestricted**: Full access to all user features 
• **Standard**: All features except restricted ones

**Current Policy:**
All users have access to all features including exchange.
Access restrictions have been removed."""

        keyboard = [[
            InlineKeyboardButton("👤 Check User", callback_data="admin_check_permissions_prompt"),
            InlineKeyboardButton("🏠 Admin Menu", callback_data="admin_menu")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if query:
            await query.edit_message_text(text, parse_mode="Markdown", reply_markup=reply_markup)
        else:
            if update.message:
                await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)


# Register handlers function
def register_admin_user_access_handlers(application):
    """Register admin user access handlers"""
    from telegram.ext import CommandHandler, CallbackQueryHandler
    
    # Command handlers
    application.add_handler(CommandHandler("admin_permissions", AdminUserAccessHandler.check_user_permissions))
    
    # Callback query handlers
    application.add_handler(CallbackQueryHandler(AdminUserAccessHandler.show_access_summary, pattern="^admin_access_summary$"))
    application.add_handler(CallbackQueryHandler(AdminUserAccessHandler.check_user_permissions, pattern="^admin_check_permissions"))
    application.add_handler(CallbackQueryHandler(AdminUserAccessHandler.check_user_permissions, pattern="^admin_check_permissions_prompt$"))


# Export handler instance
admin_user_access_handler = AdminUserAccessHandler()