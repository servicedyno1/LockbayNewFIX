"""Withdrawal Notification Service for sending completion notifications to customers"""

import logging
from typing import Optional, Dict, Any
from telegram import Bot
from telegram.error import TelegramError

from services.email import EmailService
from config import Config

logger = logging.getLogger(__name__)


class WithdrawalNotificationService:
    """Send withdrawal completion notifications via Telegram and email"""
    
    # Blockchain explorer URLs for supported cryptocurrencies
    EXPLORER_URLS = {
        # Bitcoin variants
        'BTC': 'https://blockchair.com/bitcoin/transaction/',
        'XXBT': 'https://blockchair.com/bitcoin/transaction/',  # Kraken BTC
        'XBT': 'https://blockchair.com/bitcoin/transaction/',  # Kraken BTC
        
        # Ethereum variants  
        'ETH': 'https://etherscan.io/tx/',
        'XETH': 'https://etherscan.io/tx/',  # Kraken ETH
        
        # Litecoin variants
        'LTC': 'https://blockchair.com/litecoin/transaction/',
        'XLTC': 'https://blockchair.com/litecoin/transaction/',  # Kraken LTC
        
        # Dogecoin variants
        'DOGE': 'https://blockchair.com/dogecoin/transaction/',
        'XXDG': 'https://blockchair.com/dogecoin/transaction/',  # Kraken DOGE
        
        # Tron variants
        'TRX': 'https://tronscan.org/#/transaction/',
        'XTRX': 'https://tronscan.org/#/transaction/',  # Kraken TRX
        
        # USDT variants (network-specific)
        'USDT': 'https://etherscan.io/tx/',  # Default USDT (Ethereum)
        'USDT-ERC20': 'https://etherscan.io/tx/',  # USDT on Ethereum
        'USDT-TRC20': 'https://tronscan.org/#/transaction/',  # USDT on Tron
        
        # Bitcoin Cash
        'BCH': 'https://blockchair.com/bitcoin-cash/transaction/',
        'XBCH': 'https://blockchair.com/bitcoin-cash/transaction/',  # Kraken BCH
        
        # Binance Smart Chain
        'BNB': 'https://bscscan.com/tx/',
        'BSC': 'https://bscscan.com/tx/',
        
        # Monero (optional - privacy coin)
        'XMR': 'https://xmrchain.net/tx/'
    }
    
    def __init__(self):
        self.bot = Bot(token=Config.BOT_TOKEN)
        self.email_service = EmailService()
    
    async def send_withdrawal_completion_notification(
        self,
        user_id: int,
        cashout_id: str,
        amount: float,
        currency: str,
        blockchain_hash: str,
        user_email: Optional[str] = None,
        usd_amount: Optional[float] = None,
        destination_address: Optional[str] = None,
        pending_funding: bool = False
    ) -> bool:
        """Send both Telegram and email notifications for withdrawal completion with enhanced content"""
        
        try:
            # Send enhanced Telegram notification
            telegram_success = await self._send_telegram_notification(
                user_id, cashout_id, amount, currency, blockchain_hash, 
                usd_amount, destination_address, pending_funding
            )
            
            # Send enhanced email notification if email is available
            email_success = True  # Default to True if no email
            if user_email:
                email_success = await self._send_email_notification(
                    user_email, cashout_id, amount, currency, blockchain_hash,
                    usd_amount, destination_address, pending_funding
                )
            
            # Consider successful if at least one notification was sent
            overall_success = telegram_success or email_success
            
            if overall_success:
                logger.info(f"Enhanced withdrawal completion notification sent for {cashout_id} (Telegram: {telegram_success}, Email: {email_success})")
            else:
                logger.error(f"Failed to send any notification for {cashout_id}")
            
            return overall_success
            
        except Exception as e:
            logger.error(f"Error sending withdrawal completion notification for {cashout_id}: {str(e)}")
            return False
    
    async def _send_telegram_notification(
        self,
        user_id: int,
        cashout_id: str,
        amount: float,
        currency: str,
        blockchain_hash: str,
        usd_amount: Optional[float] = None,
        destination_address: Optional[str] = None,
        pending_funding: bool = False
    ) -> bool:
        """Send enhanced Telegram notification with rich content matching NGN quality"""
        
        try:
            # Format crypto amount with appropriate decimal places
            if currency in ['BTC', 'ETH', 'LTC']:
                amount_str = f"{amount:.6f}"
            elif currency in ['USDT', 'USDT-TRC20']:
                amount_str = f"{amount:.2f}"
            else:
                amount_str = f"{amount:.4f}"
            
            # Format USD amount
            
            # Truncate hash for professional display
            f"{blockchain_hash[:8]}...{blockchain_hash[-4:]}" if len(blockchain_hash) > 12 else blockchain_hash
            
            # Truncate destination address for security
            if destination_address:
                if len(destination_address) > 20:
                    dest_display = f"{destination_address[:10]}...{destination_address[-6:]}"
                else:
                    dest_display = destination_address
            else:
                dest_display = "External Wallet"
            
            # Get explorer URL - but check if hash is a real blockchain txid
            explorer_url = self.EXPLORER_URLS.get(currency, '')
            
            # CRITICAL FIX: Kraken returns refid (internal ID) not blockchain txid
            # Real blockchain hashes are typically 64 chars hex or start with 0x
            is_real_blockchain_hash = (
                len(blockchain_hash) >= 64 or  # Bitcoin-style 64 char hex
                blockchain_hash.startswith('0x')  # Ethereum-style with 0x prefix
            )
            
            # Ultra-minimal message format
            # Format USD amount with cents precision for "net" display
            usd_net = f"${usd_amount:.2f}" if usd_amount else ""
            
            if pending_funding:
                # Special message for funding scenarios (still processing - NOT completed)
                message = (
                    "⏳ <b>Crypto Cashout Processing</b>\n\n"
                    f"🪙 {amount_str} {currency}\n"
                    f"💰 {usd_net} net\n"
                    f"📍 {dest_display}\n"
                    f"🆔 {cashout_id}\n\n"
                    "⏰ Processing • Will arrive in 10-30 min\n"
                    "📧 Email when sent\n\n"
                    "🔒 LockBay"
                )
            else:
                # Normal successful processing message (ACTUALLY sent to blockchain)
                message = (
                    "✅ <b>Crypto Sent Successfully!</b>\n\n"
                    f"🪙 {amount_str} {currency}\n"
                    f"💰 {usd_net} net\n"
                    f"📍 {dest_display}\n"
                    f"🆔 {cashout_id}\n\n"
                    "⏰ ~5 min • 📧 Details in email\n\n"
                    "🔒 LockBay"
                )
            
            # Add blockchain explorer link ONLY if we have a real blockchain txid
            if explorer_url and not pending_funding and is_real_blockchain_hash:
                message += f"\n\n🔍 <a href='{explorer_url}{blockchain_hash}'>View on Blockchain</a>"
            elif not pending_funding and not is_real_blockchain_hash:
                # Show processing message when we only have Kraken refid
                message += "\n\n⏳ <i>Blockchain hash available soon</i>"
            
            # Send message with professional formatting
            await self.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode='HTML',
                disable_web_page_preview=True  # Keep message clean
            )
            
            return True
            
        except TelegramError as e:
            logger.error(f"Telegram error sending enhanced withdrawal notification to {user_id}: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Error sending enhanced Telegram withdrawal notification: {str(e)}")
            return False
    
    async def _send_email_notification(
        self,
        user_email: str,
        cashout_id: str,
        amount: float,
        currency: str,
        blockchain_hash: str,
        usd_amount: Optional[float] = None,
        destination_address: Optional[str] = None,
        pending_funding: bool = False
    ) -> bool:
        """Send enhanced email notification with professional templates matching NGN quality"""
        
        try:
            # Format crypto amount
            if currency in ['BTC', 'ETH', 'LTC']:
                amount_str = f"{amount:.6f}"
            elif currency in ['USDT', 'USDT-TRC20']:
                amount_str = f"{amount:.2f}"
            else:
                amount_str = f"{amount:.4f}"
            
            # Format USD amount for display
            usd_display = f" (${usd_amount:.2f})" if usd_amount else ""
            
            # Truncate destination address for security
            if destination_address:
                if len(destination_address) > 30:
                    dest_display = f"{destination_address[:15]}...{destination_address[-10:]}"
                else:
                    dest_display = destination_address
            else:
                dest_display = "External Wallet"
            
            # CRITICAL FIX: Check if hash is real blockchain txid (not Kraken refid)
            is_real_blockchain_hash = (
                len(blockchain_hash) >= 64 or  # Bitcoin-style 64 char hex
                blockchain_hash.startswith('0x')  # Ethereum-style with 0x prefix
            )
            
            # Get explorer URL and create link
            explorer_url = self.EXPLORER_URLS.get(currency, '')
            if explorer_url and not pending_funding and is_real_blockchain_hash:
                # Real blockchain hash - show working explorer link
                explorer_link = f'<a href="{explorer_url}{blockchain_hash}" style="color: #007bff;">View on Blockchain Explorer</a>'
            elif not pending_funding and not is_real_blockchain_hash:
                # Kraken refid (not real blockchain hash) - show processing message
                explorer_link = '<span style="color: #ffc107;">⏳ Blockchain hash available soon</span>'
            else:
                explorer_link = 'Processing...' if pending_funding else 'N/A'
            
            # Dynamic subject and content based on scenario
            if pending_funding:
                subject = f"✅ Processing: {amount_str} {currency}{usd_display}"
                status_title = "Crypto Cashout Processing"
                status_desc = "Your cryptocurrency withdrawal is being processed and will be sent shortly"
                completion_status = "Processing (10-30 minutes)"
                color_scheme = "#ffc107"  # Yellow/warning color for processing
            else:
                subject = f"✅ Sent: {amount_str} {currency}{usd_display}"
                status_title = "Crypto Withdrawal Complete"
                status_desc = "Your cryptocurrency withdrawal has been successfully processed and sent"
                completion_status = "Complete - Sent to blockchain"
                color_scheme = "#28a745"  # Green for success
            
            # Professional email body matching NGN template quality
            email_body = f"""
            <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background-color: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 8px rgba(0,0,0,0.1);">
                    <div style="text-align: center; margin-bottom: 30px;">
                        <h2 style="color: {color_scheme}; margin: 0; font-size: 28px;">✅ {status_title}</h2>
                        <p style="color: #6c757d; margin: 10px 0 0 0; font-size: 16px;">{status_desc}</p>
                    </div>
                    
                    <div style="background-color: #f8f9fa; padding: 25px; border-radius: 10px; margin-bottom: 25px;">
                        <h3 style="color: #343a40; margin: 0 0 20px 0; font-size: 20px;">💰 Transaction Details</h3>
                        <table style="width: 100%; border-collapse: collapse;">
                            <tr>
                                <td style="padding: 12px 0; font-weight: 600; color: #495057; width: 140px;">Amount:</td>
                                <td style="padding: 12px 0; color: #212529; font-size: 16px; font-weight: 500;">{amount_str} {currency}{usd_display}</td>
                            </tr>
                            <tr>
                                <td style="padding: 12px 0; font-weight: 600; color: #495057;">Destination:</td>
                                <td style="padding: 12px 0; color: #212529; font-family: 'Monaco', monospace; font-size: 14px; word-break: break-all;">{dest_display}</td>
                            </tr>
                            <tr>
                                <td style="padding: 12px 0; font-weight: 600; color: #495057;">Reference:</td>
                                <td style="padding: 12px 0; color: #212529; font-family: 'Monaco', monospace; font-size: 14px;">{cashout_id}</td>
                            </tr>
                            <tr>
                                <td style="padding: 12px 0; font-weight: 600; color: #495057;">Transaction Hash:</td>
                                <td style="padding: 12px 0; color: #212529; font-family: 'Monaco', monospace; font-size: 14px; word-break: break-all;">{blockchain_hash}</td>
                            </tr>
                            <tr>
                                <td style="padding: 12px 0; font-weight: 600; color: #495057;">Status:</td>
                                <td style="padding: 12px 0; color: {color_scheme}; font-weight: 600;">{completion_status}</td>
                            </tr>
                            <tr>
                                <td style="padding: 12px 0; font-weight: 600; color: #495057;">Blockchain:</td>
                                <td style="padding: 12px 0;">{explorer_link}</td>
                            </tr>
                        </table>
                    </div>
                    
                    <div style="background-color: #e7f3ff; padding: 20px; border-radius: 10px; border-left: 4px solid #007bff; margin-bottom: 25px;">
                        <h4 style="color: #0056b3; margin: 0 0 12px 0; font-size: 16px;">📋 What This Means</h4>
                        <p style="color: #495057; margin: 0; line-height: 1.6; font-size: 15px;">
                            {'Your ' + currency + ' withdrawal is being processed by our secure systems. The transaction will be broadcast to the blockchain network shortly and your funds will arrive at the destination address within 10-30 minutes.' if pending_funding else 
                            'Your ' + currency + ' withdrawal has been successfully broadcast to the blockchain network. The transaction is now permanently recorded and your funds have been sent to your specified wallet address. Confirmation times vary by network congestion.'}
                        </p>
                    </div>
                    
                    <div style="background-color: #f8f9fa; padding: 20px; border-radius: 10px; border: 1px solid #e9ecef;">
                        <h4 style="color: #495057; margin: 0 0 12px 0; font-size: 16px;">🔒 Security Notice</h4>
                        <p style="color: #6c757d; margin: 0; line-height: 1.5; font-size: 14px;">
                            This transaction was initiated from your verified LockBay account. If you did not request this withdrawal, please contact our support team immediately. Always verify transaction details on the blockchain explorer using the link above.
                        </p>
                    </div>
                    
                    <div style="text-align: center; margin-top: 30px; padding-top: 20px; border-top: 1px solid #dee2e6;">
                        <p style="color: #6c757d; margin: 0 0 10px 0; font-size: 14px;">
                            Thank you for using <strong>LockBay</strong> • Secure Cryptocurrency Trading Platform
                        </p>
                        <p style="color: #adb5bd; margin: 0; font-size: 12px;">
                            Need help? Contact us at support@lockbay.com
                        </p>
                    </div>
                </div>
            </div>
            """
            
            # Send email
            success = self.email_service.send_email(
                to_email=user_email,
                subject=subject,
                html_content=email_body
            )
            
            if success:
                logger.info(f"Withdrawal completion email sent to {user_email} for {cashout_id}")
            else:
                logger.error(f"Failed to send withdrawal completion email to {user_email}")
            
            return success
            
        except Exception as e:
            logger.error(f"Error sending withdrawal completion email: {str(e)}")
            return False
    
    def get_explorer_url(self, currency: str, tx_hash: str) -> Optional[str]:
        """Get blockchain explorer URL for a transaction"""
        base_url = self.EXPLORER_URLS.get(currency)
        if base_url:
            return f"{base_url}{tx_hash}"
        return None
    
    async def send_test_notification(
        self,
        user_id: int,
        user_email: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send test notification for testing purposes"""
        
        # Test data
        test_data = {
            'cashout_id': 'TEST_240907_001',
            'amount': 0.05,
            'currency': 'LTC',
            'blockchain_hash': '8a7b9c2d1e3f4a5b6c7d8e9f0123456789abcdef0123456789abcdef01234567'
        }
        
        try:
            success = await self.send_withdrawal_completion_notification(
                user_id=user_id,
                cashout_id=test_data['cashout_id'],
                amount=test_data['amount'],
                currency=test_data['currency'],
                blockchain_hash=test_data['blockchain_hash'],
                user_email=user_email
            )
            
            return {
                'success': success,
                'test_data': test_data,
                'message': 'Test notification sent successfully' if success else 'Test notification failed'
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'message': 'Test notification failed with error'
            }


# Global instance for use across the application
withdrawal_notification = WithdrawalNotificationService()