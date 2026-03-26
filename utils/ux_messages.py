"""Centralized UX messages for consistent, user-friendly communication"""

# USAGE:
# from utils.ux_messages import UXMessages
# message = UXMessages.SESSION['timeout_account']
# progress = UXMessages.render_progress_bar(2, 4)  # ━━▫️▫️ 50% Complete



class UXMessages:
    """Compact, mobile-friendly messages for better user experience"""

    # Session Recovery - Clear guidance for users
    SESSION = {
        "timeout_resume": "⏰ Session timed out.\n\nTap /start to continue.",
        "timeout_account": "⏰ Session timed out.\n\nTap /start to get back to your account.",
        "timeout_trade": "⏰ Session timed out.\n\nTap /start and try creating your trade again.",
        "user_refresh": "⏰ Please tap /start to refresh your account.",
        "load_fail_refresh": "🔄 Loading failed. Tap /start to refresh.",
        "cancelled": "🔄 Cancelled\n\nReturning to main menu...",
    }

    # Error messages - helpful and encouraging
    ERRORS = {
        "user_not_found": "👋 Please say /start to get set up!",
        "daily_limit": "⏳ You've reached today's limit. Come back tomorrow!",
        "invalid_amount": "💰 That amount doesn't look right. Try again?",
        "insufficient_funds": "💳 You need more funds for this. Want to add some?",
        "network_error": "🌐 Connection hiccup! Please try again",
        "file_too_large": "📎 File is too big (20MB max). Try a smaller one?",
        "invalid_email": "📧 That email doesn't look right. Double-check it?",
        "escrow_not_found": "🔍 Can't find that trade. It might be completed or cancelled",
        "unauthorized": "🔒 You don't have permission for this action",
        "already_exists": "ℹ️ This already exists! You're all set",
        "expired": "⏰ This link has expired. Need a new one?",
        "invalid_input": "❌ That doesn't look right. Want to try again?",
        "contact_load_error": "🔄 Couldn't load contacts. Tap /start to refresh.",
    }

    # Success messages - celebratory and encouraging
    SUCCESS = {
        "escrow_created": "🎉 Your secure trade is ready!",
        "funds_released": "💰 Money released! Great job",
        "cashout_requested": "💸 CashOut on its way!",
        "dispute_filed": "🛡️ We're here to help resolve this",
        "email_verified": "✅ Email confirmed! You're all set",
        "settings_saved": "⚙️ Settings updated perfectly",
        "file_uploaded": "📎 File received and saved",
        "message_sent": "💬 Message delivered!",
        "rating_submitted": "⭐ Thanks for the feedback!",
    }

    # Action prompts - friendly and clear
    PROMPTS = {
        "enter_seller": "👤 Who are you trading with? (Enter their @username)",
        "enter_amount": "💰 How much are you trading?",
        "enter_address": "📍 Where should we send the money?",
        "select_network": "🌐 Pick your preferred network:",
        "confirm_action": "Does this look right?",
        "upload_file": "📎 Want to add a file? (totally optional)",
        "enter_message": "💬 What would you like to say?",
        "rate_user": "⭐ How was your experience with this person?",
        "describe_issue": "📝 Tell us what happened:",
    }

    # Status updates - clear and encouraging
    STATUS = {
        "processing": "⏳ Working on it...",
        "waiting_seller": "👤 Waiting for your partner to accept",
        "waiting_deposit": "💰 Ready for you to add funds",
        "trade_active": "🟢 Trade is live and secure",
        "funds_held": "🔒 Money is safe and protected",
        "dispute_pending": "⚠️ Getting help to resolve this",
        "completed": "✅ Trade completed successfully",
        "cancelled": "❌ Trade was cancelled",
    }

    @staticmethod
    def format_currency_amount(amount: float, currency: str) -> str:
        """Format currency amount for display"""
        if amount < 1:
            return f"{amount:.6f} {currency}"
        elif amount < 1000:
            return f"{amount:.2f} {currency}"
        else:
            return f"{amount:,.0f} {currency}"

    @staticmethod
    def format_time_remaining(hours: int) -> str:
        """Format time remaining in user-friendly way"""
        if hours < 1:
            return "< 1h"
        elif hours < 24:
            return f"{hours}h"
        elif hours < 168:
            days = hours // 24
            return f"{days}d"
        else:
            weeks = hours // 168
            return f"{weeks}w"

    @staticmethod
    def escrow_summary_compact(escrow) -> str:
        """Generate compact escrow summary"""
        from utils.constants import CURRENCY_EMOJIS

        emoji = CURRENCY_EMOJIS.get(escrow.currency, "💰")

        return f"""
🆔 #{escrow.escrow_id[-6:]}
{emoji} {UXMessages.format_currency_amount(escrow.amount, escrow.currency)}
👤 {escrow.seller_username if '@' in escrow.seller_username else f'@{escrow.seller_username}'}
⏱️ {UXMessages.format_time_remaining(escrow.delivery_timeout_hours)}
"""

    @staticmethod
    def wallet_balance_compact(balances: list) -> str:
        """Generate compact wallet balance display"""
        if not balances:
            return "💰 Wallet Empty"

        total_value = 0
        balance_lines = []

        for balance in balances:
            if balance.balance > 0:
                emoji = balance.currency_emoji or "💰"
                amount_str = UXMessages.format_currency_amount(
                    balance.balance, balance.currency
                )
                balance_lines.append(f"{emoji} {amount_str}")
                total_value += balance.usd_value or 0

        if not balance_lines:
            return "💰 Wallet Empty"

        header = f"💰 ${total_value:.2f} USD" if total_value > 0 else "💰 Wallet"
        return f"{header}\n" + " • ".join(balance_lines)

    @staticmethod
    def get_loading_message(action: str) -> str:
        """Get appropriate loading message for action"""
        loading_map = {
            "creating_escrow": "🔄 Creating escrow...",
            "confirming_deposit": "🔄 Confirming deposit...",
            "processing_cashout": "🔄 Processing cashout...",
            "sending_notification": "🔄 Sending notification...",
            "generating_address": "🔄 Generating address...",
            "checking_balance": "🔄 Checking balance...",
            "updating_status": "🔄 Updating status...",
        }
        return loading_map.get(action, "🔄 Processing...")

    @staticmethod
    def render_progress_bar(current: int, total: int) -> str:
        """Render mathematically consistent progress bars"""
        if total <= 0 or current < 0:
            return "━━━━ 100% Complete"

        percentage = min(100, int((current / total) * 100))
        filled = max(1, int((current / total) * 4))  # 4 bars total
        empty = 4 - filled

        bar = "━" * filled + "▫️" * empty
        return f"{bar} {percentage}% Complete"

    @staticmethod
    def step_header(step: int, total: int, title: str) -> str:
        """Generate step header with consistent progress"""
        progress = UXMessages.render_progress_bar(step, total)
        return f"{title} (Step {step} of {total})\n{progress}"

    @staticmethod
    def get_session_message(context: str) -> str:
        """Get appropriate session timeout message for context"""
        return UXMessages.SESSION.get(context, UXMessages.SESSION["timeout_resume"])
