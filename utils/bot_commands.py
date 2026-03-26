"""
Bot Commands Setup - Telegram Bot Menu Configuration
Creates the proper bot menu that users see in Telegram interface
"""

import logging
from telegram import Bot, BotCommand, BotCommandScopeChat
from telegram.ext import Application
from typing import List, Optional

logger = logging.getLogger(__name__)

class BotCommandsManager:
    """Manages Telegram bot commands and menu setup"""
    
    # Limited commands for non-onboarded users (default/safe set)
    LIMITED_COMMANDS = [
        BotCommand("start", "🚀 Start LockBay - Register & Access Dashboard"),
        BotCommand("help", "❓ Get help and support"),
    ]
    
    # Full commands for onboarded users
    FULL_COMMANDS = [
        BotCommand("start", "🚀 Start LockBay - Register & Access Dashboard"),
        BotCommand("help", "❓ Get help and support"),
        BotCommand("menu", "📋 Show main menu"),
        BotCommand("wallet", "💰 View your wallet & balances"),
        BotCommand("escrow", "🔒 Create new escrow transaction"),
        BotCommand("orders", "📊 View your orders & transactions"),
        BotCommand("profile", "👤 View and edit your profile"),
        BotCommand("settings", "⚙️ Change your settings"),
    ]
    
    # Keep COMMANDS for backward compatibility (use LIMITED as default for safety)
    COMMANDS = LIMITED_COMMANDS
    
    @classmethod
    async def setup_bot_commands(cls, application: Application) -> bool:
        """
        Set up the bot commands menu that appears in Telegram
        
        Args:
            application: The Telegram bot application
            
        Returns:
            bool: True if commands were set successfully
        """
        try:
            logger.info("🤖 Setting up Telegram bot commands menu...")
            
            bot = application.bot
            await bot.set_my_commands(cls.COMMANDS)
            
            logger.info(f"✅ Bot commands menu configured with {len(cls.COMMANDS)} commands")
            
            # Log the commands for verification
            [f"/{cmd.command} - {cmd.description}" for cmd in cls.COMMANDS]
            logger.info(f"📋 Commands available: {', '.join([cmd.command for cmd in cls.COMMANDS])}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to set up bot commands: {e}")
            return False
    
    @classmethod
    async def get_current_commands(cls, bot: Bot) -> List[BotCommand]:
        """
        Get the currently set bot commands
        
        Args:
            bot: The Telegram bot instance
            
        Returns:
            List of current bot commands
        """
        try:
            commands = await bot.get_my_commands()
            return list(commands)
        except Exception as e:
            logger.error(f"❌ Failed to get current commands: {e}")
            return []
    
    @classmethod
    async def verify_commands_setup(cls, bot: Bot) -> bool:
        """
        Verify that bot commands are properly configured
        
        Args:
            bot: The Telegram bot instance
            
        Returns:
            bool: True if commands are properly set up
        """
        try:
            current_commands = await cls.get_current_commands(bot)
            
            if len(current_commands) == 0:
                logger.warning("⚠️ No bot commands are currently set")
                return False
            
            logger.info(f"✅ Bot commands verification: {len(current_commands)} commands active")
            
            # Check if our expected commands are present
            current_command_names = [cmd.command for cmd in current_commands]
            expected_command_names = [cmd.command for cmd in cls.COMMANDS]
            
            missing_commands = set(expected_command_names) - set(current_command_names)
            if missing_commands:
                logger.warning(f"⚠️ Missing commands: {missing_commands}")
                return False
            
            logger.info("✅ All expected commands are properly configured")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to verify commands setup: {e}")
            return False
    
    @classmethod
    async def set_user_commands(cls, user_id: int, is_onboarded: bool, bot: Optional[Bot] = None) -> bool:
        """
        Set user-specific commands based on onboarding status
        
        Args:
            user_id: The Telegram user ID
            is_onboarded: Whether the user has completed onboarding
            bot: Optional Bot instance (if not provided, must be called from within bot context)
            
        Returns:
            bool: True if commands were set successfully
        """
        try:
            if bot is None:
                logger.error("❌ Bot instance required to set user commands")
                return False
            
            # Select command set based on onboarding status
            commands = cls.FULL_COMMANDS if is_onboarded else cls.LIMITED_COMMANDS
            command_type = "full" if is_onboarded else "limited"
            
            # Set user-specific commands using BotCommandScopeChat
            scope = BotCommandScopeChat(chat_id=user_id)
            await bot.set_my_commands(commands, scope=scope)
            
            logger.info(
                f"✅ Set {command_type} commands for user {user_id}: "
                f"{', '.join([cmd.command for cmd in commands])}"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to set commands for user {user_id}: {e}")
            return False


# Convenience function for easy integration
async def initialize_bot_commands(application: Application) -> bool:
    """
    Initialize bot commands during startup
    
    Args:
        application: The Telegram bot application
        
    Returns:
        bool: True if initialization was successful
    """
    return await BotCommandsManager.setup_bot_commands(application)


async def verify_bot_commands(application: Application) -> bool:
    """
    Verify bot commands are working
    
    Args:
        application: The Telegram bot application
        
    Returns:
        bool: True if commands are working properly
    """
    return await BotCommandsManager.verify_commands_setup(application.bot)