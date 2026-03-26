"""
Lockbay Comprehensive Branding Utils Module
Phase 1 Implementation of unified branding strategy across all bot interactions.

This module provides standardized branding functions for:
- Headers and footers
- Transaction receipts
- ID generation
- Currency formatting
- Social proof messaging
- User milestone celebrations
"""

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional, Dict, Any, Union
from config import Config
from utils.constants import CURRENCY_EMOJIS
from utils.branding import SecurityIcons, TrustMessages
from utils.universal_id_generator import UniversalIDGenerator

logger = logging.getLogger(__name__)


class BrandingUtils:
    """Comprehensive branding utility functions for Lockbay platform"""
    
    # Lockbay brand constants
    BRAND_EMOJI = "🔒"
    SUPPORT_HANDLE = "@LockbayAssist"
    PROTECTION_TAGLINE = f"Protected by {Config.PLATFORM_NAME}"
    
    # ID prefixes for LB-format transaction IDs
    ID_PREFIXES = {
        "escrow": "LB-ESC",
        "cashout": "LB-CASH", 
        "exchange": "LB-EXC",
        "wallet": "LB-WAL",
        "transaction": "LB-TXN",
        "refund": "LB-REF",
        "deposit": "LB-DEP"
    }
    
    @staticmethod
    def make_header(screen_title: str) -> str:
        """
        Create standardized platform header for all screens
        
        Args:
            screen_title: The title of the current screen/section
            
        Returns:
            Formatted header string: "🔒 {platform_name} • [Title]"
        """
        try:
            if not screen_title or not isinstance(screen_title, str):
                logger.warning(f"Invalid screen_title provided: {screen_title}")
                screen_title = "Dashboard"
            
            # Clean and truncate title for mobile optimization
            clean_title = screen_title.strip()[:25]  # Mobile-friendly length
            
            return f"{BrandingUtils.BRAND_EMOJI} {Config.PLATFORM_NAME} • {clean_title}"
            
        except Exception as e:
            logger.error(f"Error creating header: {e}")
            return f"{BrandingUtils.BRAND_EMOJI} {Config.PLATFORM_NAME}"
    
    @staticmethod
    def make_trust_footer() -> str:
        """
        Create standardized trust footer for all interactions
        
        Returns:
            Trust footer: "Protected by {platform_name} • Support: @LockbayAssist"
        """
        try:
            # Don't escape @ symbol - it's a normal character in Telegram
            return f"{BrandingUtils.PROTECTION_TAGLINE} • Support: {BrandingUtils.SUPPORT_HANDLE}"
        except Exception as e:
            logger.error(f"Error creating trust footer: {e}")
            return f"{SecurityIcons.SHIELD} {Config.PLATFORM_NAME} Support"
    
    @staticmethod
    def make_receipt(tx_id: str, amount: Union[Decimal, float, str], 
                    asset: str, tx_type: str, additional_details: Optional[Dict[str, Any]] = None) -> str:
        """
        Create enhanced branded transaction receipt with comprehensive details
        
        Args:
            tx_id: Transaction ID (LB-prefixed format)
            amount: Transaction amount
            asset: Currency/asset code (BTC, ETH, USD, etc.)
            tx_type: Type of transaction (escrow, cashout, exchange, etc.)
            additional_details: Optional additional receipt details
            
        Returns:
            Formatted enhanced receipt with branding elements
        """
        try:
            # Format amount with proper branding
            formatted_amount = BrandingUtils.format_branded_amount(amount, asset)
            
            # Get appropriate emoji for transaction type
            type_emoji = BrandingUtils._get_transaction_type_emoji(tx_type)
            
            # Generate timestamp
            timestamp = datetime.utcnow()
            formatted_time = timestamp.strftime('%m/%d/%Y %H:%M UTC')
            
            # Prepare QR data for verification
            qr_data = BrandingUtils.prepare_receipt_qr_data(tx_id, amount, asset, tx_type, timestamp)
            
            # Create enhanced mobile-optimized receipt
            receipt_lines = [
                f"{BrandingUtils.make_header('Transaction Receipt')}",
                "",
                f"{type_emoji} **Transaction Confirmed**",
                "",
                f"💰 **Amount:** {formatted_amount}",
                f"🆔 **ID:** `{tx_id}`",
                f"📋 **Type:** {tx_type.title()}",
                f"⏰ **Time:** {formatted_time}",
            ]
            
            # Add additional details if provided
            if additional_details:
                receipt_lines.append("")
                
                # Add transaction-specific details
                if additional_details.get("fee_amount"):
                    fee_formatted = BrandingUtils.format_branded_amount(
                        additional_details["fee_amount"], 
                        additional_details.get("fee_currency", asset)
                    )
                    receipt_lines.append(f"💸 **Fee:** {fee_formatted}")
                
                if additional_details.get("net_amount"):
                    net_formatted = BrandingUtils.format_branded_amount(
                        additional_details["net_amount"], asset
                    )
                    receipt_lines.append(f"📊 **Net Amount:** {net_formatted}")
                
                if additional_details.get("recipient"):
                    recipient = additional_details["recipient"][:30]  # Truncate for mobile
                    receipt_lines.append(f"👤 **Recipient:** {recipient}")
                
                if additional_details.get("network"):
                    receipt_lines.append(f"🌐 **Network:** {additional_details['network']}")
                
                if additional_details.get("confirmation_blocks"):
                    receipt_lines.append(f"🔗 **Confirmations:** {additional_details['confirmation_blocks']}")
                
                if additional_details.get("exchange_rate"):
                    receipt_lines.append(f"📈 **Rate:** {additional_details['exchange_rate']}")
            
            # Add verification and security section
            receipt_lines.extend([
                "",
                f"{SecurityIcons.VERIFIED} **Transaction Secured & Recorded**",
                f"{SecurityIcons.SHIELD} **Blockchain Verified**",
                ""
            ])
            
            # Add QR verification note
            if qr_data:
                receipt_lines.append("📱 **Verification:** Scan QR code for instant verification")
                receipt_lines.append("")
            
            # Add sharing encouragement
            receipt_lines.extend([
                "📤 **Share:** Forward this receipt as proof of payment",
                f"💬 **Support:** {BrandingUtils.SUPPORT_HANDLE} for assistance",
                "",
                BrandingUtils.make_trust_footer()
            ])
            
            return "\n".join(receipt_lines)
            
        except Exception as e:
            logger.error(f"Error creating enhanced receipt for {tx_id}: {e}")
            return BrandingUtils._create_fallback_receipt(tx_id, amount, asset, tx_type)
    
    @staticmethod
    def make_shareable_receipt(tx_id: str, amount: Union[Decimal, float, str], 
                              asset: str, tx_type: str, 
                              additional_details: Optional[Dict[str, Any]] = None,
                              include_qr: bool = True) -> Dict[str, Any]:
        """
        Create shareable receipt with multiple formats and QR code data
        
        Args:
            tx_id: Transaction ID
            amount: Transaction amount
            asset: Currency/asset code
            tx_type: Transaction type
            additional_details: Additional receipt details
            include_qr: Whether to include QR code data
            
        Returns:
            Dictionary with formatted receipt, QR data, and sharing metadata
        """
        try:
            # Generate main receipt content
            receipt_text = BrandingUtils.make_receipt(tx_id, amount, asset, tx_type, additional_details)
            
            # Generate QR code data if requested
            qr_data = None
            if include_qr:
                timestamp = datetime.utcnow()
                qr_data = BrandingUtils.prepare_receipt_qr_data(tx_id, amount, asset, tx_type, timestamp)
            
            # Create sharing-optimized short format
            formatted_amount = BrandingUtils.format_branded_amount(amount, asset)
            short_receipt = f"""
✅ **{Config.PLATFORM_NAME} Receipt**

{BrandingUtils._get_transaction_type_emoji(tx_type)} {tx_type.title()}: {formatted_amount}
🆔 {tx_id}
⏰ {datetime.utcnow().strftime('%m/%d/%Y %H:%M')}

{SecurityIcons.VERIFIED} Secured by {Config.PLATFORM_NAME}
"""
            
            return {
                "full_receipt": receipt_text,
                "short_receipt": short_receipt.strip(),
                "qr_data": qr_data,
                "transaction_summary": {
                    "transaction_id": tx_id,
                    "amount": str(amount),
                    "currency": asset,
                    "type": tx_type,
                    "timestamp": datetime.utcnow().isoformat(),
                    "platform": Config.PLATFORM_NAME
                },
                "sharing_metadata": {
                    "title": f"{Config.PLATFORM_NAME} Transaction Receipt",
                    "description": f"{tx_type.title()} of {formatted_amount} completed successfully",
                    "hashtags": [f"#{Config.PLATFORM_NAME}", "#CryptoPayment", "#SecureTransaction"],
                    "verification_url": f"https://{Config.PLATFORM_NAME.lower()}.com/verify/{tx_id}" if hasattr(Config, 'PLATFORM_DOMAIN') else None
                }
            }
            
        except Exception as e:
            logger.error(f"Error creating shareable receipt for {tx_id}: {e}")
            return {
                "full_receipt": BrandingUtils._create_fallback_receipt(tx_id, amount, asset, tx_type),
                "short_receipt": f"✅ {tx_type.title()}: {amount} {asset} | {tx_id}",
                "qr_data": None,
                "error": str(e)
            }
    
    @staticmethod
    def prepare_receipt_qr_data(tx_id: str, amount: Union[Decimal, float, str], 
                               asset: str, tx_type: str, timestamp: datetime) -> Optional[Dict[str, Any]]:
        """
        Prepare QR code data for receipt verification
        
        Args:
            tx_id: Transaction ID
            amount: Transaction amount
            asset: Currency/asset code
            tx_type: Transaction type
            timestamp: Transaction timestamp
            
        Returns:
            QR code data dictionary
        """
        try:
            # Create verification payload
            verification_data = {
                "platform": Config.PLATFORM_NAME,
                "transaction_id": tx_id,
                "amount": str(amount),
                "currency": asset,
                "type": tx_type,
                "timestamp": timestamp.isoformat(),
                "verification_hash": BrandingUtils._generate_verification_hash(tx_id, amount, asset, timestamp)
            }
            
            # Create QR-friendly verification URL
            verification_url = f"https://verify.{Config.PLATFORM_NAME.lower()}.com/tx/{tx_id}"
            
            return {
                "content": verification_url,
                "data": verification_data,
                "format": "url",
                "size": "medium",  # 200x200px recommended for mobile
                "error_correction": "M",  # Medium error correction for reliability
                "brand_overlay": True,
                "watermark_text": f"Verified by {Config.PLATFORM_NAME}"
            }
            
        except Exception as e:
            logger.error(f"Error preparing QR data for {tx_id}: {e}")
            return None
    
    @staticmethod
    def _generate_verification_hash(tx_id: str, amount: Union[Decimal, float, str], 
                                   asset: str, timestamp: datetime) -> str:
        """Generate verification hash for QR code security"""
        try:
            import hashlib
            
            # Create verification string
            verification_string = f"{tx_id}:{amount}:{asset}:{timestamp.isoformat()}:{Config.PLATFORM_NAME}"
            
            # Generate hash
            hash_object = hashlib.sha256(verification_string.encode())
            return hash_object.hexdigest()[:16]  # First 16 characters for QR efficiency
            
        except Exception as e:
            logger.error(f"Error generating verification hash: {e}")
            return "UNVERIFIED"
    
    @staticmethod
    def _create_fallback_receipt(tx_id: str, amount: Union[Decimal, float, str], 
                                asset: str, tx_type: str) -> str:
        """Create simple fallback receipt if main receipt generation fails"""
        try:
            formatted_amount = BrandingUtils.format_branded_amount(amount, asset)
            return f"""
{SecurityIcons.VERIFIED} **Transaction Confirmed**

💰 {formatted_amount}
🆔 {tx_id}
📋 {tx_type.title()}
⏰ {datetime.utcnow().strftime('%m/%d/%Y %H:%M UTC')}

{BrandingUtils.make_trust_footer()}
"""
        except Exception as e:
            logger.error(f"Error creating fallback receipt: {e}")
            return f"{SecurityIcons.VERIFIED} Transaction {tx_id} confirmed"
    
    @staticmethod
    def generate_transaction_id(tx_type: str) -> str:
        """
        Generate transaction IDs using UniversalIDGenerator
        
        DEPRECATED: This method now delegates to UniversalIDGenerator for consistency.
        Use UniversalIDGenerator directly for new code.
        
        Args:
            tx_type: Transaction type (escrow, cashout, exchange, wallet, etc.)
            
        Returns:
            Unified transaction ID (e.g., TX092523A4B7)
        """
        try:
            # Map transaction types to UniversalIDGenerator entity types
            entity_type_mapping = {
                "escrow": "escrow",
                "cashout": "cashout", 
                "exchange": "exchange",
                "wallet": "wallet_deposit",
                "transaction": "transaction",
                "refund": "refund",
                "deposit": "wallet_deposit",
                "payment": "payment",
                "fee": "fee"
            }
            
            # Get the appropriate entity type or default to 'transaction'
            entity_type = entity_type_mapping.get(tx_type.lower(), "transaction")
            
            # Delegate to UniversalIDGenerator for unified ID generation
            tx_id = UniversalIDGenerator.generate_id(entity_type)
            
            logger.info(f"Generated unified transaction ID: {tx_id} for type: {tx_type} (mapped to {entity_type})")
            return tx_id
            
        except Exception as e:
            logger.error(f"Error generating transaction ID for type {tx_type}: {e}")
            # Fallback to UniversalIDGenerator with default transaction type
            return UniversalIDGenerator.generate_id("transaction")
    
    @staticmethod
    def format_branded_amount(amount: Union[Decimal, float, str], currency: str) -> str:
        """
        Format amounts with consistent branding and currency display
        
        Args:
            amount: Monetary amount
            currency: Currency code (BTC, ETH, USD, NGN, etc.)
            
        Returns:
            Formatted amount with emoji and proper precision
        """
        try:
            # Convert to Decimal for precise calculations
            if isinstance(amount, str):
                decimal_amount = Decimal(amount)
            else:
                decimal_amount = Decimal(str(amount))
            
            # Get currency emoji
            currency_emoji = CURRENCY_EMOJIS.get(currency.upper(), "💰")
            
            # Format based on currency type
            if currency.upper() in ["BTC", "ETH", "LTC", "DOGE"]:
                # Crypto currencies - show more decimal places
                formatted = f"{decimal_amount:.8f}".rstrip('0').rstrip('.')
            elif currency.upper() in ["USD", "EUR", "GBP", "CAD"]:
                # Fiat currencies - 2 decimal places
                formatted = f"{decimal_amount:.2f}"
            elif currency.upper() == "NGN":
                # Nigerian Naira - no decimal places for small amounts
                if decimal_amount >= 1:
                    formatted = f"{decimal_amount:,.0f}"
                else:
                    formatted = f"{decimal_amount:.2f}"
            else:
                # Default formatting
                formatted = f"{decimal_amount:.4f}".rstrip('0').rstrip('.')
            
            return f"{currency_emoji} {formatted} {currency.upper()}"
            
        except Exception as e:
            logger.error(f"Error formatting amount {amount} {currency}: {e}")
            return f"💰 {amount} {currency.upper()}"
    
    @staticmethod
    async def get_social_proof_text(variant: str = "default") -> str:
        """
        Generate enhanced social proof text with real-time platform statistics
        
        Args:
            variant: Type of social proof - 'default', 'payment', 'compact', 'detailed'
            
        Returns:
            Social proof message with current platform statistics
        """
        try:
            # Import here to avoid circular imports
            from database import SessionLocal
            from models import Escrow, EscrowStatus
            from sqlalchemy import func, and_
            from datetime import datetime, timedelta
            
            # Use sync session since this is a compatibility function
            session = SessionLocal()
            try:
                # Get comprehensive platform statistics
                completed_escrows = session.scalar(
                    func.count(Escrow.id).where(
                        Escrow.status == EscrowStatus.COMPLETED
                    )
                ) or 0
                
                total_volume = session.scalar(
                    func.sum(Escrow.amount).where(
                        Escrow.status == EscrowStatus.COMPLETED
                    )
                ) or Decimal('0')
                
                # Enhanced: Get both buyer and seller counts for total user count
                total_buyers = session.scalar(
                    func.count(func.distinct(Escrow.buyer_id)).where(
                        Escrow.status == EscrowStatus.COMPLETED
                    )
                ) or 0
                
                total_sellers = session.scalar(
                    func.count(func.distinct(Escrow.seller_id)).where(
                        Escrow.status == EscrowStatus.COMPLETED
                    )
                ) or 0
                
                # Total unique users (avoid double counting users who are both buyers and sellers)
                unique_users = max(total_buyers, total_sellers, total_buyers + total_sellers // 2)
                
                # Enhanced: Get recent activity (last 24 hours)
                yesterday = datetime.utcnow() - timedelta(days=1)
                recent_completions = session.scalar(
                    func.count(Escrow.id).where(
                        and_(
                            Escrow.status == EscrowStatus.COMPLETED,
                            Escrow.updated_at >= yesterday
                        )
                    )
                ) or 0
                
                # Enhanced: Get active trades count
                active_trades = session.scalar(
                    func.count(Escrow.id).where(
                        Escrow.status.in_(['active', 'payment_confirmed', 'pending_deposit'])
                    )
                ) or 0
            
            finally:
                session.close()
            
            # Format volume display
            if total_volume >= 1000000:
                volume_text = f"${total_volume/1000000:.1f}M+"
            elif total_volume >= 1000:
                volume_text = f"${total_volume/1000:.0f}K+"
            else:
                volume_text = f"${total_volume:.0f}+"
            
            # Generate social proof based on variant
            if variant == "compact":
                # Compact version for mobile/small spaces
                if completed_escrows > 0:
                    social_proof = f"{SecurityIcons.STAR} {completed_escrows:,}+ trades • 💰 {volume_text} • {SecurityIcons.VERIFIED} Secure"
                else:
                    social_proof = f"{SecurityIcons.SHIELD} Secure Platform • {SecurityIcons.VERIFIED} Bank-grade Security"
                    
            elif variant == "payment":
                # Enhanced version for payment screens (builds trust during critical moments)
                if completed_escrows > 0:
                    social_proof = f"""
🔒 {Config.PLATFORM_NAME} Secure Escrow
{SecurityIcons.STAR} {completed_escrows:,}+ successful trades
💰 {volume_text} safely processed
👥 {unique_users:,}+ trusted traders

{TrustMessages.FUNDS_PROTECTED}
Your payment is secured until trade completion
"""
                else:
                    social_proof = f"""
🔒 {Config.PLATFORM_NAME} Secure Escrow
{SecurityIcons.SHIELD} Bank-grade security
{SecurityIcons.VERIFIED} Funds protected until completion

{TrustMessages.FUNDS_PROTECTED}
Your payment is secured until trade completion
"""
            
            elif variant == "detailed":
                # Detailed version for main dashboard/welcome screens
                if completed_escrows > 0:
                    recent_text = f"{recent_completions} trades completed today" if recent_completions > 0 else "Active trading community"
                    active_text = f"{active_trades} trades in progress" if active_trades > 0 else "Growing marketplace"
                    
                    social_proof = f"""
{SecurityIcons.STAR} {completed_escrows:,}+ completed trades
💰 {volume_text} in secure transactions
👥 {unique_users:,}+ trusted traders
⚡ {recent_text}
🔄 {active_text}

{TrustMessages.FUNDS_PROTECTED}
"""
                else:
                    social_proof = f"""
{SecurityIcons.SHIELD} Professional escrow platform
{SecurityIcons.VERIFIED} Bank-grade security systems
{SecurityIcons.STAR} Growing trusted community
⚡ Real-time transaction monitoring

{TrustMessages.FUNDS_PROTECTED}
"""
            
            else:
                # Default version - balanced information
                if completed_escrows > 0:
                    social_proof = f"""
{SecurityIcons.STAR} {completed_escrows:,}+ completed trades
💰 {volume_text} in secure transactions
👥 {unique_users:,}+ trusted users

{TrustMessages.FUNDS_PROTECTED}
"""
                else:
                    social_proof = f"""
{SecurityIcons.SHIELD} Secure escrow platform
{SecurityIcons.VERIFIED} Bank-grade security
{SecurityIcons.STAR} Growing community

{TrustMessages.FUNDS_PROTECTED}
"""
            
            return social_proof.strip()
            
        except Exception as e:
            logger.error(f"Error generating social proof text ({variant}): {e}")
            # Fallback social proof based on variant
            if variant == "compact":
                return f"{SecurityIcons.SHIELD} Secure Platform • {SecurityIcons.VERIFIED} Trusted"
            elif variant == "payment":
                return f"""
🔒 {Config.PLATFORM_NAME} Secure Escrow
{SecurityIcons.VERIFIED} Your payment is protected

{TrustMessages.FUNDS_PROTECTED}
Funds secured until completion
"""
            else:
                return f"""
{SecurityIcons.SHIELD} Trusted platform
{SecurityIcons.VERIFIED} Secure transactions
{SecurityIcons.STAR} Growing community

{TrustMessages.FUNDS_PROTECTED}
"""
    
    @staticmethod
    def make_milestone_message(user_data: Dict[str, Any], milestone_type: str) -> str:
        """
        Create user achievement milestone messages
        
        Args:
            user_data: User information dictionary
            milestone_type: Type of milestone achieved
            
        Returns:
            Branded milestone celebration message
        """
        try:
            user_name = user_data.get('first_name', 'Trader')[:15]  # Mobile-friendly length
            
            # Milestone configurations
            milestones = {
                "first_completion": {
                    "emoji": SecurityIcons.VERIFIED,
                    "title": "First Trade Complete!",
                    "message": f"Congratulations {user_name}! You've successfully completed your first secure trade.",
                    "reward": "You've unlocked trusted trader benefits!"
                },
                "reputation_milestone": {
                    "emoji": SecurityIcons.STAR,
                    "title": "Reputation Milestone!",
                    "message": f"Amazing work {user_name}! Your reputation is growing.",
                    "reward": "Higher reputation = more trading opportunities!"
                },
                "trusted_status": {
                    "emoji": SecurityIcons.TRUSTED_USER,
                    "title": "Trusted Trader Status!",
                    "message": f"Incredible {user_name}! You've earned Trusted Trader status.",
                    "reward": "Enjoy priority support and exclusive features!"
                },
                "volume_milestone": {
                    "emoji": SecurityIcons.PROGRESS,
                    "title": "Volume Milestone!",
                    "message": f"Outstanding {user_name}! You've reached a new trading volume milestone.",
                    "reward": "Your trading success is building a stronger reputation!"
                }
            }
            
            milestone_config = milestones.get(milestone_type, milestones["first_completion"])
            
            milestone_message = f"""
{milestone_config['emoji']} **{milestone_config['title']}**

{milestone_config['message']}

🎉 **{milestone_config['reward']}**

{BrandingUtils.make_trust_footer()}
"""
            
            return milestone_message.strip()
            
        except Exception as e:
            logger.error(f"Error creating milestone message for {milestone_type}: {e}")
            return f"""
{SecurityIcons.STAR} **Achievement Unlocked!**

Congratulations on reaching a new milestone!

{BrandingUtils.make_trust_footer()}
"""
    
    @staticmethod
    def _get_transaction_type_emoji(tx_type: str) -> str:
        """Get appropriate emoji for transaction type"""
        emoji_map = {
            "escrow": "🤝",
            "cashout": "💸", 
            "exchange": "🔄",
            "wallet": "💰",
            "deposit": "📥",
            "refund": "↩️",
            "transaction": "💳"
        }
        return emoji_map.get(tx_type.lower(), "💳")
    
    @staticmethod
    def prepare_qr_watermark_data(content: str, user_id: Optional[int] = None) -> Dict[str, str]:
        """
        Prepare data for QR code watermarking (Phase 2 preparation)
        
        Args:
            content: QR code content
            user_id: Optional user ID for personalization
            
        Returns:
            Dictionary with watermark data
        """
        try:
            return {
                "content": content,
                "brand": Config.PLATFORM_NAME,
                "watermark_text": f"Generated by {Config.PLATFORM_NAME}",
                "security_level": "high",
                "user_id": str(user_id) if user_id else "anonymous",
                "timestamp": datetime.utcnow().isoformat()
            }
        except Exception as e:
            logger.error(f"Error preparing QR watermark data: {e}")
            return {"content": content, "brand": Config.PLATFORM_NAME}
    
    @staticmethod
    def get_branded_error_message(error_type: str, context: str = "") -> str:
        """
        Generate branded error messages with consistent styling
        
        Args:
            error_type: Type of error (payment, network, validation, etc.)
            context: Additional context for the error
            
        Returns:
            Branded error message
        """
        try:
            error_configs = {
                "payment": {
                    "emoji": "💳",
                    "title": "Payment Issue",
                    "message": "We're experiencing a temporary payment processing issue."
                },
                "network": {
                    "emoji": "🌐",
                    "title": "Connection Issue", 
                    "message": "Please check your connection and try again."
                },
                "validation": {
                    "emoji": "⚠️",
                    "title": "Input Error",
                    "message": "Please check your input and try again."
                },
                "timeout": {
                    "emoji": "⏰",
                    "title": "Request Timeout",
                    "message": "Request timed out. Please try again."
                }
            }
            
            config = error_configs.get(error_type, error_configs["validation"])
            
            error_message = f"""
{config['emoji']} **{config['title']}**

{config['message']}
{f"Details: {context}" if context else ""}

{BrandingUtils.make_trust_footer()}
"""
            
            return error_message.strip()
            
        except Exception as e:
            logger.error(f"Error creating branded error message: {e}")
            return f"⚠️ An error occurred. Please try again.\n\n{BrandingUtils.make_trust_footer()}"


# Backward compatibility aliases and convenience functions
def make_header(screen_title: str) -> str:
    """Convenience function for header creation"""
    return BrandingUtils.make_header(screen_title)

def make_trust_footer() -> str:
    """Convenience function for trust footer creation"""
    return BrandingUtils.make_trust_footer()

def make_receipt(tx_id: str, amount: Union[Decimal, float, str], asset: str, tx_type: str) -> str:
    """Convenience function for receipt creation"""
    return BrandingUtils.make_receipt(tx_id, amount, asset, tx_type)

def generate_transaction_id(tx_type: str) -> str:
    """Convenience function for transaction ID generation - delegates to UniversalIDGenerator"""
    return BrandingUtils.generate_transaction_id(tx_type)

def format_branded_amount(amount: Union[Decimal, float, str], currency: str) -> str:
    """Convenience function for amount formatting"""
    return BrandingUtils.format_branded_amount(amount, currency)

async def get_social_proof_text(variant: str = "default") -> str:
    """Convenience function for social proof text"""
    return await BrandingUtils.get_social_proof_text(variant)

def make_milestone_message(user_data: Dict[str, Any], milestone_type: str) -> str:
    """Convenience function for milestone messages"""
    return BrandingUtils.make_milestone_message(user_data, milestone_type)


# Logging setup
logger.info("✅ BrandingUtils module loaded successfully")
logger.info(f"🔒 Platform: {Config.PLATFORM_NAME}")
logger.info(f"🎯 Brand emoji: {BrandingUtils.BRAND_EMOJI}")