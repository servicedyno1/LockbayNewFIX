"""
Bot Commands Migration - Restore Commands for Existing Onboarded Users
========================================================================

This migration ensures all existing onboarded users get the full command menu.
Runs once during bot startup to fix the bug where only new users received full commands.
"""

import logging
from telegram.ext import Application
from sqlalchemy import select

from models import User
from database import async_managed_session
from utils.bot_commands import BotCommandsManager

logger = logging.getLogger(__name__)


async def migrate_onboarded_user_commands(application: Application) -> dict:
    """
    Startup migration to restore full commands for all existing onboarded users.
    
    This fixes the critical bug where:
    - Global default commands were set to LIMITED_COMMANDS
    - Only NEW users completing onboarding got FULL_COMMANDS  
    - EXISTING onboarded users remained stuck with LIMITED_COMMANDS
    
    Args:
        application: The Telegram bot application with bot instance
        
    Returns:
        dict: Migration results with success count and any errors
    """
    try:
        logger.info("🔧 COMMAND_MIGRATION: Starting migration for existing onboarded users...")
        
        bot = application.bot
        if not bot:
            logger.error("❌ COMMAND_MIGRATION: Bot instance not available")
            return {"success": False, "error": "Bot instance unavailable"}
        
        migrated_count = 0
        error_count = 0
        errors = []
        
        async with async_managed_session() as session:
            # Query all users who completed onboarding
            query = select(User).where(User.onboarding_completed == True)
            result = await session.execute(query)
            onboarded_users = result.scalars().all()
            
            total_users = len(onboarded_users)
            logger.info(f"📊 COMMAND_MIGRATION: Found {total_users} onboarded users to process")
            
            # Process each onboarded user
            for user in onboarded_users:
                try:
                    # Set full commands for this user
                    success = await BotCommandsManager.set_user_commands(
                        user_id=user.telegram_id,
                        is_onboarded=True,
                        bot=bot
                    )
                    
                    if success:
                        migrated_count += 1
                        logger.debug(f"✅ COMMAND_MIGRATION: User {user.telegram_id} - commands restored")
                    else:
                        error_count += 1
                        error_msg = f"Failed to set commands for user {user.telegram_id}"
                        errors.append(error_msg)
                        logger.warning(f"⚠️ COMMAND_MIGRATION: {error_msg}")
                        
                except Exception as e:
                    error_count += 1
                    error_msg = f"User {user.telegram_id}: {str(e)}"
                    errors.append(error_msg)
                    logger.error(f"❌ COMMAND_MIGRATION: Error for user {user.telegram_id}: {e}")
        
        # Log final results
        if error_count > 0:
            logger.warning(
                f"⚠️ COMMAND_MIGRATION: Completed with errors - "
                f"Success: {migrated_count}/{total_users}, Errors: {error_count}"
            )
        else:
            logger.info(
                f"✅ COMMAND_MIGRATION: Successfully restored commands for "
                f"{migrated_count}/{total_users} onboarded users"
            )
        
        return {
            "success": True,
            "total_users": total_users,
            "migrated_count": migrated_count,
            "error_count": error_count,
            "errors": errors[:10] if errors else []  # Limit error list to avoid bloat
        }
        
    except Exception as e:
        logger.error(f"❌ COMMAND_MIGRATION: Fatal error during migration: {e}")
        return {
            "success": False,
            "error": str(e),
            "migrated_count": 0,
            "error_count": 0
        }


async def run_commands_migration_background(application: Application) -> None:
    """
    Run the commands migration in the background without blocking startup.
    
    This is called after the bot is fully initialized and ready.
    """
    try:
        logger.info("🚀 COMMAND_MIGRATION: Starting background migration task...")
        result = await migrate_onboarded_user_commands(application)
        
        if result.get("success"):
            logger.info(
                f"✅ COMMAND_MIGRATION: Background migration completed - "
                f"Processed {result.get('migrated_count', 0)} users"
            )
        else:
            logger.error(
                f"❌ COMMAND_MIGRATION: Background migration failed - "
                f"{result.get('error', 'Unknown error')}"
            )
            
    except Exception as e:
        logger.error(f"❌ COMMAND_MIGRATION: Background migration crashed: {e}")
