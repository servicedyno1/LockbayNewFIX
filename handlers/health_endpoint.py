"""
Health Check Endpoint Handler
Provides system health status for monitoring
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes
from monitoring.health_check import get_health_status, ping_check
from utils.admin_security import is_admin_secure
from utils.exception_handler import safe_telegram_handler

logger = logging.getLogger(__name__)

@safe_telegram_handler
async def handle_health_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /health command - admin only"""
    user = update.effective_user
    if not user:
        return

    if not is_admin_secure(user.id):
        if update.message:
            await update.message.reply_text("❌ Access denied. Admin access required.")
        return

    try:
        # Get comprehensive health status
        health_data = await get_health_status()

        # Format response
        status_emoji = {"healthy": "✅", "warning": "⚠️", "critical": "❌"}

        overall_status = health_data["status"]
        emoji = status_emoji.get(overall_status, "❓")

        # COMPACT HEALTH CHECK - 70% less verbose display
        health_data["summary"]
        db_status = "✅ OK"
        app_status = "✅ OK"

        # Get critical component status compactly
        for check in health_data["checks"]:
            comp_emoji = status_emoji.get(check["status"], "❓")
            if check["component"] == "database":
                if check["status"] != "healthy":
                    db_status = f"{comp_emoji} {check['status'].upper()}"
            elif check["component"] == "application":
                if check["status"] != "healthy":
                    app_status = f"{comp_emoji} {check['status'].upper()}"

        message = f"""🏥 Health: {emoji} {overall_status.upper()}
💾 DB: {db_status} • 🎯 App: {app_status}
✅ {len([c for c in health_data['checks'] if c['status'] == 'healthy'])}/{len(health_data['checks'])} healthy

Last check: {health_data.get('timestamp', 'Unknown')[11:16] if health_data.get('timestamp') else 'Unknown'}"""

        # Add compact navigation for health check
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup

        keyboard = [
            [
                InlineKeyboardButton(
                    "🔄 Refresh", callback_data="admin_health_refresh"
                ),
                InlineKeyboardButton("🏠 Admin", callback_data="admin_main"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if update.message:
            await update.message.reply_text(
                message, parse_mode="Markdown", reply_markup=reply_markup
            )

    except Exception as e:
        logger.error(f"Health check failed: {e}")
        if update.message:
            await update.message.reply_text(f"❌ Health check failed: {str(e)}")

@safe_telegram_handler
async def handle_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ping command - basic availability check"""
    user = update.effective_user
    if not user:
        return

    if not is_admin_secure(user.id):
        if update.message:
            await update.message.reply_text("❌ Access denied.")
        return

    try:
        ping_data = await ping_check()
        if update.message:
            await update.message.reply_text(
                f"🏓 Pong!\n\nService is running normally.\n*{ping_data['timestamp']}*",
                parse_mode="Markdown",
            )
    except Exception as e:
        if update.message:
            await update.message.reply_text(f"❌ Ping failed: {str(e)}")

@safe_telegram_handler
async def handle_system_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sysinfo command - system information"""
    user = update.effective_user
    if not user:
        return

    if not is_admin_secure(user.id):
        if update.message:
            await update.message.reply_text("❌ Access denied.")
        return

    try:
        # COLLISION FIX: Use shared CPU monitor to prevent resource contention
        from utils.shared_cpu_monitor import get_cpu_usage, get_memory_usage
        import psutil
        import os
        from datetime import datetime

        # Get performance metrics using shared service
        cpu_reading = await get_cpu_usage()
        await get_memory_usage()
        
        # Extract values for display
        cpu_percent = cpu_reading.cpu_percent  # System CPU
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        # Get process information
        process = psutil.Process(os.getpid())
        boot_time = datetime.fromtimestamp(psutil.boot_time())
        uptime = datetime.now() - boot_time

        message = f"""💻 System Information

Resources:
• CPU Usage: {cpu_percent}%
• Memory: {memory.percent}% ({memory.available / (1024**3):.1f}GB free)
• Disk: {disk.percent}% ({disk.free / (1024**3):.1f}GB free)

Process:
• PID: {process.pid}
• Memory: {process.memory_info().rss / (1024**2):.1f}MB
• CPU: {process.cpu_percent()}%
• Threads: {process.num_threads()}

System:
• Boot Time: {boot_time.strftime('%Y-%m-%d %H:%M:%S')}
• Uptime: {str(uptime).split('.')[0]}
• Load Avg: {', '.join(map(str, os.getloadavg()[:3]))}

*Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"""

        if update.message:
            await update.message.reply_text(message, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"System info failed: {e}")
        if update.message:
            await update.message.reply_text(f"❌ System info failed: {str(e)}")
