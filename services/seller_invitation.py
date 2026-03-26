"""
Seller Invitation Service - Handle inviting sellers via username or email
"""

import re
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from database import SessionLocal
from models import User
from services.welcome_email import WelcomeEmailService
from services.sms_eligibility_service import SMSEligibilityService
from config import Config
import logging

logger = logging.getLogger(__name__)


class SellerInvitationService:
    """Service to handle seller invitations via username or email"""

    @staticmethod
    def is_valid_email(email: str) -> bool:
        """Check if the input is a valid email address with ReDoS protection"""
        # Use length check first to prevent ReDoS attacks
        if len(email) > 254:  # RFC 5321 limit
            return False
        
        # Simple, ReDoS-safe email validation
        email_pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9._-]*[a-zA-Z0-9])?@[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?\.[a-zA-Z]{2,}$'
        return re.match(email_pattern, email.lower()) is not None

    @staticmethod
    def is_valid_username(username: str) -> bool:
        """Check if the input is a valid Telegram username"""
        # Username validation (without @)
        if not username:
            return False

        # Must start with a letter
        if not username[0].isalpha():
            return False

        # Must be 4-32 characters (aligning with Telegram standards)
        if len(username) < 4 or len(username) > 32:
            return False

        # Only letters, numbers, and underscores
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]*$", username):
            return False

        return True

    @staticmethod
    def generate_invitation_token() -> str:
        """Generate a cryptographically secure invitation token"""
        from utils.secure_crypto import SecureCrypto
        return SecureCrypto.generate_invitation_token()

    @staticmethod
    def find_user_by_email(email: str, session: Session) -> Optional[User]:
        """Find an existing user by email address (case-insensitive)"""
        from sqlalchemy import func
        return session.query(User).filter(func.lower(User.email) == func.lower(email)).first()

    @staticmethod
    def find_user_by_username(username: str, session: Session) -> Optional[User]:
        """Find an existing user by username (case-insensitive)"""
        from sqlalchemy import func
        return session.query(User).filter(func.lower(User.username) == func.lower(username)).first()

    @staticmethod
    def find_user_by_phone(phone: str, session: Session) -> Optional[User]:
        """Find an existing user by phone number"""
        return session.query(User).filter(User.phone_number == phone).first()

    @staticmethod
    def is_valid_phone(phone: str) -> bool:
        """Validate phone number format"""
        if not phone:
            return False

        # Remove all non-digit characters except +
        cleaned = re.sub(r"[^\d+]", "", phone.strip())

        # Must start with + and have 7-15 digits
        if not re.match(r"^\+[1-9]\d{6,14}$", cleaned):
            return False

        return True

    @staticmethod
    async def process_seller_input(
        seller_input: str, buyer_user: User
    ) -> Dict[str, Any]:
        """
        Process seller input (username, email, or phone) and return seller info

        Enhanced to detect email addresses linked to existing Telegram users

        Returns:
        {
            'type': 'username' | 'email' | 'email_with_telegram' | 'phone' | 'phone_with_telegram',
            'seller_identifier': str,  # username, email, or phone
            'seller_user': User | None,  # existing user if found
            'display_name': str,  # for UI display
            'needs_invitation': bool,  # if email invitation needed
            'telegram_user': User | None,  # if email is linked to Telegram user
            'notification_preference': 'telegram' | 'email' | 'sms'  # preferred notification method
        }
        """
        seller_input = seller_input.strip()

        # Check if it's a phone number (starts with + or contains only digits)
        if SellerInvitationService.is_valid_phone(seller_input):
            return await SellerInvitationService._process_phone_seller(
                seller_input, buyer_user
            )

        # Check if it's an email
        if SellerInvitationService.is_valid_email(seller_input):
            return await SellerInvitationService._process_email_seller(
                seller_input, buyer_user
            )

        # Check if it's a username (with or without @)
        if seller_input.startswith("@"):
            username = seller_input[1:]  # Remove @
        else:
            username = seller_input

        if SellerInvitationService.is_valid_username(username):
            return await SellerInvitationService._process_username_seller(
                username, buyer_user
            )

        # Invalid input
        return {
            "type": "invalid",
            "error": "Please enter a valid @username, email address, or phone number (e.g., +1234567890)",
        }

    @staticmethod
    async def _process_email_seller(email: str, buyer_user: User) -> Dict[str, Any]:
        """Process email-based seller with enhanced Telegram detection"""
        session = SessionLocal()
        try:
            # Check if user with this email already exists (case-insensitive)
            from sqlalchemy import func
            existing_user = (
                session.query(User).filter(func.lower(User.email) == func.lower(email)).first()
            )

            if existing_user:
                # User exists with this email - they have Telegram account
                return {
                    "type": "email_with_telegram",
                    "seller_identifier": email,
                    "seller_user": existing_user,
                    "telegram_user": existing_user,
                    "display_name": f"{getattr(existing_user, 'first_name', None) or getattr(existing_user, 'username', None) or 'User'} ({email})",
                    "needs_invitation": False,
                    "notification_preference": "telegram",  # Prefer Telegram for existing users
                }
            else:
                # New user - will need email invitation
                return {
                    "type": "email",
                    "seller_identifier": email,
                    "seller_user": None,
                    "telegram_user": None,
                    "display_name": email,
                    "needs_invitation": True,
                    "notification_preference": "email",
                }
        finally:
            session.close()

    @staticmethod
    async def _process_username_seller(
        username: str, buyer_user: User
    ) -> Dict[str, Any]:
        """Process username-based seller"""
        session = SessionLocal()
        try:
            # Check if user with this username exists (case-insensitive)
            from sqlalchemy import func
            existing_user = (
                session.query(User).filter(func.lower(User.username) == func.lower(username)).first()
            )

            if existing_user:
                # Existing user with reputation info
                # Handle SQLAlchemy Column types properly
                rep_score = getattr(existing_user, "reputation_score", None)
                reputation_score = 0.0 if rep_score is None else float(rep_score)
                trades_count = getattr(existing_user, "total_trades", None)
                total_trades = 0 if trades_count is None else int(trades_count)
                rating_stars = (
                    "⭐" * int(reputation_score) if reputation_score > 0 else "New User"
                )
                completion_rate = (
                    f"{total_trades}/{total_trades}" if total_trades > 0 else "0/0"
                )

                return {
                    "type": "username",
                    "seller_identifier": username,
                    "seller_user": existing_user,
                    "telegram_user": existing_user,
                    "display_name": f"@{username} ({rating_stars} {completion_rate})",
                    "needs_invitation": False,
                    "notification_preference": "telegram",
                }
            else:
                # Username doesn't exist yet - will be invited via Telegram
                return {
                    "type": "username",
                    "seller_identifier": username,
                    "seller_user": None,
                    "telegram_user": None,
                    "display_name": f"@{username} (New User)",
                    "needs_invitation": True,
                    "notification_preference": "telegram",
                }
        finally:
            session.close()

    @staticmethod
    async def _process_phone_seller(phone: str, buyer_user: User) -> Dict[str, Any]:
        """Process phone-based seller with SMS eligibility checks"""
        session = SessionLocal()
        try:
            # Normalize phone number
            cleaned_phone = re.sub(r"[^\d+]", "", phone.strip())

            # Check if user with this phone exists
            existing_user = (
                session.query(User).filter(User.phone_number == cleaned_phone).first()
            )

            if existing_user:
                # User exists with this phone - they have Telegram account
                rep_score = getattr(existing_user, "reputation_score", None)
                reputation_score = 0.0 if rep_score is None else float(rep_score)
                trades_count = getattr(existing_user, "total_trades", None)
                total_trades = 0 if trades_count is None else int(trades_count)
                rating_stars = (
                    "⭐" * int(reputation_score) if reputation_score > 0 else "New User"
                )
                completion_rate = (
                    f"{total_trades}/{total_trades}" if total_trades > 0 else "0/0"
                )

                return {
                    "type": "phone_with_telegram",
                    "seller_identifier": cleaned_phone,
                    "seller_user": existing_user,
                    "telegram_user": existing_user,
                    "display_name": f"📱 {cleaned_phone} ({rating_stars} {completion_rate})",
                    "needs_invitation": False,
                    "notification_preference": "telegram",  # Prefer Telegram for existing users
                }
            else:
                # New user - will need SMS invitation, check eligibility first
                eligibility = await SMSEligibilityService.check_sms_eligibility(int(buyer_user.id))
                
                if not eligibility["eligible"]:
                    # SMS not allowed - return restricted type
                    return {
                        "type": "phone_restricted",
                        "seller_identifier": cleaned_phone,
                        "seller_user": None,
                        "telegram_user": None,
                        "display_name": f"📱 {cleaned_phone} (SMS Restricted)",
                        "needs_invitation": False,  # Can't send invitation
                        "notification_preference": None,
                        "restriction_reason": eligibility["reason"],
                        "trading_volume": eligibility["trading_volume"],
                        "required_volume": eligibility["required_volume"],
                        "remaining_sms": eligibility["remaining_sms"]
                    }
                
                # SMS allowed - proceed with SMS invitation
                return {
                    "type": "phone",
                    "seller_identifier": cleaned_phone,
                    "seller_user": None,
                    "telegram_user": None,
                    "display_name": f"📱 {cleaned_phone} (New User - SMS allowed)",
                    "needs_invitation": True,
                    "notification_preference": "sms",
                    "sms_remaining": eligibility["remaining_sms"]
                }
        finally:
            session.close()

    @staticmethod
    def format_seller_display(seller_info: Dict[str, Any]) -> str:
        """Format seller display with reputation information for buyer confirmation"""
        if seller_info.get("type") == "invalid":
            return "❌ Invalid seller"

        seller_type = seller_info.get("type")
        display_name = seller_info.get("display_name", "Unknown")
        seller_user = seller_info.get("seller_user")

        # For existing users, show enhanced reputation info
        if seller_user and seller_type in [
            "username",
            "email_with_telegram",
            "phone_with_telegram",
        ]:
            # Get reputation details
            rep_score = getattr(seller_user, "reputation_score", None)
            total_trades = getattr(seller_user, "total_trades", None)

            reputation_score = 0.0 if rep_score is None else float(rep_score)
            trades_count = 0 if total_trades is None else int(total_trades)

            # Create reputation display
            if trades_count > 0:
                rating_stars = (
                    "⭐" * min(int(reputation_score), 5)
                    if reputation_score > 0
                    else "🆕"
                )
                success_rate = f"{trades_count}/{trades_count}"  # For now, assuming all completed trades were successful

                if seller_type == "username":
                    return f"@{seller_info['seller_identifier']} {rating_stars} ({success_rate} trades)"
                elif seller_type == "email_with_telegram":
                    return f"{seller_info['seller_identifier']} {rating_stars} ({success_rate} trades)\n     └ 📱 Connected to Telegram: @{seller_user.username or 'user'}"
                elif seller_type == "phone_with_telegram":
                    return f"📱 {seller_info['seller_identifier']} {rating_stars} ({success_rate} trades)\n     └ 📱 Connected to Telegram: @{seller_user.username or 'user'}"
            else:
                # New user with Telegram account
                if seller_type == "username":
                    return f"@{seller_info['seller_identifier']} 🆕 New Trader"
                elif seller_type == "email_with_telegram":
                    return f"{seller_info['seller_identifier']} 🆕 New Trader\n     └ 📱 Connected to Telegram: @{seller_user.username or 'user'}"
                elif seller_type == "phone_with_telegram":
                    return f"📱 {seller_info['seller_identifier']} 🆕 New Trader\n     └ 📱 Connected to Telegram: @{seller_user.username or 'user'}"

        # For new users (no existing account)
        if seller_type == "email":
            return f"{seller_info['seller_identifier']} 🆕 New User (will receive email invitation)"
        elif seller_type == "username":
            return f"@{seller_info['seller_identifier']} 🆕 New User (will receive Telegram invitation)"
        elif seller_type == "phone":
            sms_remaining = seller_info.get("sms_remaining", 0)
            return f"📱 {seller_info['seller_identifier']} 🆕 New User (will receive SMS invitation)\n     └ SMS remaining today: {sms_remaining}"
        elif seller_type == "phone_restricted":
            return f"❌ 📱 {seller_info['seller_identifier']} - SMS invitations not available\n     └ {seller_info.get('restriction_reason', 'SMS restrictions apply')}"

        # Fallback
        return display_name

    @staticmethod
    async def send_email_invitation(
        email: str,
        buyer_name: str,
        escrow_id: str,
        amount_usd: float,
        description: str,
        invitation_link: str | None = None,
        base_amount: float | None = None,
        seller_fee: float = 0.0,
        fee_split: str = "buyer_pays",
    ) -> bool:
        """Send email invitation to new seller"""
        try:
            # Use provided invitation link if available, otherwise generate generic invitation
            invitation_token = None
            if not invitation_link:
                # Generate invitation token for generic invitations
                invitation_token = SellerInvitationService.generate_invitation_token()

                # Create invitation link
                bot_username = Config.BOT_USERNAME or "escrowprototype_bot"
                invitation_link = (
                    f"https://t.me/{bot_username}?start=invite_{invitation_token}"
                )

            # Store invitation token in database (we'll need to create a table for this)
            session = SessionLocal()
            try:
                # For now, we'll store in user_data or create a simple mapping
                # Note: Invitation tokens are handled in-memory for simplicity
                # For production, consider storing in Redis or database table
                if invitation_token:
                    logger.debug(
                        f"Generated invitation token: {invitation_token[:8]}..."
                    )
            finally:
                session.close()

            # Calculate fee details for display
            if base_amount is None:
                base_amount = amount_usd

            # Determine fee text and seller payout
            if fee_split == "buyer_pays":
                fee_text = "🟢 No fees for you"
                seller_receives = base_amount
            elif fee_split == "seller_pays":
                fee_text = f"🔴 You pay ${seller_fee:.2f} fee"
                seller_receives = base_amount - seller_fee
            else:  # split
                fee_text = f"🟡 Split fees (${seller_fee:.2f})"
                seller_receives = base_amount - seller_fee

            # Compose email
            subject = f"Trade Invitation: ${base_amount:.0f} USD - {buyer_name}"

            html_content = f"""
            <!DOCTYPE html>
            <html lang="en">
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>LockBay Trade Invitation</title>
                <style>
                    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap');
                    
                    * {{
                        box-sizing: border-box;
                        margin: 0;
                        padding: 0;
                    }}
                    
                    body {{
                        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                        line-height: 1.6;
                        color: #1a1a1a;
                        background: #f8fafc;
                        margin: 0;
                        padding: 0;
                        -webkit-font-smoothing: antialiased;
                        -moz-osx-font-smoothing: grayscale;
                    }}
                    
                    .email-container {{
                        max-width: 680px;
                        margin: 40px auto;
                        background: #ffffff;
                        border-radius: 16px;
                        overflow: hidden;
                        box-shadow: 0 10px 30px rgba(0, 0, 0, 0.08), 0 1px 8px rgba(0, 0, 0, 0.06);
                        border: 1px solid rgba(0, 0, 0, 0.04);
                    }}
                    
                    .header {{
                        background: linear-gradient(135deg, #1e40af 0%, #3b82f6 100%);
                        color: white;
                        padding: 48px 40px;
                        position: relative;
                        overflow: hidden;
                    }}
                    
                    .header::before {{
                        content: '';
                        position: absolute;
                        top: 0;
                        left: 0;
                        right: 0;
                        bottom: 0;
                        background: url('data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32" fill="none" stroke="rgba(255,255,255,0.05)"><path d="m0 2 2-2 2 2"/><path d="m0 10 2-2 2 2"/><path d="m0 18 2-2 2 2"/><path d="m0 26 2-2 2 2"/></svg>');
                        opacity: 0.4;
                    }}
                    
                    .header-content {{
                        position: relative;
                        z-index: 2;
                        text-align: center;
                    }}
                    
                    .logo {{
                        font-size: 28px;
                        font-weight: 800;
                        margin: 0 0 8px 0;
                        letter-spacing: -0.02em;
                        color: #ffffff;
                    }}
                    
                    .tagline {{
                        font-size: 15px;
                        opacity: 0.85;
                        font-weight: 400;
                        margin: 0;
                        color: rgba(255, 255, 255, 0.9);
                    }}
                    
                    .notification-badge {{
                        background: rgba(34, 197, 94, 0.15);
                        border: 1px solid rgba(34, 197, 94, 0.3);
                        color: #16a34a;
                        padding: 6px 16px;
                        border-radius: 20px;
                        font-size: 13px;
                        font-weight: 600;
                        display: inline-block;
                        margin-bottom: 24px;
                        backdrop-filter: blur(10px);
                    }}
                    
                    .main-content {{
                        padding: 48px 40px;
                        background: #ffffff;
                    }}
                    
                    .invite-title {{
                        color: #111827;
                        font-size: 32px;
                        font-weight: 700;
                        margin: 0 0 16px 0;
                        text-align: center;
                        letter-spacing: -0.02em;
                        line-height: 1.2;
                    }}
                    
                    .invite-subtitle {{
                        color: #6b7280;
                        font-size: 18px;
                        font-weight: 400;
                        margin: 0 0 40px 0;
                        text-align: center;
                        line-height: 1.5;
                    }}
                    
                    .trade-card {{
                        background: #ffffff;
                        border: 1px solid #e5e7eb;
                        border-radius: 12px;
                        padding: 32px;
                        margin: 32px 0;
                        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1), 0 1px 2px rgba(0, 0, 0, 0.06);
                        position: relative;
                    }}
                    
                    .trade-card::before {{
                        content: '';
                        position: absolute;
                        top: 0;
                        left: 0;
                        right: 0;
                        height: 4px;
                        background: linear-gradient(90deg, #3b82f6, #8b5cf6, #06b6d4);
                        border-radius: 12px 12px 0 0;
                    }}
                    
                    .trade-header {{
                        border-bottom: 1px solid #f3f4f6;
                        padding-bottom: 16px;
                        margin-bottom: 20px;
                    }}
                    
                    .trade-title {{
                        color: #111827;
                        font-size: 18px;
                        font-weight: 600;
                        margin: 0 0 4px 0;
                    }}
                    
                    .trade-id {{
                        color: #6b7280;
                        font-size: 14px;
                        font-weight: 500;
                        font-family: 'SF Mono', 'Monaco', 'Cascadia Code', 'Roboto Mono', monospace;
                    }}
                    
                    .trade-detail {{
                        display: flex;
                        justify-content: space-between;
                        align-items: center;
                        padding: 16px 0;
                        border-bottom: 1px solid #f3f4f6;
                    }}
                    
                    .trade-detail:last-child {{
                        border-bottom: none;
                        padding-bottom: 0;
                    }}
                    
                    .detail-left {{
                        display: flex;
                        align-items: center;
                        gap: 12px;
                    }}
                    
                    .detail-icon {{
                        width: 40px;
                        height: 40px;
                        border-radius: 8px;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        font-size: 18px;
                        background: #f3f4f6;
                    }}
                    
                    .buyer-icon {{ background: linear-gradient(135deg, #dbeafe 0%, #bfdbfe 100%); }}
                    .amount-icon {{ background: linear-gradient(135deg, #dcfce7 0%, #bbf7d0 100%); }}
                    .service-icon {{ background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); }}
                    .id-icon {{ background: linear-gradient(135deg, #e0e7ff 0%, #c7d2fe 100%); }}
                    
                    .detail-content {{
                        flex: 1;
                    }}
                    
                    .detail-label {{
                        color: #6b7280;
                        font-weight: 500;
                        font-size: 13px;
                        margin: 0 0 2px 0;
                        text-transform: uppercase;
                        letter-spacing: 0.05em;
                    }}
                    
                    .detail-value {{
                        color: #111827;
                        font-weight: 600;
                        font-size: 16px;
                        margin: 0;
                    }}
                    
                    .amount-highlight {{
                        color: #059669;
                        font-size: 24px;
                        font-weight: 700;
                        letter-spacing: -0.01em;
                    }}
                    
                    .cta-section {{
                        text-align: center;
                        margin: 40px 0;
                        padding: 32px 0;
                    }}
                    
                    .cta-button {{
                        background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%);
                        color: white;
                        padding: 16px 40px;
                        text-decoration: none;
                        border-radius: 8px;
                        font-weight: 600;
                        font-size: 16px;
                        display: inline-block;
                        box-shadow: 0 4px 14px rgba(59, 130, 246, 0.4);
                        transition: all 0.2s ease;
                        border: none;
                        letter-spacing: 0.02em;
                        position: relative;
                        overflow: hidden;
                    }}
                    
                    .cta-button::before {{
                        content: '';
                        position: absolute;
                        top: 0;
                        left: -100%;
                        width: 100%;
                        height: 100%;
                        background: linear-gradient(90deg, transparent, rgba(255,255,255,0.1), transparent);
                        transition: left 0.5s ease;
                    }}
                    
                    .cta-button:hover::before {{
                        left: 100%;
                    }}
                    
                    .cta-text {{
                        position: relative;
                        z-index: 1;
                    }}
                    
                    .process-steps {{
                        background: #f8fafc;
                        border-radius: 12px;
                        padding: 24px;
                        margin: 24px 0;
                    }}
                    
                    .process-title {{
                        color: #1e293b;
                        font-size: 18px;
                        font-weight: 600;
                        margin: 0 0 16px 0;
                        text-align: center;
                    }}
                    
                    .step {{
                        display: flex;
                        align-items: center;
                        margin: 12px 0;
                    }}
                    
                    .step-number {{
                        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                        color: white;
                        width: 28px;
                        height: 28px;
                        border-radius: 50%;
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        font-weight: 600;
                        font-size: 14px;
                        margin-right: 12px;
                        flex-shrink: 0;
                    }}
                    
                    .step-text {{
                        color: #475569;
                        font-size: 14px;
                        line-height: 1.5;
                    }}
                    
                    .security-badge {{
                        background: #f8fafc;
                        border: 1px solid #e2e8f0;
                        border-radius: 8px;
                        padding: 20px;
                        margin: 32px 0;
                        text-align: center;
                    }}
                    
                    .security-text {{
                        color: #374151;
                        font-size: 15px;
                        margin: 0;
                        font-weight: 500;
                        line-height: 1.5;
                    }}
                    
                    .security-icon {{
                        color: #10b981;
                        font-size: 20px;
                        margin-right: 8px;
                    }}
                    
                    .trust-indicators {{
                        display: flex;
                        justify-content: center;
                        gap: 32px;
                        margin: 32px 0;
                        padding: 24px 0;
                        border-top: 1px solid #f3f4f6;
                        border-bottom: 1px solid #f3f4f6;
                    }}
                    
                    .trust-item {{
                        text-align: center;
                        flex: 1;
                    }}
                    
                    .trust-number {{
                        color: #111827;
                        font-size: 20px;
                        font-weight: 700;
                        display: block;
                        margin-bottom: 4px;
                    }}
                    
                    .trust-label {{
                        color: #6b7280;
                        font-size: 13px;
                        font-weight: 500;
                    }}
                    
                    .footer {{
                        background: #f9fafb;
                        padding: 32px 40px;
                        text-align: center;
                        border-top: 1px solid #e5e7eb;
                    }}
                    
                    .footer-brand {{
                        color: #111827;
                        font-size: 16px;
                        font-weight: 600;
                        margin: 0 0 12px 0;
                    }}
                    
                    .footer-text {{
                        color: #6b7280;
                        font-size: 14px;
                        margin: 0 0 16px 0;
                        line-height: 1.5;
                    }}
                    
                    .footer-links {{
                        margin: 20px 0;
                    }}
                    
                    .footer-links a {{
                        color: #3b82f6;
                        text-decoration: none;
                        font-weight: 500;
                        margin: 0 16px;
                        font-size: 14px;
                    }}
                    
                    .footer-legal {{
                        color: #9ca3af;
                        font-size: 12px;
                        margin-top: 20px;
                        line-height: 1.4;
                    }}
                    
                    @media (max-width: 600px) {{
                        .container {{
                            margin: 0 16px;
                        }}
                        
                        .header, .main-content {{
                            padding: 24px 20px;
                        }}
                        
                        .logo {{
                            font-size: 28px;
                        }}
                        
                        .trade-card {{
                            padding: 20px;
                        }}
                    }}
                </style>
            </head>
            <body>
                <div class="email-container">
                    <div class="header">
                        <div class="header-content">
                            <div class="notification-badge">New Trade Invitation</div>
                            <h1 class="logo">🔒 LockBay</h1>
                            <p class="tagline">Secure Crypto Escrow Platform</p>
                        </div>
                    </div>
                    
                    <div class="main-content">
                        <h2 class="invite-title">You've received a trade invitation</h2>
                        <p class="invite-subtitle">A secure escrow transaction is waiting for your acceptance</p>
                        
                        <div class="trade-card">
                            <div class="trade-header">
                                <div class="trade-title">💰 New Trade Invitation</div>
                                <div class="trade-id">#{escrow_id}</div>
                            </div>
                            
                            <div class="trade-detail">
                                <div class="detail-left">
                                    <div class="detail-icon buyer-icon">👤</div>
                                    <div class="detail-content">
                                        <div class="detail-label">Buyer</div>
                                        <div class="detail-value">{buyer_name}</div>
                                    </div>
                                </div>
                            </div>
                            
                            <div class="trade-detail">
                                <div class="detail-left">
                                    <div class="detail-icon amount-icon">💵</div>
                                    <div class="detail-content">
                                        <div class="detail-label">Trade Amount</div>
                                        <div class="detail-value amount-highlight">${base_amount:.2f} USD ✅ <strong>Paid & Secured</strong></div>
                                    </div>
                                </div>
                            </div>
                            
                            <div class="trade-detail">
                                <div class="detail-left">
                                    <div class="detail-icon service-icon">💸</div>
                                    <div class="detail-content">
                                        <div class="detail-label">Fees</div>
                                        <div class="detail-value">{fee_text}</div>
                                    </div>
                                </div>
                            </div>
                            
                            <div class="trade-detail">
                                <div class="detail-left">
                                    <div class="detail-icon amount-icon">💳</div>
                                    <div class="detail-content">
                                        <div class="detail-label">You Receive</div>
                                        <div class="detail-value amount-highlight"><strong>${seller_receives:.2f} USD</strong></div>
                                    </div>
                                </div>
                            </div>
                            
                            <div class="trade-detail">
                                <div class="detail-left">
                                    <div class="detail-icon service-icon">📋</div>
                                    <div class="detail-content">
                                        <div class="detail-label">Description</div>
                                        <div class="detail-value">{description[:100]}{'...' if len(description) > 100 else ''}</div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        
                        <div class="cta-section">
                            <a href="{invitation_link}" class="cta-button">
                                <span class="cta-text">Accept Trade Invitation</span>
                            </a>
                        </div>
                        
                        <div class="trust-indicators">
                            <div class="trust-item">
                                <span class="trust-number">99.8%</span>
                                <span class="trust-label">Success Rate</span>
                            </div>
                            <div class="trust-item">
                                <span class="trust-number">$2M+</span>
                                <span class="trust-label">Protected</span>
                            </div>
                            <div class="trust-item">
                                <span class="trust-number">24/7</span>
                                <span class="trust-label">Support</span>
                            </div>
                        </div>
                        
                        <div class="security-badge">
                            <p class="security-text">
                                <span class="security-icon">🔐</span>
                                <strong>Bank-Grade Security:</strong> Your funds are protected by our advanced escrow system. 
                                Payment is only released when both parties confirm completion.
                            </p>
                        </div>
                    </div>
                    
                    <div class="footer">
                        <p class="footer-brand">LockBay - Trusted Crypto Escrow</p>
                        <p class="footer-text">
                            This invitation was sent to {email} by {buyer_name}<br>
                            Securing digital transactions worldwide since 2024
                        </p>
                        
                        <div class="footer-links">
                            <a href="mailto:{Config.SUPPORT_EMAIL}">Support</a>
                            <a href="{Config.WEBAPP_URL}">Platform</a>
                            <a href="https://t.me/{Config.BOT_USERNAME}">Bot</a>
                        </div>
                        
                        <p class="footer-legal">
                            This email was sent by LockBay. Questions? Reply to this email or contact us at {Config.SUPPORT_EMAIL}
                        </p>
                    </div>
                </div>
            </body>
            </html>
            """

            text_content = f"""
Trade Invitation • #{escrow_id}

From: {buyer_name}
You earn: ${amount_usd:.2f} USD
Task: {description[:40]}{'...' if len(description) > 40 else ''}

Accept: {invitation_link}

Expires in 7 days. Funds secured.
            """

            # Send invitation email
            email_service = WelcomeEmailService()
            return await email_service.send_custom_email(
                to_email=email,
                subject=subject,
                html_content=html_content,
                text_content=text_content,
            )

        except Exception as e:
            logger.error(f"Failed to send email invitation to {email}: {e}")
            return False
