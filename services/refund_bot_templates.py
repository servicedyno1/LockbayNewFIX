"""
Refund Bot Templates Service
Comprehensive Telegram bot notification templates with inline keyboards for all refund scenarios
"""

import logging
from typing import Dict, Any, List
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


class RefundBotTemplates:
    """
    Comprehensive bot template generator for all refund notification scenarios
    """
    
    def __init__(self):
        self.emoji_map = {
            "success": "✅",
            "processing": "🔄", 
            "warning": "⚠️",
            "error": "❌",
            "money": "💰",
            "clock": "🕐",
            "shield": "🛡️",
            "tools": "🔧",
            "scales": "⚖️",
            "fire": "🔥",
            "info": "ℹ️",
            "rocket": "🚀",
            "heart": "❤️",
            "thumbs_up": "👍",
            "bell": "🔔"
        }
    
    def generate_bot_content(self, template_id: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate complete bot notification content for a given template and context
        
        Args:
            template_id: Template identifier
            context: Template variables and data
            
        Returns:
            Dictionary with message text and inline keyboard
        """
        try:
            template_method = getattr(self, f"_generate_{template_id}", None)
            if not template_method:
                logger.warning(f"⚠️ No bot template method found for {template_id}, using fallback")
                return self._generate_generic_refund_template(context)
            
            return template_method(context)
            
        except Exception as e:
            logger.error(f"❌ Error generating bot template {template_id}: {e}")
            return self._generate_error_fallback_template(context, template_id, str(e))
    
    def _generate_cashout_failed_refund(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate bot template for failed cashout refunds"""
        refund = context["refund"]
        user = context["user"]
        context["platform"]
        context.get("cashout", {})
        
        # Create compact, mobile-friendly message
        message = f"""{self.emoji_map['error']} Cashout Failed - Refund Processed

Hello {user['first_name']}! 

Your cashout request couldn't be completed, but don't worry - your funds are safe and have been automatically refunded to your wallet.

{self.emoji_map['money']} Refund Amount: `{refund['formatted_amount']}`
{self.emoji_map['info']} Refund ID: `{refund['refund_id']}`
{self.emoji_map['clock']} Processed: {self._format_datetime_for_bot(refund['created_at'])}

What happened?
The cashout failed due to technical issues or insufficient funds in our processing systems. Your money was never at risk.

Next Steps:
✅ Funds are back in your wallet
{self.emoji_map['rocket']} You can try cashing out again
{self.emoji_map['bell']} Contact support if you need help

The refund is available for immediate use!"""
        
        # Create mobile-optimized inline keyboard
        keyboard = [
            [
                InlineKeyboardButton(f"{self.emoji_map['money']} Check Wallet", callback_data=f"view_wallet_{user['id']}"),
                InlineKeyboardButton(f"{self.emoji_map['processing']} Try Cashout", callback_data="start_cashout")
            ],
            [
                InlineKeyboardButton("📊 Transaction History", callback_data=f"view_transactions_{user['id']}"),
                InlineKeyboardButton("💬 Contact Support", callback_data="start_support_chat")
            ],
            [
                InlineKeyboardButton(f"{self.emoji_map['thumbs_up']} Got it!", callback_data=f"confirm_notification_{refund['refund_id']}")
            ]
        ]
        
        return {
            "message": message,
            "keyboard": InlineKeyboardMarkup(keyboard),
            "parse_mode": "Markdown"
        }
    
    def _generate_escrow_timeout_refund(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate bot template for escrow timeout refunds"""
        refund = context["refund"]
        user = context["user"]
        context["platform"]
        escrow = context.get("escrow", {})
        
        message = f"""{self.emoji_map['clock']} Escrow Timeout - Refund Issued

Hi {user['first_name']}!

Your escrow trade expired due to timeout. We've automatically processed a full refund to protect your interests.

{self.emoji_map['money']} Refund Amount: `{refund['formatted_amount']}`
{self.emoji_map['shield']} Trade ID: `{escrow.get('escrow_id', 'N/A')}`
{self.emoji_map['info']} Item: {escrow.get('item_name', 'Digital Item')}
{self.emoji_map['clock']} Refunded: {self._format_datetime_for_bot(refund['created_at'])}

Why this happened:
Trades have time limits for fairness. When not completed within the timeframe, we automatically refund buyers to protect their funds.

Your Protection:
{self.emoji_map['shield']} Funds were never at risk
{self.emoji_map['success']} Full refund processed
{self.emoji_map['rocket']} Available for immediate use

You can start a new escrow trade anytime!"""
        
        keyboard = [
            [
                InlineKeyboardButton(f"{self.emoji_map['shield']} New Escrow", callback_data="start_escrow"),
                InlineKeyboardButton(f"{self.emoji_map['money']} Check Wallet", callback_data=f"view_wallet_{user['id']}")
            ],
            [
                InlineKeyboardButton("📋 View Trade History", callback_data=f"view_escrows_{user['id']}"),
                InlineKeyboardButton("💡 Trading Tips", callback_data="escrow_tips")
            ],
            [
                InlineKeyboardButton(f"{self.emoji_map['thumbs_up']} Understood", callback_data=f"confirm_notification_{refund['refund_id']}")
            ]
        ]
        
        return {
            "message": message,
            "keyboard": InlineKeyboardMarkup(keyboard),
            "parse_mode": "Markdown"
        }
    
    def _generate_dispute_resolution_refund(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate bot template for dispute resolution refunds"""
        refund = context["refund"]
        user = context["user"]
        context["platform"]
        dispute_details = context.get("dispute_details", {})
        
        message = f"""{self.emoji_map['scales']} Dispute Resolved - You Won!

Excellent news, {user['first_name']}!

The dispute for your trade has been resolved in your favor. We've processed a full refund as per our decision.

{self.emoji_map['success']} **Resolution:** Buyer Favor (Full Refund)
{self.emoji_map['money']} **Amount Refunded:** `{refund['formatted_amount']}`
{self.emoji_map['scales']} **Dispute ID:** `{dispute_details.get('dispute_id', 'N/A')}`
{self.emoji_map['clock']} Resolved: {self._format_datetime_for_bot(refund['created_at'])}

What this means:
After careful review of all evidence, our team determined the trade terms weren't fulfilled as agreed. You're entitled to a full refund.

Your Refund:
{self.emoji_map['success']} Automatically added to wallet
{self.emoji_map['rocket']} Ready for immediate use
{self.emoji_map['shield']} Fair resolution process complete

Thank you for your patience during the review process!"""
        
        keyboard = [
            [
                InlineKeyboardButton(f"{self.emoji_map['money']} View Wallet", callback_data=f"view_wallet_{user['id']}"),
                InlineKeyboardButton("📊 Transaction History", callback_data=f"view_transactions_{user['id']}")
            ],
            [
                InlineKeyboardButton(f"{self.emoji_map['scales']} Dispute Details", callback_data=f"view_dispute_{dispute_details.get('dispute_id', '')}"),
                InlineKeyboardButton("📋 Resolution Report", callback_data=f"dispute_report_{refund['refund_id']}")
            ],
            [
                InlineKeyboardButton(f"{self.emoji_map['heart']} Thank You!", callback_data=f"confirm_notification_{refund['refund_id']}")
            ]
        ]
        
        return {
            "message": message,
            "keyboard": InlineKeyboardMarkup(keyboard),
            "parse_mode": "Markdown"
        }
    
    def _generate_post_timeout_payment_refund(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate bot template for post-timeout payment refunds"""
        refund = context["refund"]
        user = context["user"]
        context["platform"]
        payment_details = context.get("payment_details", {})
        
        message = f"""{self.emoji_map['fire']} URGENT: Post-Timeout Payment Refund

{user['first_name']}, we detected an issue with your payment timing!

Your payment arrived **after** the order timeout period expired. For your protection, we've automatically processed a full refund.

{self.emoji_map['warning']} **Payment Issue:** Received after timeout
{self.emoji_map['money']} **Amount Refunded:** `{refund['formatted_amount']}`
{self.emoji_map['clock']} **Refund Date:** {self._format_datetime_for_bot(refund['created_at'])}
{self.emoji_map['shield']} **Status:** Automatically Protected

**Timeline Issue:**
• Your payment: {payment_details.get('payment_time', 'Recently')}
• Order expired: {payment_details.get('timeout_time', 'Earlier')}
• Gap detected: Automatic refund triggered

Your Protection:
{self.emoji_map['success']} Funds safely returned
{self.emoji_map['rocket']} No money lost
{self.emoji_map['shield']} System protected you automatically

Why this happened:
Network delays, payment processing delays, or wallet sync issues can cause timing problems. Our systems catch these automatically!

**Prevention Tips:**
• Pay within the time window
• Check confirmations before timeout
• Monitor order timers
• Contact support for delays"""
        
        keyboard = [
            [
                InlineKeyboardButton(f"{self.emoji_map['processing']} Create New Order", callback_data="start_exchange"),
                InlineKeyboardButton(f"{self.emoji_map['money']} Check Wallet", callback_data=f"view_wallet_{user['id']}")
            ],
            [
                InlineKeyboardButton("📚 Prevention Tips", callback_data="payment_timing_tips"),
                InlineKeyboardButton("💬 Report Issue", callback_data="start_support_chat")
            ],
            [
                InlineKeyboardButton(f"{self.emoji_map['thumbs_up']} I Understand", callback_data=f"confirm_notification_{refund['refund_id']}")
            ]
        ]
        
        return {
            "message": message,
            "keyboard": InlineKeyboardMarkup(keyboard),
            "parse_mode": "Markdown"
        }
    
    def _generate_system_error_refund(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate bot template for system error refunds"""
        refund = context["refund"]
        user = context["user"]
        context["platform"]
        
        message = f"""{self.emoji_map['tools']} **System Error - We're Making It Right**

{user['first_name']}, we sincerely apologize!

A technical error occurred during your transaction. We take full responsibility and have immediately processed your refund.

{self.emoji_map['error']} **Issue:** System Processing Error
{self.emoji_map['money']} **Refund:** `{refund['formatted_amount']}`
{self.emoji_map['success']} **Status:** ✅ Refund Completed
{self.emoji_map['clock']} Processed: {self._format_datetime_for_bot(refund['created_at'])}

**Our Response:**
{self.emoji_map['success']} Immediate refund processed
{self.emoji_map['tools']} Root cause analysis started
{self.emoji_map['shield']} System improvements underway
{self.emoji_map['bell']} Enhanced monitoring active

**Your Funds:**
{self.emoji_map['success']} Safely returned to wallet
{self.emoji_map['rocket']} Available immediately
{self.emoji_map['shield']} Never at risk

**Making it Right:**
We'd like to discuss potential compensation for this inconvenience. Please contact our support team with this refund ID as reference.

We appreciate your patience and continued trust!"""
        
        keyboard = [
            [
                InlineKeyboardButton(f"{self.emoji_map['money']} Check Wallet", callback_data=f"view_wallet_{user['id']}"),
                InlineKeyboardButton("💬 Contact Support", callback_data="start_support_chat")
            ],
            [
                InlineKeyboardButton("🎁 Discuss Compensation", callback_data=f"compensation_inquiry_{refund['refund_id']}"),
                InlineKeyboardButton("📊 View Transactions", callback_data=f"view_transactions_{user['id']}")
            ],
            [
                InlineKeyboardButton("📋 Error Report", callback_data=f"error_report_{refund['refund_id']}"),
                InlineKeyboardButton(f"{self.emoji_map['heart']} Thank You", callback_data=f"confirm_notification_{refund['refund_id']}")
            ]
        ]
        
        return {
            "message": message,
            "keyboard": InlineKeyboardMarkup(keyboard),
            "parse_mode": "Markdown"
        }
    
    def _generate_admin_manual_refund(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate bot template for admin manual refunds"""
        refund = context["refund"]
        user = context["user"]
        context["platform"]
        
        message = f"""{self.emoji_map['tools']} **Manual Refund Processed**

Hi {user['first_name']}!

Our admin team has processed a manual refund for your account.

{self.emoji_map['money']} Refund Amount: `{refund['formatted_amount']}`
{self.emoji_map['info']} Refund ID: `{refund['refund_id']}`
{self.emoji_map['tools']} **Type:** Manual Admin Refund
{self.emoji_map['clock']} Processed: {self._format_datetime_for_bot(refund['created_at'])}

**Reason:** {refund.get('reason', 'Manual refund processed by admin team')}

**Status:**
{self.emoji_map['success']} Refund completed
{self.emoji_map['rocket']} Funds available immediately
{self.emoji_map['shield']} Added to wallet balance

The refunded amount is ready for use in new transactions or cashouts!"""
        
        keyboard = [
            [
                InlineKeyboardButton(f"{self.emoji_map['money']} Check Wallet", callback_data=f"view_wallet_{user['id']}"),
                InlineKeyboardButton("📊 Transaction History", callback_data=f"view_transactions_{user['id']}")
            ],
            [
                InlineKeyboardButton("💬 Thank Admin Team", callback_data="thank_admin"),
                InlineKeyboardButton(f"{self.emoji_map['rocket']} Start Trading", callback_data="main_menu")
            ],
            [
                InlineKeyboardButton(f"{self.emoji_map['thumbs_up']} Got It!", callback_data=f"confirm_notification_{refund['refund_id']}")
            ]
        ]
        
        return {
            "message": message,
            "keyboard": InlineKeyboardMarkup(keyboard),
            "parse_mode": "Markdown"
        }
    
    def _generate_overpayment_refund(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate bot template for overpayment refunds"""
        refund = context["refund"]
        user = context["user"]
        context["platform"]
        
        message = f"""{self.emoji_map['money']} **Overpayment Refund Processed**

{user['first_name']}, you paid more than required!

We detected an overpayment on your recent transaction and have automatically refunded the excess amount.

{self.emoji_map['money']} **Excess Amount:** `{refund['formatted_amount']}`
{self.emoji_map['success']} **Status:** Refund Completed
{self.emoji_map['info']} Refund ID: `{refund['refund_id']}`
{self.emoji_map['clock']} Processed: {self._format_datetime_for_bot(refund['created_at'])}

**What happened:**
You sent more than the required amount for your order. Our system automatically detected this and processed a refund for the difference.

Your Refund:
{self.emoji_map['success']} Automatically processed
{self.emoji_map['rocket']} Added to wallet balance
{self.emoji_map['shield']} Available immediately

**Original Order:**
Your original order was processed successfully with the correct amount. Only the excess was refunded.

Great attention to detail by our automated systems!"""
        
        keyboard = [
            [
                InlineKeyboardButton(f"{self.emoji_map['money']} View Wallet", callback_data=f"view_wallet_{user['id']}"),
                InlineKeyboardButton("📊 Order Details", callback_data=f"view_order_{refund.get('transaction_id', '')}")
            ],
            [
                InlineKeyboardButton("💡 Payment Tips", callback_data="payment_accuracy_tips"),
                InlineKeyboardButton("💬 Ask Questions", callback_data="start_support_chat")
            ],
            [
                InlineKeyboardButton(f"{self.emoji_map['thumbs_up']} Thanks!", callback_data=f"confirm_notification_{refund['refund_id']}")
            ]
        ]
        
        return {
            "message": message,
            "keyboard": InlineKeyboardMarkup(keyboard),
            "parse_mode": "Markdown"
        }
    
    def _generate_rate_lock_expired_refund(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate bot template for rate lock expired refunds"""
        refund = context["refund"]
        user = context["user"]
        context["platform"]
        
        message = f"""{self.emoji_map['clock']} **Rate Lock Expired - Refund Issued**

Hi {user['first_name']}!

Your rate lock expired before payment confirmation. We've processed a refund to protect you from unfavorable rate changes.

{self.emoji_map['money']} Refund Amount: `{refund['formatted_amount']}`
{self.emoji_map['clock']} **Rate Lock:** Expired
{self.emoji_map['info']} Refund ID: `{refund['refund_id']}`
{self.emoji_map['success']} **Status:** Refund Completed

**What happened:**
Rate locks protect you from market fluctuations for a limited time. When they expire, we refund to avoid processing at unfavorable rates.

Your Protection:
{self.emoji_map['shield']} Protected from rate changes
{self.emoji_map['success']} Full refund processed
{self.emoji_map['rocket']} Can create new order immediately

Next Steps:
• Check current rates
• Create new order with fresh rate lock
• Complete payment within the time window

Rate protection working as designed!"""
        
        keyboard = [
            [
                InlineKeyboardButton("📈 Check Current Rates", callback_data="view_exchange_rates"),
                InlineKeyboardButton(f"{self.emoji_map['processing']} New Order", callback_data="start_exchange")
            ],
            [
                InlineKeyboardButton(f"{self.emoji_map['money']} Check Wallet", callback_data=f"view_wallet_{user['id']}"),
                InlineKeyboardButton("💡 Rate Lock Tips", callback_data="rate_lock_help")
            ],
            [
                InlineKeyboardButton(f"{self.emoji_map['thumbs_up']} Understood", callback_data=f"confirm_notification_{refund['refund_id']}")
            ]
        ]
        
        return {
            "message": message,
            "keyboard": InlineKeyboardMarkup(keyboard),
            "parse_mode": "Markdown"
        }
    
    def _generate_refund_processing_confirmation(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate bot template for refund processing confirmation"""
        refund = context["refund"]
        user = context["user"]
        context["platform"]
        
        message = f"""{self.emoji_map['processing']} **Refund Being Processed**

Hi {user['first_name']}!

We're currently processing your refund request. This message confirms we've received it and are working to complete it quickly.

{self.emoji_map['money']} **Amount:** `{refund['formatted_amount']}`
{self.emoji_map['info']} Refund ID: `{refund['refund_id']}`
{self.emoji_map['processing']} **Status:** Processing
{self.emoji_map['clock']} **Expected:** Within 24 hours
{self.emoji_map['bell']} **Started:** {self._format_datetime_for_bot(refund['created_at'])}

**Processing Steps:**
1. {self.emoji_map['shield']} Verifying refund eligibility
2. {self.emoji_map['tools']} Calculating refund amount
3. {self.emoji_map['money']} Adding to wallet balance
4. {self.emoji_map['bell']} Sending completion notification

**What to expect:**
You'll receive another notification once the refund is completed and added to your wallet balance.

Please allow up to 24 hours for processing."""
        
        keyboard = [
            [
                InlineKeyboardButton("📊 Check Status", callback_data=f"refund_status_{refund['refund_id']}"),
                InlineKeyboardButton(f"{self.emoji_map['money']} View Wallet", callback_data=f"view_wallet_{user['id']}")
            ],
            [
                InlineKeyboardButton("💬 Contact Support", callback_data="start_support_chat"),
                InlineKeyboardButton("📋 Transaction History", callback_data=f"view_transactions_{user['id']}")
            ],
            [
                InlineKeyboardButton(f"{self.emoji_map['thumbs_up']} OK", callback_data=f"confirm_notification_{refund['refund_id']}")
            ]
        ]
        
        return {
            "message": message,
            "keyboard": InlineKeyboardMarkup(keyboard),
            "parse_mode": "Markdown"
        }
    
    def _generate_refund_completed_confirmation(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate bot template for refund completion confirmation"""
        refund = context["refund"]
        user = context["user"]
        context["platform"]
        wallet = context.get("wallet", {})
        
        message = f"""{self.emoji_map['success']} **Refund Completed Successfully!**

🎉 Great news, {user['first_name']}!

Your refund has been completed and added to your wallet balance.

{self.emoji_map['money']} Amount Added: `{refund['formatted_amount']}`
{self.emoji_map['info']} Refund ID: `{refund['refund_id']}`
{self.emoji_map['success']} Status: ✅ Completed
{self.emoji_map['rocket']} Available: Immediately
{self.emoji_map['clock']} Completed: {self._format_datetime_for_bot(refund.get('completed_at') or refund['created_at'])}

Current Wallet Balance:
{self.emoji_map['money']} `{wallet.get('formatted_balance', 'Check wallet for balance')}`

What you can do now:
{self.emoji_map['rocket']} Use funds immediately
{self.emoji_map['shield']} Start new escrow trades
{self.emoji_map['processing']} Request cashout
{self.emoji_map['bell']} Convert currencies

Your refunded funds are ready for action!"""
        
        keyboard = [
            [
                InlineKeyboardButton(f"{self.emoji_map['money']} View Wallet", callback_data=f"view_wallet_{user['id']}"),
                InlineKeyboardButton(f"{self.emoji_map['shield']} Start Escrow", callback_data="start_escrow")
            ],
            [
                InlineKeyboardButton(f"{self.emoji_map['processing']} Request Cashout", callback_data="start_cashout"),
                InlineKeyboardButton("🔄 Exchange Currency", callback_data="start_exchange")
            ],
            [
                InlineKeyboardButton("📊 Transaction History", callback_data=f"view_transactions_{user['id']}"),
                InlineKeyboardButton(f"{self.emoji_map['heart']} Awesome!", callback_data=f"confirm_notification_{refund['refund_id']}")
            ]
        ]
        
        return {
            "message": message,
            "keyboard": InlineKeyboardMarkup(keyboard),
            "parse_mode": "Markdown"
        }
    
    def _generate_generic_refund_template(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Generate generic refund template as fallback"""
        refund = context["refund"]
        user = context["user"]
        context["platform"]
        
        message = f"""{self.emoji_map['processing']} **Refund Processed**

Hello {user['first_name']}!

We have processed a refund for your account.

{self.emoji_map['money']} **Amount:** `{refund['formatted_amount']}`
{self.emoji_map['info']} Refund ID: `{refund['refund_id']}`
{self.emoji_map['tools']} **Type:** {refund.get('type_display', 'Refund')}
{self.emoji_map['clock']} **Date:** {self._format_datetime_for_bot(refund['created_at'])}

**Reason:** {refund.get('reason', 'Refund processed')}

The refund has been added to your wallet balance and is available for immediate use.

If you have any questions about this refund, please contact our support team."""
        
        keyboard = [
            [
                InlineKeyboardButton(f"{self.emoji_map['money']} Check Wallet", callback_data=f"view_wallet_{user['id']}"),
                InlineKeyboardButton("💬 Contact Support", callback_data="start_support_chat")
            ],
            [
                InlineKeyboardButton("📊 Transaction History", callback_data=f"view_transactions_{user['id']}"),
                InlineKeyboardButton(f"{self.emoji_map['thumbs_up']} OK", callback_data=f"confirm_notification_{refund['refund_id']}")
            ]
        ]
        
        return {
            "message": message,
            "keyboard": InlineKeyboardMarkup(keyboard),
            "parse_mode": "Markdown"
        }
    
    def _generate_error_fallback_template(
        self, 
        context: Dict[str, Any], 
        template_id: str, 
        error_message: str
    ) -> Dict[str, Any]:
        """Generate error fallback template when template generation fails"""
        user = context.get("user", {})
        platform = context.get("platform", {"name": "Trading Platform"})
        
        message = f"""{self.emoji_map['warning']} **Important Account Update**

Hello {user.get('first_name', 'User')}!

We have an important update regarding your account. Please check your account dashboard for details.

If you have any questions, please contact our support team.

{platform.get('name', 'Trading Platform')} Support Team"""
        
        keyboard = [
            [
                InlineKeyboardButton("📱 View Dashboard", callback_data="main_menu"),
                InlineKeyboardButton("💬 Contact Support", callback_data="start_support_chat")
            ]
        ]
        
        return {
            "message": message,
            "keyboard": InlineKeyboardMarkup(keyboard),
            "parse_mode": "Markdown"
        }
    
    def _format_datetime_for_bot(self, datetime_str: str) -> str:
        """Format datetime string for bot messages"""
        try:
            if isinstance(datetime_str, datetime):
                dt = datetime_str
            else:
                dt = datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
            
            return dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            return str(datetime_str)
    
    def generate_progressive_disclosure_content(
        self,
        base_template: str,
        user_id: int,
        disclosure_level: int = 1
    ) -> Dict[str, Any]:
        """
        Generate progressive disclosure content for complex scenarios
        
        Args:
            base_template: Base template identifier
            user_id: User ID for personalization
            disclosure_level: Level of detail (1=basic, 2=detailed, 3=expert)
        """
        try:
            # This would implement progressive disclosure logic
            # Level 1: Basic information
            # Level 2: Detailed explanation
            # Level 3: Technical details and advanced options
            
            if disclosure_level == 1:
                # Basic level - essential information only
                return {
                    "show_details": False,
                    "action_buttons": ["main_actions"],
                    "explanation_depth": "basic"
                }
            elif disclosure_level == 2:
                # Detailed level - more context and options
                return {
                    "show_details": True,
                    "action_buttons": ["main_actions", "secondary_actions"],
                    "explanation_depth": "detailed"
                }
            else:  # Level 3
                # Expert level - full details and advanced options
                return {
                    "show_details": True,
                    "action_buttons": ["main_actions", "secondary_actions", "advanced_actions"],
                    "explanation_depth": "expert"
                }
                
        except Exception as e:
            logger.error(f"❌ Error generating progressive disclosure content: {e}")
            return {
                "show_details": False,
                "action_buttons": ["main_actions"],
                "explanation_depth": "basic"
            }
    
    def create_confirmation_keyboard(
        self,
        refund_id: str,
        additional_actions: List[Dict[str, str]] = None
    ) -> InlineKeyboardMarkup:
        """
        Create a confirmation keyboard for refund notifications
        
        Args:
            refund_id: Refund ID for confirmation tracking
            additional_actions: Additional action buttons to include
        """
        try:
            keyboard = []
            
            # Add additional actions if provided
            if additional_actions:
                for action in additional_actions:
                    keyboard.append([
                        InlineKeyboardButton(
                            action["text"],
                            callback_data=action["callback_data"]
                        )
                    ])
            
            # Always add confirmation button
            keyboard.append([
                InlineKeyboardButton(
                    f"{self.emoji_map['thumbs_up']} Got it!",
                    callback_data=f"confirm_notification_{refund_id}"
                )
            ])
            
            return InlineKeyboardMarkup(keyboard)
            
        except Exception as e:
            logger.error(f"❌ Error creating confirmation keyboard: {e}")
            return InlineKeyboardMarkup([
                [InlineKeyboardButton("OK", callback_data=f"confirm_notification_{refund_id}")]
            ])
    
    def create_support_escalation_keyboard(self, refund_id: str) -> InlineKeyboardMarkup:
        """Create keyboard for support escalation scenarios"""
        keyboard = [
            [
                InlineKeyboardButton("💬 Live Chat", callback_data="start_support_chat"),
                InlineKeyboardButton("📧 Email Support", callback_data="email_support")
            ],
            [
                InlineKeyboardButton("📞 Request Callback", callback_data=f"callback_request_{refund_id}"),
                InlineKeyboardButton("🎫 Create Ticket", callback_data=f"create_ticket_{refund_id}")
            ],
            [
                InlineKeyboardButton("📋 FAQ", callback_data="refund_faq"),
                InlineKeyboardButton(f"{self.emoji_map['thumbs_up']} Resolved", callback_data=f"confirm_notification_{refund_id}")
            ]
        ]
        
        return InlineKeyboardMarkup(keyboard)
    
    def create_action_summary_keyboard(
        self,
        user_id: int,
        refund_id: str,
        primary_actions: List[str] = None
    ) -> InlineKeyboardMarkup:
        """Create keyboard with summary of available actions"""
        
        primary_actions = primary_actions or ["wallet", "support"]
        keyboard = []
        
        if "wallet" in primary_actions:
            keyboard.append([
                InlineKeyboardButton(f"{self.emoji_map['money']} Wallet", callback_data=f"view_wallet_{user_id}")
            ])
        
        if "support" in primary_actions:
            keyboard.append([
                InlineKeyboardButton("💬 Support", callback_data="start_support_chat")
            ])
        
        if "transactions" in primary_actions:
            keyboard.append([
                InlineKeyboardButton("📊 Transactions", callback_data=f"view_transactions_{user_id}")
            ])
        
        # Always include confirmation
        keyboard.append([
            InlineKeyboardButton(f"{self.emoji_map['thumbs_up']} Acknowledged", callback_data=f"confirm_notification_{refund_id}")
        ])
        
        return InlineKeyboardMarkup(keyboard)


# Global template service instance
refund_bot_templates = RefundBotTemplates()


# Convenience functions for easy integration

def generate_cashout_failed_bot_notification(context: Dict[str, Any]) -> Dict[str, Any]:
    """Generate bot notification for failed cashout refund"""
    return refund_bot_templates.generate_bot_content("cashout_failed_refund", context)


def generate_escrow_timeout_bot_notification(context: Dict[str, Any]) -> Dict[str, Any]:
    """Generate bot notification for escrow timeout refund"""
    return refund_bot_templates.generate_bot_content("escrow_timeout_refund", context)


def generate_dispute_resolution_bot_notification(context: Dict[str, Any]) -> Dict[str, Any]:
    """Generate bot notification for dispute resolution refund"""
    return refund_bot_templates.generate_bot_content("dispute_resolution_refund", context)


def generate_post_timeout_payment_bot_notification(context: Dict[str, Any]) -> Dict[str, Any]:
    """Generate bot notification for post-timeout payment refund"""
    return refund_bot_templates.generate_bot_content("post_timeout_payment_refund", context)


def generate_system_error_bot_notification(context: Dict[str, Any]) -> Dict[str, Any]:
    """Generate bot notification for system error refund"""
    return refund_bot_templates.generate_bot_content("system_error_refund", context)


def generate_generic_refund_bot_notification(context: Dict[str, Any]) -> Dict[str, Any]:
    """Generate generic bot notification for refunds"""
    return refund_bot_templates.generate_bot_content("generic_refund_template", context)