"""
Refund Email Templates Service
Comprehensive HTML email templates for all refund notification scenarios
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class RefundEmailTemplates:
    """
    Comprehensive email template generator for all refund notification scenarios
    """
    
    def __init__(self):
        self.brand_color = "#1e3a8a"  # Professional blue
        self.success_color = "#16a34a"  # Green for positive actions
        self.warning_color = "#f59e0b"  # Amber for warnings
        self.error_color = "#dc2626"  # Red for errors
        self.neutral_color = "#6b7280"  # Gray for neutral content
        
    def generate_email_content(self, template_id: str, context: Dict[str, Any]) -> Dict[str, str]:
        """
        Generate complete email content for a given template and context
        
        Args:
            template_id: Template identifier
            context: Template variables and data
            
        Returns:
            Dictionary with subject, html_body, and text_body
        """
        try:
            template_method = getattr(self, f"_generate_{template_id}", None)
            if not template_method:
                logger.warning(f"⚠️ No template method found for {template_id}, using fallback")
                return self._generate_generic_refund_template(context)
            
            return template_method(context)
            
        except Exception as e:
            logger.error(f"❌ Error generating email template {template_id}: {e}")
            return self._generate_error_fallback_template(context, template_id, str(e))
    
    def _generate_cashout_failed_refund(self, context: Dict[str, Any]) -> Dict[str, str]:
        """Generate email template for failed cashout refunds"""
        refund = context["refund"]
        user = context["user"]
        platform = context["platform"]
        cashout = context.get("cashout", {})
        
        subject = f"🔄 Cashout Refund Processed - {refund['formatted_amount']} - {platform['name']}"
        
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Cashout Refund Notification</title>
        </head>
        <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc; line-height: 1.6;">
            <!-- Header -->
            <table width="100%" cellpadding="0" cellspacing="0" style="background-color: {self.brand_color};">
                <tr>
                    <td align="center" style="padding: 30px 20px;">
                        <h1 style="color: white; margin: 0; font-size: 28px; font-weight: bold;">
                            🔄 Cashout Refund Processed
                        </h1>
                        <p style="color: #e2e8f0; margin: 10px 0 0 0; font-size: 18px;">
                            {platform['name']} - Secure Trading Platform
                        </p>
                    </td>
                </tr>
            </table>
            
            <!-- Main Content -->
            <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                    <td align="center" style="padding: 0 20px;">
                        <table width="100%" max-width="600" cellpadding="0" cellspacing="0" style="background-color: white; margin: 20px 0; border-radius: 12px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
                            
                            <!-- Greeting -->
                            <tr>
                                <td style="padding: 30px;">
                                    <h2 style="color: {self.brand_color}; margin: 0 0 20px 0; font-size: 24px;">
                                        Hello {user['first_name']}!
                                    </h2>
                                    <p style="color: #374151; font-size: 16px; margin: 0 0 20px 0;">
                                        We've processed a refund for your failed cashout request. Your funds have been safely returned to your wallet.
                                    </p>
                                </td>
                            </tr>
                            
                            <!-- Refund Details Card -->
                            <tr>
                                <td style="padding: 0 30px 20px 30px;">
                                    <div style="background-color: #f0f9ff; border: 1px solid #0ea5e9; border-radius: 8px; padding: 25px;">
                                        <h3 style="color: {self.brand_color}; margin: 0 0 15px 0; font-size: 20px;">
                                            💰 Refund Details
                                        </h3>
                                        
                                        <table width="100%" cellpadding="0" cellspacing="0" style="font-size: 15px;">
                                            <tr style="border-bottom: 1px solid #e2e8f0;">
                                                <td style="padding: 10px 0; color: #6b7280; font-weight: 600;">Refund Amount:</td>
                                                <td style="padding: 10px 0; color: #1f2937; font-weight: bold; text-align: right; font-size: 18px;">
                                                    {refund['formatted_amount']}
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #e2e8f0;">
                                                <td style="padding: 10px 0; color: #6b7280; font-weight: 600;">Refund ID:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right; font-family: monospace;">
                                                    {refund['refund_id']}
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #e2e8f0;">
                                                <td style="padding: 10px 0; color: #6b7280; font-weight: 600;">Original Cashout ID:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right; font-family: monospace;">
                                                    {cashout.get('cashout_id', 'N/A')}
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #e2e8f0;">
                                                <td style="padding: 10px 0; color: #6b7280; font-weight: 600;">Reason:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right;">
                                                    Cashout processing failed
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 10px 0; color: #6b7280; font-weight: 600;">Date Processed:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right;">
                                                    {refund['created_at']}
                                                </td>
                                            </tr>
                                        </table>
                                    </div>
                                </td>
                            </tr>
                            
                            <!-- What This Means -->
                            <tr>
                                <td style="padding: 0 30px 20px 30px;">
                                    <div style="background-color: #fefce8; border: 1px solid #facc15; border-radius: 8px; padding: 20px;">
                                        <h3 style="color: {self.warning_color}; margin: 0 0 12px 0; font-size: 18px;">
                                            ℹ️ What This Means
                                        </h3>
                                        <p style="color: #78716c; margin: 0; font-size: 14px; line-height: 1.5;">
                                            Your cashout request couldn't be completed due to technical issues or insufficient funds in our processing systems. 
                                            Don't worry - your funds are safe and have been returned to your wallet balance automatically.
                                        </p>
                                    </div>
                                </td>
                            </tr>
                            
                            <!-- Current Wallet Balance -->
                            {self._generate_wallet_balance_section(context)}
                            
                            <!-- Action Buttons -->
                            <tr>
                                <td style="padding: 20px 30px;">
                                    <table width="100%" cellpadding="0" cellspacing="0">
                                        <tr>
                                            <td align="center" style="padding: 0 10px 15px 0;">
                                                <a href="{platform['webapp_url']}/wallet" 
                                                   style="display: inline-block; background-color: {self.success_color}; color: white; padding: 14px 24px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px;">
                                                    💰 Check Wallet Balance
                                                </a>
                                            </td>
                                            <td align="center" style="padding: 0 0 15px 10px;">
                                                <a href="{platform['webapp_url']}/cashout" 
                                                   style="display: inline-block; background-color: {self.brand_color}; color: white; padding: 14px 24px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px;">
                                                    🔄 Try Cashout Again
                                                </a>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                            
                            <!-- Support Information -->
                            <tr>
                                <td style="padding: 20px 30px 30px 30px;">
                                    <div style="background-color: #f9fafb; border-radius: 8px; padding: 20px; text-align: center;">
                                        <h3 style="color: {self.brand_color}; margin: 0 0 12px 0; font-size: 18px;">
                                            Need Help?
                                        </h3>
                                        <p style="color: #6b7280; margin: 0 0 15px 0; font-size: 14px;">
                                            Our support team is here to help with any questions about your refund or future cashout attempts.
                                        </p>
                                        <a href="mailto:{platform['support_email']}" 
                                           style="display: inline-block; background-color: {self.neutral_color}; color: white; padding: 12px 20px; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 14px;">
                                            📧 Contact Support
                                        </a>
                                    </div>
                                </td>
                            </tr>
                            
                        </table>
                    </td>
                </tr>
            </table>
            
            <!-- Footer -->
            {self._generate_email_footer(platform)}
            
        </body>
        </html>
        """
        
        text_body = f"""
        Cashout Refund Processed - {platform['name']}
        
        Hello {user['first_name']},
        
        We've processed a refund for your failed cashout request. Your funds have been safely returned to your wallet.
        
        REFUND DETAILS:
        • Refund Amount: {refund['formatted_amount']}
        • Refund ID: {refund['refund_id']}
        • Original Cashout ID: {cashout.get('cashout_id', 'N/A')}
        • Reason: Cashout processing failed
        • Date Processed: {refund['created_at']}
        
        WHAT THIS MEANS:
        Your cashout request couldn't be completed due to technical issues or insufficient funds in our processing systems. 
        Don't worry - your funds are safe and have been returned to your wallet balance automatically.
        
        NEXT STEPS:
        • Check your wallet balance: {platform['webapp_url']}/wallet
        • Try cashout again: {platform['webapp_url']}/cashout
        • Contact support if needed: {platform['support_email']}
        
        Thank you for your patience and for using {platform['name']}.
        
        ---
        This is an automated message from {platform['name']} notification system.
        """
        
        return {
            "subject": subject,
            "html_body": html_body,
            "text_body": text_body
        }
    
    def _generate_escrow_timeout_refund(self, context: Dict[str, Any]) -> Dict[str, str]:
        """Generate email template for escrow timeout refunds"""
        refund = context["refund"]
        user = context["user"]
        platform = context["platform"]
        escrow = context.get("escrow", {})
        
        subject = f"🕐 Escrow Timeout Refund - {refund['formatted_amount']} - {platform['name']}"
        
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Escrow Timeout Refund</title>
        </head>
        <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc; line-height: 1.6;">
            
            <!-- Header -->
            <table width="100%" cellpadding="0" cellspacing="0" style="background-color: {self.warning_color};">
                <tr>
                    <td align="center" style="padding: 30px 20px;">
                        <h1 style="color: white; margin: 0; font-size: 28px; font-weight: bold;">
                            🕐 Escrow Timeout Refund
                        </h1>
                        <p style="color: #fef3c7; margin: 10px 0 0 0; font-size: 18px;">
                            {platform['name']} - Secure Trading Platform
                        </p>
                    </td>
                </tr>
            </table>
            
            <!-- Main Content -->
            <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                    <td align="center" style="padding: 0 20px;">
                        <table width="100%" max-width="600" cellpadding="0" cellspacing="0" style="background-color: white; margin: 20px 0; border-radius: 12px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
                            
                            <!-- Greeting -->
                            <tr>
                                <td style="padding: 30px;">
                                    <h2 style="color: {self.brand_color}; margin: 0 0 20px 0; font-size: 24px;">
                                        Hello {user['first_name']}!
                                    </h2>
                                    <p style="color: #374151; font-size: 16px; margin: 0 0 20px 0;">
                                        Your escrow trade has expired due to timeout, and we've automatically processed a full refund to your wallet.
                                    </p>
                                </td>
                            </tr>
                            
                            <!-- Trade & Refund Details -->
                            <tr>
                                <td style="padding: 0 30px 20px 30px;">
                                    <div style="background-color: #fff7ed; border: 1px solid {self.warning_color}; border-radius: 8px; padding: 25px;">
                                        <h3 style="color: {self.warning_color}; margin: 0 0 15px 0; font-size: 20px;">
                                            📋 Trade & Refund Details
                                        </h3>
                                        
                                        <table width="100%" cellpadding="0" cellspacing="0" style="font-size: 15px;">
                                            <tr style="border-bottom: 1px solid #fed7aa;">
                                                <td style="padding: 10px 0; color: #9a3412; font-weight: 600;">Refund Amount:</td>
                                                <td style="padding: 10px 0; color: #1f2937; font-weight: bold; text-align: right; font-size: 18px;">
                                                    {refund['formatted_amount']}
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #fed7aa;">
                                                <td style="padding: 10px 0; color: #9a3412; font-weight: 600;">Escrow ID:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right; font-family: monospace;">
                                                    {escrow.get('escrow_id', 'N/A')}
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #fed7aa;">
                                                <td style="padding: 10px 0; color: #9a3412; font-weight: 600;">Item:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right;">
                                                    {escrow.get('item_name', 'Digital Item')}
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #fed7aa;">
                                                <td style="padding: 10px 0; color: #9a3412; font-weight: 600;">Refund ID:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right; font-family: monospace;">
                                                    {refund['refund_id']}
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 10px 0; color: #9a3412; font-weight: 600;">Refund Date:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right;">
                                                    {refund['created_at']}
                                                </td>
                                            </tr>
                                        </table>
                                    </div>
                                </td>
                            </tr>
                            
                            <!-- Why This Happened -->
                            <tr>
                                <td style="padding: 0 30px 20px 30px;">
                                    <div style="background-color: #eff6ff; border: 1px solid {self.brand_color}; border-radius: 8px; padding: 20px;">
                                        <h3 style="color: {self.brand_color}; margin: 0 0 12px 0; font-size: 18px;">
                                            ❓ Why This Happened
                                        </h3>
                                        <p style="color: #1e40af; margin: 0; font-size: 14px; line-height: 1.5;">
                                            Escrow trades have time limits to ensure fairness for both parties. When a trade isn't completed within the 
                                            specified timeframe, we automatically refund the buyer to protect their interests. Your funds were never at risk.
                                        </p>
                                    </div>
                                </td>
                            </tr>
                            
                            <!-- Current Wallet Balance -->
                            {self._generate_wallet_balance_section(context)}
                            
                            <!-- Action Buttons -->
                            <tr>
                                <td style="padding: 20px 30px;">
                                    <table width="100%" cellpadding="0" cellspacing="0">
                                        <tr>
                                            <td align="center" style="padding: 0 10px 15px 0;">
                                                <a href="{platform['webapp_url']}/escrow/create" 
                                                   style="display: inline-block; background-color: {self.success_color}; color: white; padding: 14px 24px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px;">
                                                    🛡️ Start New Escrow
                                                </a>
                                            </td>
                                            <td align="center" style="padding: 0 0 15px 10px;">
                                                <a href="{platform['webapp_url']}/wallet" 
                                                   style="display: inline-block; background-color: {self.brand_color}; color: white; padding: 14px 24px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px;">
                                                    💰 Check Wallet
                                                </a>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                            
                            <!-- Tips for Future Trades -->
                            <tr>
                                <td style="padding: 20px 30px 30px 30px;">
                                    <div style="background-color: #f0f9ff; border-radius: 8px; padding: 20px;">
                                        <h3 style="color: {self.brand_color}; margin: 0 0 12px 0; font-size: 18px;">
                                            💡 Tips for Future Trades
                                        </h3>
                                        <ul style="color: #374151; margin: 0; padding-left: 20px; font-size: 14px; line-height: 1.6;">
                                            <li>Communicate clearly with your trading partner about delivery timelines</li>
                                            <li>Set realistic delivery timeouts based on item complexity</li>
                                            <li>Use our messaging system to stay updated on trade progress</li>
                                            <li>Report any issues immediately to our support team</li>
                                        </ul>
                                    </div>
                                </td>
                            </tr>
                            
                        </table>
                    </td>
                </tr>
            </table>
            
            <!-- Footer -->
            {self._generate_email_footer(platform)}
            
        </body>
        </html>
        """
        
        text_body = f"""
        Escrow Timeout Refund - {platform['name']}
        
        Hello {user['first_name']},
        
        Your escrow trade has expired due to timeout, and we've automatically processed a full refund to your wallet.
        
        TRADE & REFUND DETAILS:
        • Refund Amount: {refund['formatted_amount']}
        • Escrow ID: {escrow.get('escrow_id', 'N/A')}
        • Item: {escrow.get('item_name', 'Digital Item')}
        • Refund ID: {refund['refund_id']}
        • Refund Date: {refund['created_at']}
        
        WHY THIS HAPPENED:
        Escrow trades have time limits to ensure fairness for both parties. When a trade isn't completed within the 
        specified timeframe, we automatically refund the buyer to protect their interests. Your funds were never at risk.
        
        NEXT STEPS:
        • Start new escrow: {platform['webapp_url']}/escrow/create
        • Check wallet: {platform['webapp_url']}/wallet
        • Contact support: {platform['support_email']}
        
        TIPS FOR FUTURE TRADES:
        • Communicate clearly with your trading partner about delivery timelines
        • Set realistic delivery timeouts based on item complexity
        • Use our messaging system to stay updated on trade progress
        • Report any issues immediately to our support team
        
        Thank you for using {platform['name']}.
        
        ---
        This is an automated message from {platform['name']} notification system.
        """
        
        return {
            "subject": subject,
            "html_body": html_body,
            "text_body": text_body
        }
    
    def _generate_dispute_resolution_refund(self, context: Dict[str, Any]) -> Dict[str, str]:
        """Generate email template for dispute resolution refunds"""
        refund = context["refund"]
        user = context["user"]
        platform = context["platform"]
        dispute_details = context.get("dispute_details", {})
        
        subject = f"⚖️ Dispute Resolved - Refund Issued - {refund['formatted_amount']} - {platform['name']}"
        
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Dispute Resolution Refund</title>
        </head>
        <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc; line-height: 1.6;">
            
            <!-- Header -->
            <table width="100%" cellpadding="0" cellspacing="0" style="background-color: {self.success_color};">
                <tr>
                    <td align="center" style="padding: 30px 20px;">
                        <h1 style="color: white; margin: 0; font-size: 28px; font-weight: bold;">
                            ⚖️ Dispute Resolved - Refund Issued
                        </h1>
                        <p style="color: #d1fae5; margin: 10px 0 0 0; font-size: 18px;">
                            {platform['name']} - Secure Trading Platform
                        </p>
                    </td>
                </tr>
            </table>
            
            <!-- Main Content -->
            <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                    <td align="center" style="padding: 0 20px;">
                        <table width="100%" max-width="600" cellpadding="0" cellspacing="0" style="background-color: white; margin: 20px 0; border-radius: 12px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
                            
                            <!-- Greeting -->
                            <tr>
                                <td style="padding: 30px;">
                                    <h2 style="color: {self.brand_color}; margin: 0 0 20px 0; font-size: 24px;">
                                        Hello {user['first_name']}!
                                    </h2>
                                    <p style="color: #374151; font-size: 16px; margin: 0 0 20px 0;">
                                        Great news! The dispute for your escrow trade has been resolved in your favor. 
                                        We've processed a full refund to your wallet as per our resolution decision.
                                    </p>
                                </td>
                            </tr>
                            
                            <!-- Resolution & Refund Details -->
                            <tr>
                                <td style="padding: 0 30px 20px 30px;">
                                    <div style="background-color: #f0fdf4; border: 1px solid {self.success_color}; border-radius: 8px; padding: 25px;">
                                        <h3 style="color: {self.success_color}; margin: 0 0 15px 0; font-size: 20px;">
                                            ✅ Resolution & Refund Details
                                        </h3>
                                        
                                        <table width="100%" cellpadding="0" cellspacing="0" style="font-size: 15px;">
                                            <tr style="border-bottom: 1px solid #bbf7d0;">
                                                <td style="padding: 10px 0; color: #15803d; font-weight: 600;">Resolution:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right; font-weight: bold;">
                                                    Buyer Favor (Full Refund)
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #bbf7d0;">
                                                <td style="padding: 10px 0; color: #15803d; font-weight: 600;">Refund Amount:</td>
                                                <td style="padding: 10px 0; color: #1f2937; font-weight: bold; text-align: right; font-size: 18px;">
                                                    {refund['formatted_amount']}
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #bbf7d0;">
                                                <td style="padding: 10px 0; color: #15803d; font-weight: 600;">Dispute ID:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right; font-family: monospace;">
                                                    {dispute_details.get('dispute_id', 'N/A')}
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #bbf7d0;">
                                                <td style="padding: 10px 0; color: #15803d; font-weight: 600;">Refund ID:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right; font-family: monospace;">
                                                    {refund['refund_id']}
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 10px 0; color: #15803d; font-weight: 600;">Resolution Date:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right;">
                                                    {refund['created_at']}
                                                </td>
                                            </tr>
                                        </table>
                                    </div>
                                </td>
                            </tr>
                            
                            <!-- Resolution Summary -->
                            <tr>
                                <td style="padding: 0 30px 20px 30px;">
                                    <div style="background-color: #eff6ff; border: 1px solid {self.brand_color}; border-radius: 8px; padding: 20px;">
                                        <h3 style="color: {self.brand_color}; margin: 0 0 12px 0; font-size: 18px;">
                                            📝 Resolution Summary
                                        </h3>
                                        <p style="color: #1e40af; margin: 0; font-size: 14px; line-height: 1.5;">
                                            After careful review of all evidence and communications, our dispute resolution team determined that 
                                            the trade terms were not fulfilled as agreed. As a result, you are entitled to a full refund of your payment.
                                            {dispute_details.get('admin_notes', '')}
                                        </p>
                                    </div>
                                </td>
                            </tr>
                            
                            <!-- Current Wallet Balance -->
                            {self._generate_wallet_balance_section(context)}
                            
                            <!-- Action Buttons -->
                            <tr>
                                <td style="padding: 20px 30px;">
                                    <table width="100%" cellpadding="0" cellspacing="0">
                                        <tr>
                                            <td align="center" style="padding: 0 10px 15px 0;">
                                                <a href="{platform['webapp_url']}/wallet" 
                                                   style="display: inline-block; background-color: {self.success_color}; color: white; padding: 14px 24px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px;">
                                                    💰 Check Wallet Balance
                                                </a>
                                            </td>
                                            <td align="center" style="padding: 0 0 15px 10px;">
                                                <a href="{platform['webapp_url']}/transactions" 
                                                   style="display: inline-block; background-color: {self.brand_color}; color: white; padding: 14px 24px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px;">
                                                    📊 View Transaction History
                                                </a>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                            
                            <!-- Thank You Message -->
                            <tr>
                                <td style="padding: 20px 30px 30px 30px;">
                                    <div style="background-color: #f9fafb; border-radius: 8px; padding: 20px; text-align: center;">
                                        <h3 style="color: {self.brand_color}; margin: 0 0 12px 0; font-size: 18px;">
                                            🙏 Thank You for Your Patience
                                        </h3>
                                        <p style="color: #6b7280; margin: 0 0 15px 0; font-size: 14px; line-height: 1.5;">
                                            We appreciate your patience during the dispute resolution process. Our goal is always to ensure 
                                            fair and secure transactions for all users. We hope you'll continue to use {platform['name']} for your trading needs.
                                        </p>
                                        <p style="color: #6b7280; margin: 0; font-size: 13px;">
                                            Questions? Contact us at <a href="mailto:{platform['support_email']}" style="color: {self.brand_color}; text-decoration: none;">{platform['support_email']}</a>
                                        </p>
                                    </div>
                                </td>
                            </tr>
                            
                        </table>
                    </td>
                </tr>
            </table>
            
            <!-- Footer -->
            {self._generate_email_footer(platform)}
            
        </body>
        </html>
        """
        
        text_body = f"""
        Dispute Resolved - Refund Issued - {platform['name']}
        
        Hello {user['first_name']},
        
        Great news! The dispute for your escrow trade has been resolved in your favor. 
        We've processed a full refund to your wallet as per our resolution decision.
        
        RESOLUTION & REFUND DETAILS:
        • Resolution: Buyer Favor (Full Refund)
        • Refund Amount: {refund['formatted_amount']}
        • Dispute ID: {dispute_details.get('dispute_id', 'N/A')}
        • Refund ID: {refund['refund_id']}
        • Resolution Date: {refund['created_at']}
        
        RESOLUTION SUMMARY:
        After careful review of all evidence and communications, our dispute resolution team determined that 
        the trade terms were not fulfilled as agreed. As a result, you are entitled to a full refund of your payment.
        {dispute_details.get('admin_notes', '')}
        
        NEXT STEPS:
        • Check wallet balance: {platform['webapp_url']}/wallet
        • View transaction history: {platform['webapp_url']}/transactions
        • Contact support: {platform['support_email']}
        
        THANK YOU:
        We appreciate your patience during the dispute resolution process. Our goal is always to ensure 
        fair and secure transactions for all users. We hope you'll continue to use {platform['name']} for your trading needs.
        
        ---
        This is an automated message from {platform['name']} notification system.
        """
        
        return {
            "subject": subject,
            "html_body": html_body,
            "text_body": text_body
        }
    
    def _generate_post_timeout_payment_refund(self, context: Dict[str, Any]) -> Dict[str, str]:
        """Generate email template for post-timeout payment refunds"""
        refund = context["refund"]
        user = context["user"]
        platform = context["platform"]
        payment_details = context.get("payment_details", {})
        
        subject = f"⏰ Post-Timeout Payment Refund - {refund['formatted_amount']} - {platform['name']}"
        
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Post-Timeout Payment Refund</title>
        </head>
        <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc; line-height: 1.6;">
            
            <!-- Header -->
            <table width="100%" cellpadding="0" cellspacing="0" style="background-color: {self.error_color};">
                <tr>
                    <td align="center" style="padding: 30px 20px;">
                        <h1 style="color: white; margin: 0; font-size: 28px; font-weight: bold;">
                            ⏰ Post-Timeout Payment Refund
                        </h1>
                        <p style="color: #fecaca; margin: 10px 0 0 0; font-size: 18px;">
                            {platform['name']} - Secure Trading Platform
                        </p>
                    </td>
                </tr>
            </table>
            
            <!-- Main Content -->
            <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                    <td align="center" style="padding: 0 20px;">
                        <table width="100%" max-width="600" cellpadding="0" cellspacing="0" style="background-color: white; margin: 20px 0; border-radius: 12px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
                            
                            <!-- Urgent Notice -->
                            <tr>
                                <td style="padding: 20px 30px;">
                                    <div style="background-color: #fef2f2; border: 2px solid {self.error_color}; border-radius: 8px; padding: 20px; text-align: center;">
                                        <h2 style="color: {self.error_color}; margin: 0 0 10px 0; font-size: 20px;">
                                            🚨 Payment After Timeout Detected
                                        </h2>
                                        <p style="color: #7f1d1d; margin: 0; font-size: 14px; font-weight: 600;">
                                            AUTOMATIC REFUND PROCESSED FOR YOUR PROTECTION
                                        </p>
                                    </div>
                                </td>
                            </tr>
                            
                            <!-- Greeting -->
                            <tr>
                                <td style="padding: 0 30px 20px 30px;">
                                    <h2 style="color: {self.brand_color}; margin: 0 0 20px 0; font-size: 24px;">
                                        Hello {user['first_name']},
                                    </h2>
                                    <p style="color: #374151; font-size: 16px; margin: 0 0 20px 0;">
                                        We detected a payment you made after the order timeout period had expired. To protect your interests, 
                                        we've automatically processed a full refund to your wallet.
                                    </p>
                                </td>
                            </tr>
                            
                            <!-- Payment & Refund Details -->
                            <tr>
                                <td style="padding: 0 30px 20px 30px;">
                                    <div style="background-color: #fff1f2; border: 1px solid {self.error_color}; border-radius: 8px; padding: 25px;">
                                        <h3 style="color: {self.error_color}; margin: 0 0 15px 0; font-size: 20px;">
                                            💰 Payment & Refund Details
                                        </h3>
                                        
                                        <table width="100%" cellpadding="0" cellspacing="0" style="font-size: 15px;">
                                            <tr style="border-bottom: 1px solid #fecaca;">
                                                <td style="padding: 10px 0; color: #b91c1c; font-weight: 600;">Payment Amount:</td>
                                                <td style="padding: 10px 0; color: #1f2937; font-weight: bold; text-align: right; font-size: 18px;">
                                                    {refund['formatted_amount']}
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #fecaca;">
                                                <td style="padding: 10px 0; color: #b91c1c; font-weight: 600;">Payment Received:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right;">
                                                    {payment_details.get('payment_time', 'Recently')}
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #fecaca;">
                                                <td style="padding: 10px 0; color: #b91c1c; font-weight: 600;">Order Timeout:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right;">
                                                    {payment_details.get('timeout_time', 'Earlier')}
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #fecaca;">
                                                <td style="padding: 10px 0; color: #b91c1c; font-weight: 600;">Refund ID:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right; font-family: monospace;">
                                                    {refund['refund_id']}
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 10px 0; color: #b91c1c; font-weight: 600;">Refund Date:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right;">
                                                    {refund['created_at']}
                                                </td>
                                            </tr>
                                        </table>
                                    </div>
                                </td>
                            </tr>
                            
                            <!-- Why This Happened -->
                            <tr>
                                <td style="padding: 0 30px 20px 30px;">
                                    <div style="background-color: #fffbeb; border: 1px solid {self.warning_color}; border-radius: 8px; padding: 20px;">
                                        <h3 style="color: {self.warning_color}; margin: 0 0 12px 0; font-size: 18px;">
                                            ❓ Why This Happened
                                        </h3>
                                        <p style="color: #92400e; margin: 0 0 15px 0; font-size: 14px; line-height: 1.5;">
                                            <strong>Timeline Issue:</strong> Your payment arrived after the order's timeout period had already expired. 
                                            This can happen due to:
                                        </p>
                                        <ul style="color: #92400e; margin: 0; padding-left: 20px; font-size: 14px; line-height: 1.5;">
                                            <li>Network delays in blockchain confirmations</li>
                                            <li>Payment processor delays</li>
                                            <li>Wallet synchronization issues</li>
                                            <li>Timing miscommunication</li>
                                        </ul>
                                    </div>
                                </td>
                            </tr>
                            
                            <!-- Protection Measures -->
                            <tr>
                                <td style="padding: 0 30px 20px 30px;">
                                    <div style="background-color: #f0f9ff; border: 1px solid {self.brand_color}; border-radius: 8px; padding: 20px;">
                                        <h3 style="color: {self.brand_color}; margin: 0 0 12px 0; font-size: 18px;">
                                            🛡️ Your Protection
                                        </h3>
                                        <p style="color: #1e40af; margin: 0; font-size: 14px; line-height: 1.5;">
                                            Our automated systems detected this timing issue and immediately processed a refund to protect you from 
                                            potential losses. This ensures you won't lose money on expired orders and maintains the integrity of our timeout system.
                                        </p>
                                    </div>
                                </td>
                            </tr>
                            
                            <!-- Current Wallet Balance -->
                            {self._generate_wallet_balance_section(context)}
                            
                            <!-- Action Buttons -->
                            <tr>
                                <td style="padding: 20px 30px;">
                                    <table width="100%" cellpadding="0" cellspacing="0">
                                        <tr>
                                            <td align="center" style="padding: 0 10px 15px 0;">
                                                <a href="{platform['webapp_url']}/exchange/create" 
                                                   style="display: inline-block; background-color: {self.success_color}; color: white; padding: 14px 24px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px;">
                                                    🔄 Create New Order
                                                </a>
                                            </td>
                                            <td align="center" style="padding: 0 0 15px 10px;">
                                                <a href="{platform['webapp_url']}/wallet" 
                                                   style="display: inline-block; background-color: {self.brand_color}; color: white; padding: 14px 24px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px;">
                                                    💰 Check Wallet
                                                </a>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                            
                            <!-- Prevention Tips -->
                            <tr>
                                <td style="padding: 20px 30px 30px 30px;">
                                    <div style="background-color: #f9fafb; border-radius: 8px; padding: 20px;">
                                        <h3 style="color: {self.brand_color}; margin: 0 0 12px 0; font-size: 18px;">
                                            💡 Prevention Tips for Future Orders
                                        </h3>
                                        <ul style="color: #374151; margin: 0; padding-left: 20px; font-size: 14px; line-height: 1.6;">
                                            <li><strong>Pay promptly:</strong> Complete payments within the specified time window</li>
                                            <li><strong>Check confirmations:</strong> Ensure your payment is confirmed before timeout</li>
                                            <li><strong>Monitor timers:</strong> Keep track of remaining time in your dashboard</li>
                                            <li><strong>Contact support:</strong> Reach out immediately if you encounter delays</li>
                                        </ul>
                                    </div>
                                </td>
                            </tr>
                            
                        </table>
                    </td>
                </tr>
            </table>
            
            <!-- Footer -->
            {self._generate_email_footer(platform)}
            
        </body>
        </html>
        """
        
        text_body = f"""
        Post-Timeout Payment Refund - {platform['name']}
        
        🚨 PAYMENT AFTER TIMEOUT DETECTED - AUTOMATIC REFUND PROCESSED FOR YOUR PROTECTION
        
        Hello {user['first_name']},
        
        We detected a payment you made after the order timeout period had expired. To protect your interests, 
        we've automatically processed a full refund to your wallet.
        
        PAYMENT & REFUND DETAILS:
        • Payment Amount: {refund['formatted_amount']}
        • Payment Received: {payment_details.get('payment_time', 'Recently')}
        • Order Timeout: {payment_details.get('timeout_time', 'Earlier')}
        • Refund ID: {refund['refund_id']}
        • Refund Date: {refund['created_at']}
        
        WHY THIS HAPPENED:
        Your payment arrived after the order's timeout period had already expired. This can happen due to:
        • Network delays in blockchain confirmations
        • Payment processor delays  
        • Wallet synchronization issues
        • Timing miscommunication
        
        YOUR PROTECTION:
        Our automated systems detected this timing issue and immediately processed a refund to protect you from 
        potential losses. This ensures you won't lose money on expired orders and maintains the integrity of our timeout system.
        
        NEXT STEPS:
        • Create new order: {platform['webapp_url']}/exchange/create
        • Check wallet: {platform['webapp_url']}/wallet
        • Contact support: {platform['support_email']}
        
        PREVENTION TIPS FOR FUTURE ORDERS:
        • Pay promptly: Complete payments within the specified time window
        • Check confirmations: Ensure your payment is confirmed before timeout
        • Monitor timers: Keep track of remaining time in your dashboard
        • Contact support: Reach out immediately if you encounter delays
        
        ---
        This is an automated message from {platform['name']} notification system.
        """
        
        return {
            "subject": subject,
            "html_body": html_body,
            "text_body": text_body
        }
    
    def _generate_system_error_refund(self, context: Dict[str, Any]) -> Dict[str, str]:
        """Generate email template for system error refunds"""
        refund = context["refund"]
        user = context["user"]
        platform = context["platform"]
        
        subject = f"🔧 System Error Refund - {refund['formatted_amount']} - {platform['name']}"
        
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>System Error Refund</title>
        </head>
        <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc; line-height: 1.6;">
            
            <!-- Header -->
            <table width="100%" cellpadding="0" cellspacing="0" style="background-color: {self.error_color};">
                <tr>
                    <td align="center" style="padding: 30px 20px;">
                        <h1 style="color: white; margin: 0; font-size: 28px; font-weight: bold;">
                            🔧 System Error Refund
                        </h1>
                        <p style="color: #fecaca; margin: 10px 0 0 0; font-size: 18px;">
                            {platform['name']} - We're Making It Right
                        </p>
                    </td>
                </tr>
            </table>
            
            <!-- Main Content -->
            <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                    <td align="center" style="padding: 0 20px;">
                        <table width="100%" max-width="600" cellpadding="0" cellspacing="0" style="background-color: white; margin: 20px 0; border-radius: 12px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
                            
                            <!-- Apology -->
                            <tr>
                                <td style="padding: 30px;">
                                    <h2 style="color: {self.brand_color}; margin: 0 0 20px 0; font-size: 24px;">
                                        We Sincerely Apologize, {user['first_name']}
                                    </h2>
                                    <p style="color: #374151; font-size: 16px; margin: 0 0 20px 0;">
                                        A technical error occurred during your transaction processing. We take full responsibility and have 
                                        immediately processed a refund to your wallet. Your funds were never at risk.
                                    </p>
                                </td>
                            </tr>
                            
                            <!-- Error & Refund Details -->
                            <tr>
                                <td style="padding: 0 30px 20px 30px;">
                                    <div style="background-color: #fff1f2; border: 1px solid {self.error_color}; border-radius: 8px; padding: 25px;">
                                        <h3 style="color: {self.error_color}; margin: 0 0 15px 0; font-size: 20px;">
                                            🔧 Error & Refund Details
                                        </h3>
                                        
                                        <table width="100%" cellpadding="0" cellspacing="0" style="font-size: 15px;">
                                            <tr style="border-bottom: 1px solid #fecaca;">
                                                <td style="padding: 10px 0; color: #b91c1c; font-weight: 600;">Issue Type:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right;">
                                                    System Processing Error
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #fecaca;">
                                                <td style="padding: 10px 0; color: #b91c1c; font-weight: 600;">Refund Amount:</td>
                                                <td style="padding: 10px 0; color: #1f2937; font-weight: bold; text-align: right; font-size: 18px;">
                                                    {refund['formatted_amount']}
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #fecaca;">
                                                <td style="padding: 10px 0; color: #b91c1c; font-weight: 600;">Refund ID:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right; font-family: monospace;">
                                                    {refund['refund_id']}
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #fecaca;">
                                                <td style="padding: 10px 0; color: #b91c1c; font-weight: 600;">Status:</td>
                                                <td style="padding: 10px 0; color: #16a34a; text-align: right; font-weight: bold;">
                                                    ✅ Refund Completed
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 10px 0; color: #b91c1c; font-weight: 600;">Date Processed:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right;">
                                                    {refund['created_at']}
                                                </td>
                                            </tr>
                                        </table>
                                    </div>
                                </td>
                            </tr>
                            
                            <!-- What We're Doing -->
                            <tr>
                                <td style="padding: 0 30px 20px 30px;">
                                    <div style="background-color: #f0f9ff; border: 1px solid {self.brand_color}; border-radius: 8px; padding: 20px;">
                                        <h3 style="color: {self.brand_color}; margin: 0 0 12px 0; font-size: 18px;">
                                            🔍 What We're Doing About It
                                        </h3>
                                        <ul style="color: #1e40af; margin: 0; padding-left: 20px; font-size: 14px; line-height: 1.5;">
                                            <li><strong>Immediate Action:</strong> Your refund was processed automatically</li>
                                            <li><strong>Root Cause Analysis:</strong> Our team is investigating the technical issue</li>
                                            <li><strong>System Improvements:</strong> We're implementing fixes to prevent recurrence</li>
                                            <li><strong>Monitoring:</strong> Enhanced monitoring has been put in place</li>
                                        </ul>
                                    </div>
                                </td>
                            </tr>
                            
                            <!-- Current Wallet Balance -->
                            {self._generate_wallet_balance_section(context)}
                            
                            <!-- Action Buttons -->
                            <tr>
                                <td style="padding: 20px 30px;">
                                    <table width="100%" cellpadding="0" cellspacing="0">
                                        <tr>
                                            <td align="center" style="padding: 0 10px 15px 0;">
                                                <a href="{platform['webapp_url']}/wallet" 
                                                   style="display: inline-block; background-color: {self.success_color}; color: white; padding: 14px 24px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px;">
                                                    💰 Check Wallet Balance
                                                </a>
                                            </td>
                                            <td align="center" style="padding: 0 0 15px 10px;">
                                                <a href="mailto:{platform['support_email']}" 
                                                   style="display: inline-block; background-color: {self.brand_color}; color: white; padding: 14px 24px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px;">
                                                    📞 Contact Support
                                                </a>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                            
                            <!-- Compensation Offer -->
                            <tr>
                                <td style="padding: 20px 30px 30px 30px;">
                                    <div style="background-color: #f0fdf4; border: 1px solid {self.success_color}; border-radius: 8px; padding: 20px; text-align: center;">
                                        <h3 style="color: {self.success_color}; margin: 0 0 12px 0; font-size: 18px;">
                                            🎁 Making It Right
                                        </h3>
                                        <p style="color: #15803d; margin: 0 0 15px 0; font-size: 14px; line-height: 1.5;">
                                            We value your patience and understanding. As an apology for this inconvenience, please contact our support team - 
                                            we'd like to discuss potential compensation for the trouble you've experienced.
                                        </p>
                                        <p style="color: #15803d; margin: 0; font-size: 13px; font-style: italic;">
                                            Reference this email when contacting support: {refund['refund_id']}
                                        </p>
                                    </div>
                                </td>
                            </tr>
                            
                        </table>
                    </td>
                </tr>
            </table>
            
            <!-- Footer -->
            {self._generate_email_footer(platform)}
            
        </body>
        </html>
        """
        
        text_body = f"""
        System Error Refund - {platform['name']}
        
        We Sincerely Apologize, {user['first_name']}
        
        A technical error occurred during your transaction processing. We take full responsibility and have 
        immediately processed a refund to your wallet. Your funds were never at risk.
        
        ERROR & REFUND DETAILS:
        • Issue Type: System Processing Error
        • Refund Amount: {refund['formatted_amount']}
        • Refund ID: {refund['refund_id']}
        • Status: ✅ Refund Completed
        • Date Processed: {refund['created_at']}
        
        WHAT WE'RE DOING ABOUT IT:
        • Immediate Action: Your refund was processed automatically
        • Root Cause Analysis: Our team is investigating the technical issue
        • System Improvements: We're implementing fixes to prevent recurrence
        • Monitoring: Enhanced monitoring has been put in place
        
        NEXT STEPS:
        • Check wallet balance: {platform['webapp_url']}/wallet
        • Contact support: {platform['support_email']}
        
        MAKING IT RIGHT:
        We value your patience and understanding. As an apology for this inconvenience, please contact our support team - 
        we'd like to discuss potential compensation for the trouble you've experienced.
        
        Reference this email when contacting support: {refund['refund_id']}
        
        ---
        This is an automated message from {platform['name']} notification system.
        """
        
        return {
            "subject": subject,
            "html_body": html_body,
            "text_body": text_body
        }
    
    def _generate_refund_processing_confirmation(self, context: Dict[str, Any]) -> Dict[str, str]:
        """Generate email template for refund processing confirmation"""
        refund = context["refund"]
        user = context["user"]
        platform = context["platform"]
        
        subject = f"🔄 Refund Being Processed - {refund['formatted_amount']} - {platform['name']}"
        
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Refund Processing Confirmation</title>
        </head>
        <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc; line-height: 1.6;">
            
            <!-- Header -->
            <table width="100%" cellpadding="0" cellspacing="0" style="background-color: {self.warning_color};">
                <tr>
                    <td align="center" style="padding: 30px 20px;">
                        <h1 style="color: white; margin: 0; font-size: 28px; font-weight: bold;">
                            🔄 Refund Being Processed
                        </h1>
                        <p style="color: #fef3c7; margin: 10px 0 0 0; font-size: 18px;">
                            {platform['name']} - We're Working On It
                        </p>
                    </td>
                </tr>
            </table>
            
            <!-- Main Content -->
            <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                    <td align="center" style="padding: 0 20px;">
                        <table width="100%" max-width="600" cellpadding="0" cellspacing="0" style="background-color: white; margin: 20px 0; border-radius: 12px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
                            
                            <!-- Status Update -->
                            <tr>
                                <td style="padding: 30px;">
                                    <h2 style="color: {self.brand_color}; margin: 0 0 20px 0; font-size: 24px;">
                                        Hi {user['first_name']}!
                                    </h2>
                                    <p style="color: #374151; font-size: 16px; margin: 0 0 20px 0;">
                                        We're currently processing your refund request. This email confirms that we've received your request 
                                        and are working to complete it as quickly as possible.
                                    </p>
                                </td>
                            </tr>
                            
                            <!-- Processing Details -->
                            <tr>
                                <td style="padding: 0 30px 20px 30px;">
                                    <div style="background-color: #fff7ed; border: 1px solid {self.warning_color}; border-radius: 8px; padding: 25px;">
                                        <h3 style="color: {self.warning_color}; margin: 0 0 15px 0; font-size: 20px;">
                                            ⏳ Processing Details
                                        </h3>
                                        
                                        <table width="100%" cellpadding="0" cellspacing="0" style="font-size: 15px;">
                                            <tr style="border-bottom: 1px solid #fed7aa;">
                                                <td style="padding: 10px 0; color: #9a3412; font-weight: 600;">Refund Amount:</td>
                                                <td style="padding: 10px 0; color: #1f2937; font-weight: bold; text-align: right; font-size: 18px;">
                                                    {refund['formatted_amount']}
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #fed7aa;">
                                                <td style="padding: 10px 0; color: #9a3412; font-weight: 600;">Refund ID:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right; font-family: monospace;">
                                                    {refund['refund_id']}
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #fed7aa;">
                                                <td style="padding: 10px 0; color: #9a3412; font-weight: 600;">Status:</td>
                                                <td style="padding: 10px 0; color: {self.warning_color}; text-align: right; font-weight: bold;">
                                                    🔄 Processing
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #fed7aa;">
                                                <td style="padding: 10px 0; color: #9a3412; font-weight: 600;">Expected Time:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right;">
                                                    Within 24 hours
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 10px 0; color: #9a3412; font-weight: 600;">Started:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right;">
                                                    {refund['created_at']}
                                                </td>
                                            </tr>
                                        </table>
                                    </div>
                                </td>
                            </tr>
                            
                            <!-- What Happens Next -->
                            <tr>
                                <td style="padding: 0 30px 30px 30px;">
                                    <div style="background-color: #f0f9ff; border: 1px solid {self.brand_color}; border-radius: 8px; padding: 20px;">
                                        <h3 style="color: {self.brand_color}; margin: 0 0 12px 0; font-size: 18px;">
                                            📋 What Happens Next
                                        </h3>
                                        <ol style="color: #1e40af; margin: 0; padding-left: 20px; font-size: 14px; line-height: 1.6;">
                                            <li><strong>Verification:</strong> Our system verifies the refund eligibility</li>
                                            <li><strong>Processing:</strong> The refund amount is calculated and prepared</li>
                                            <li><strong>Wallet Credit:</strong> Funds are added to your wallet balance</li>
                                            <li><strong>Confirmation:</strong> You'll receive a completion notification</li>
                                        </ol>
                                    </div>
                                </td>
                            </tr>
                            
                        </table>
                    </td>
                </tr>
            </table>
            
            <!-- Footer -->
            {self._generate_email_footer(platform)}
            
        </body>
        </html>
        """
        
        text_body = f"""
        Refund Being Processed - {platform['name']}
        
        Hi {user['first_name']}!
        
        We're currently processing your refund request. This email confirms that we've received your request 
        and are working to complete it as quickly as possible.
        
        PROCESSING DETAILS:
        • Refund Amount: {refund['formatted_amount']}
        • Refund ID: {refund['refund_id']}
        • Status: 🔄 Processing
        • Expected Time: Within 24 hours
        • Started: {refund['created_at']}
        
        WHAT HAPPENS NEXT:
        1. Verification: Our system verifies the refund eligibility
        2. Processing: The refund amount is calculated and prepared
        3. Wallet Credit: Funds are added to your wallet balance
        4. Confirmation: You'll receive a completion notification
        
        Questions? Contact us at {platform['support_email']}
        
        Thank you for your patience.
        
        ---
        This is an automated message from {platform['name']} notification system.
        """
        
        return {
            "subject": subject,
            "html_body": html_body,
            "text_body": text_body
        }
    
    def _generate_refund_completed_confirmation(self, context: Dict[str, Any]) -> Dict[str, str]:
        """Generate email template for refund completion confirmation"""
        refund = context["refund"]
        user = context["user"]
        platform = context["platform"]
        
        subject = f"✅ Refund Completed - {refund['formatted_amount']} Added to Wallet - {platform['name']}"
        
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Refund Completed</title>
        </head>
        <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc; line-height: 1.6;">
            
            <!-- Header -->
            <table width="100%" cellpadding="0" cellspacing="0" style="background-color: {self.success_color};">
                <tr>
                    <td align="center" style="padding: 30px 20px;">
                        <h1 style="color: white; margin: 0; font-size: 28px; font-weight: bold;">
                            ✅ Refund Completed Successfully
                        </h1>
                        <p style="color: #d1fae5; margin: 10px 0 0 0; font-size: 18px;">
                            {platform['name']} - Your Funds Are Ready
                        </p>
                    </td>
                </tr>
            </table>
            
            <!-- Main Content -->
            <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                    <td align="center" style="padding: 0 20px;">
                        <table width="100%" max-width="600" cellpadding="0" cellspacing="0" style="background-color: white; margin: 20px 0; border-radius: 12px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
                            
                            <!-- Success Message -->
                            <tr>
                                <td style="padding: 30px;">
                                    <div style="text-align: center; background-color: #f0fdf4; border-radius: 8px; padding: 25px; margin-bottom: 20px;">
                                        <div style="font-size: 48px; margin: 0 0 15px 0;">🎉</div>
                                        <h2 style="color: {self.success_color}; margin: 0 0 10px 0; font-size: 24px;">
                                            Great News, {user['first_name']}!
                                        </h2>
                                        <p style="color: #15803d; font-size: 18px; margin: 0; font-weight: 600;">
                                            Your refund has been completed successfully
                                        </p>
                                    </div>
                                </td>
                            </tr>
                            
                            <!-- Completion Details -->
                            <tr>
                                <td style="padding: 0 30px 20px 30px;">
                                    <div style="background-color: #f0fdf4; border: 1px solid {self.success_color}; border-radius: 8px; padding: 25px;">
                                        <h3 style="color: {self.success_color}; margin: 0 0 15px 0; font-size: 20px;">
                                            💰 Refund Completion Details
                                        </h3>
                                        
                                        <table width="100%" cellpadding="0" cellspacing="0" style="font-size: 15px;">
                                            <tr style="border-bottom: 1px solid #bbf7d0;">
                                                <td style="padding: 10px 0; color: #15803d; font-weight: 600;">Amount Refunded:</td>
                                                <td style="padding: 10px 0; color: #1f2937; font-weight: bold; text-align: right; font-size: 20px;">
                                                    {refund['formatted_amount']}
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #bbf7d0;">
                                                <td style="padding: 10px 0; color: #15803d; font-weight: 600;">Refund ID:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right; font-family: monospace;">
                                                    {refund['refund_id']}
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #bbf7d0;">
                                                <td style="padding: 10px 0; color: #15803d; font-weight: 600;">Status:</td>
                                                <td style="padding: 10px 0; color: {self.success_color}; text-align: right; font-weight: bold;">
                                                    ✅ Completed
                                                </td>
                                            </tr>
                                            <tr style="border-bottom: 1px solid #bbf7d0;">
                                                <td style="padding: 10px 0; color: #15803d; font-weight: 600;">Added to Wallet:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right;">
                                                    {refund.get('completed_at') or refund['created_at']}
                                                </td>
                                            </tr>
                                            <tr>
                                                <td style="padding: 10px 0; color: #15803d; font-weight: 600;">Available for:</td>
                                                <td style="padding: 10px 0; color: #1f2937; text-align: right; font-weight: 600;">
                                                    Immediate Use ✅
                                                </td>
                                            </tr>
                                        </table>
                                    </div>
                                </td>
                            </tr>
                            
                            <!-- Current Wallet Balance -->
                            {self._generate_wallet_balance_section(context)}
                            
                            <!-- Action Buttons -->
                            <tr>
                                <td style="padding: 20px 30px;">
                                    <table width="100%" cellpadding="0" cellspacing="0">
                                        <tr>
                                            <td align="center" style="padding: 0 10px 15px 0;">
                                                <a href="{platform['webapp_url']}/wallet" 
                                                   style="display: inline-block; background-color: {self.success_color}; color: white; padding: 14px 24px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px;">
                                                    💰 View Wallet
                                                </a>
                                            </td>
                                            <td align="center" style="padding: 0 0 15px 10px;">
                                                <a href="{platform['webapp_url']}/transactions" 
                                                   style="display: inline-block; background-color: {self.brand_color}; color: white; padding: 14px 24px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px;">
                                                    📊 Transaction History
                                                </a>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                            
                            <!-- What You Can Do Now -->
                            <tr>
                                <td style="padding: 20px 30px 30px 30px;">
                                    <div style="background-color: #eff6ff; border-radius: 8px; padding: 20px;">
                                        <h3 style="color: {self.brand_color}; margin: 0 0 12px 0; font-size: 18px;">
                                            🚀 What You Can Do Now
                                        </h3>
                                        <ul style="color: #1e40af; margin: 0; padding-left: 20px; font-size: 14px; line-height: 1.6;">
                                            <li><strong>Use immediately:</strong> Your refunded funds are ready for instant use</li>
                                            <li><strong>New trades:</strong> Start new escrow trades with your available balance</li>
                                            <li><strong>Cashout:</strong> Request withdrawal to your external accounts</li>
                                            <li><strong>Exchange:</strong> Convert to different currencies if needed</li>
                                        </ul>
                                    </div>
                                </td>
                            </tr>
                            
                        </table>
                    </td>
                </tr>
            </table>
            
            <!-- Footer -->
            {self._generate_email_footer(platform)}
            
        </body>
        </html>
        """
        
        text_body = f"""
        ✅ Refund Completed Successfully - {platform['name']}
        
        🎉 Great News, {user['first_name']}!
        Your refund has been completed successfully
        
        REFUND COMPLETION DETAILS:
        • Amount Refunded: {refund['formatted_amount']}
        • Refund ID: {refund['refund_id']}
        • Status: ✅ Completed
        • Added to Wallet: {refund.get('completed_at') or refund['created_at']}
        • Available for: Immediate Use ✅
        
        WHAT YOU CAN DO NOW:
        • Use immediately: Your refunded funds are ready for instant use
        • New trades: Start new escrow trades with your available balance
        • Cashout: Request withdrawal to your external accounts
        • Exchange: Convert to different currencies if needed
        
        NEXT STEPS:
        • View wallet: {platform['webapp_url']}/wallet
        • Transaction history: {platform['webapp_url']}/transactions
        
        Thank you for using {platform['name']}!
        
        ---
        This is an automated message from {platform['name']} notification system.
        """
        
        return {
            "subject": subject,
            "html_body": html_body,
            "text_body": text_body
        }
    
    def _generate_generic_refund_template(self, context: Dict[str, Any]) -> Dict[str, str]:
        """Generate generic refund template as fallback"""
        refund = context["refund"]
        user = context["user"]
        platform = context["platform"]
        
        subject = f"🔄 Refund Notification - {refund['formatted_amount']} - {platform['name']}"
        
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Refund Notification</title>
        </head>
        <body style="margin: 0; padding: 0; font-family: Arial, sans-serif; background-color: #f8fafc; line-height: 1.6;">
            
            <!-- Header -->
            <table width="100%" cellpadding="0" cellspacing="0" style="background-color: {self.brand_color};">
                <tr>
                    <td align="center" style="padding: 30px 20px;">
                        <h1 style="color: white; margin: 0; font-size: 28px; font-weight: bold;">
                            🔄 Refund Processed
                        </h1>
                        <p style="color: #e2e8f0; margin: 10px 0 0 0; font-size: 18px;">
                            {platform['name']}
                        </p>
                    </td>
                </tr>
            </table>
            
            <!-- Main Content -->
            <table width="100%" cellpadding="0" cellspacing="0">
                <tr>
                    <td align="center" style="padding: 20px;">
                        <table width="100%" max-width="600" cellpadding="0" cellspacing="0" style="background-color: white; border-radius: 12px; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);">
                            
                            <tr>
                                <td style="padding: 30px;">
                                    <h2 style="color: {self.brand_color}; margin: 0 0 20px 0;">
                                        Hello {user['first_name']}!
                                    </h2>
                                    
                                    <p style="color: #374151; font-size: 16px; margin: 0 0 20px 0;">
                                        We have processed a refund for your account.
                                    </p>
                                    
                                    <div style="background-color: #f0f9ff; border-radius: 8px; padding: 20px; margin: 20px 0;">
                                        <h3 style="color: {self.brand_color}; margin: 0 0 15px 0;">Refund Details</h3>
                                        <p><strong>Amount:</strong> {refund['formatted_amount']}</p>
                                        <p><strong>Refund ID:</strong> {refund['refund_id']}</p>
                                        <p><strong>Type:</strong> {refund['type_display']}</p>
                                        <p><strong>Reason:</strong> {refund['reason']}</p>
                                        <p><strong>Date:</strong> {refund['created_at']}</p>
                                    </div>
                                    
                                    <p style="color: #374151; font-size: 14px;">
                                        The refund has been added to your wallet balance and is available for immediate use.
                                    </p>
                                    
                                    <div style="text-align: center; margin: 30px 0;">
                                        <a href="{platform['webapp_url']}/wallet" 
                                           style="display: inline-block; background-color: {self.brand_color}; color: white; padding: 14px 28px; text-decoration: none; border-radius: 8px; font-weight: 600;">
                                            Check Wallet Balance
                                        </a>
                                    </div>
                                    
                                </td>
                            </tr>
                            
                        </table>
                    </td>
                </tr>
            </table>
            
            <!-- Footer -->
            {self._generate_email_footer(platform)}
            
        </body>
        </html>
        """
        
        text_body = f"""
        Refund Processed - {platform['name']}
        
        Hello {user['first_name']},
        
        We have processed a refund for your account.
        
        REFUND DETAILS:
        • Amount: {refund['formatted_amount']}
        • Refund ID: {refund['refund_id']}
        • Type: {refund['type_display']}
        • Reason: {refund['reason']}
        • Date: {refund['created_at']}
        
        The refund has been added to your wallet balance and is available for immediate use.
        
        Check your wallet: {platform['webapp_url']}/wallet
        
        If you have questions, contact support: {platform['support_email']}
        
        ---
        This is an automated message from {platform['name']} notification system.
        """
        
        return {
            "subject": subject,
            "html_body": html_body,
            "text_body": text_body
        }
    
    def _generate_error_fallback_template(
        self, 
        context: Dict[str, Any], 
        template_id: str, 
        error_message: str
    ) -> Dict[str, str]:
        """Generate error fallback template when template generation fails"""
        user = context.get("user", {})
        platform = context.get("platform", {"name": "Trading Platform"})
        context.get("refund", {})
        
        subject = f"⚠️ Important Account Update - {platform['name']}"
        
        html_body = f"""
        <!DOCTYPE html>
        <html>
        <head><title>Important Account Update</title></head>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: #f59e0b; color: white; padding: 20px; text-align: center;">
                <h1>⚠️ Important Account Update</h1>
                <p>{platform['name']}</p>
            </div>
            <div style="padding: 20px;">
                <p>Hello {user.get('first_name', 'User')},</p>
                <p>We have an important update regarding your account. Please log in to your account for details.</p>
                <p>If you have any questions, please contact our support team.</p>
                <div style="text-align: center; margin: 20px 0;">
                    <a href="{platform.get('webapp_url', '#')}" 
                       style="background-color: #1e3a8a; color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px;">
                        View Account
                    </a>
                </div>
                <p>Support: {platform.get('support_email', 'support@example.com')}</p>
            </div>
        </body>
        </html>
        """
        
        text_body = f"""
        Important Account Update - {platform['name']}
        
        Hello {user.get('first_name', 'User')},
        
        We have an important update regarding your account. Please log in to your account for details.
        
        If you have any questions, please contact our support team at {platform.get('support_email', 'support@example.com')}.
        
        View your account: {platform.get('webapp_url', '#')}
        
        ---
        This is an automated message from {platform['name']}.
        """
        
        return {
            "subject": subject,
            "html_body": html_body,
            "text_body": text_body
        }
    
    def _generate_wallet_balance_section(self, context: Dict[str, Any]) -> str:
        """Generate wallet balance section for email templates"""
        wallet = context.get("wallet")
        if not wallet:
            return ""
        
        return f"""
        <tr>
            <td style="padding: 0 30px 20px 30px;">
                <div style="background-color: #f0f9ff; border: 1px solid {self.brand_color}; border-radius: 8px; padding: 20px;">
                    <h3 style="color: {self.brand_color}; margin: 0 0 12px 0; font-size: 18px;">
                        💰 Current Wallet Balance
                    </h3>
                    <div style="text-align: center;">
                        <p style="color: #1f2937; margin: 0; font-size: 24px; font-weight: bold;">
                            {wallet['formatted_balance']}
                        </p>
                        <p style="color: #6b7280; margin: 5px 0 0 0; font-size: 14px;">
                            Available for immediate use
                        </p>
                    </div>
                </div>
            </td>
        </tr>
        """
    
    def _generate_email_footer(self, platform: Dict[str, Any]) -> str:
        """Generate consistent email footer"""
        return f"""
        <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #374151; margin-top: 40px;">
            <tr>
                <td align="center" style="padding: 30px 20px;">
                    <h3 style="color: white; margin: 0 0 15px 0; font-size: 18px;">
                        {platform['name']}
                    </h3>
                    <p style="color: #9ca3af; margin: 0 0 15px 0; font-size: 14px;">
                        Secure trading platform you can trust
                    </p>
                    <div style="margin: 20px 0;">
                        <a href="{platform['webapp_url']}" style="color: #60a5fa; text-decoration: none; margin: 0 15px;">Dashboard</a>
                        <a href="{platform['help_url']}" style="color: #60a5fa; text-decoration: none; margin: 0 15px;">Help Center</a>
                        <a href="mailto:{platform['support_email']}" style="color: #60a5fa; text-decoration: none; margin: 0 15px;">Support</a>
                    </div>
                    <p style="color: #6b7280; margin: 0; font-size: 12px;">
                        This is an automated message from {platform['name']} notification system.<br>
                        Please do not reply to this email. For support, contact {platform['support_email']}.
                    </p>
                </td>
            </tr>
        </table>
        """


# Global template service instance
refund_email_templates = RefundEmailTemplates()