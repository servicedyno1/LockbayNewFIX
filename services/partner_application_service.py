"""
Partner Application Service
Handles partner program applications and admin notifications
"""
import logging
from datetime import datetime
from typing import Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from models import PartnerApplication, PartnerApplicationStatus, User
from config import Config
from services.async_email_service import AsyncEmailService

logger = logging.getLogger(__name__)


class PartnerApplicationService:
    """Service for managing partner program applications"""
    
    def __init__(self):
        self.email_service = AsyncEmailService()
        self.admin_email = Config.ADMIN_EMAIL
    
    async def submit_application(
        self,
        session: AsyncSession,
        name: str,
        telegram_handle: str,
        email: str,
        community_type: str,
        audience_size: str,
        primary_region: str,
        monthly_volume: str,
        commission_tier: str,
        goals: str
    ) -> Dict[str, Any]:
        """
        Submit a new partner application
        
        Returns:
            dict: Application details with success status
        """
        try:
            # Ensure telegram handle starts with @
            if not telegram_handle.startswith('@'):
                telegram_handle = f'@{telegram_handle}'
            
            # Create new application
            application = PartnerApplication(
                name=name,
                telegram_handle=telegram_handle,
                email=email,
                community_type=community_type,
                audience_size=audience_size,
                primary_region=primary_region,
                monthly_volume=monthly_volume,
                commission_tier=commission_tier,
                goals=goals,
                status=PartnerApplicationStatus.NEW.value,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            session.add(application)
            await session.flush()
            
            # Send admin notification
            await self._send_admin_notification(application)
            
            await session.commit()
            
            # Send confirmation email to applicant
            await self._send_applicant_confirmation(application)
            
            # Send Telegram notification to applicant if they have a Lockbay account
            await self._send_applicant_telegram_notification(application, session)
            
            logger.info(f"✅ Partner application submitted: {email} (ID: {application.id})")
            
            return {
                "success": True,
                "application_id": application.id,
                "name": name,
                "email": email,
                "telegram_handle": telegram_handle,
                "commission_tier": commission_tier,
                "submitted_at": application.created_at.strftime("%B %d, %Y at %I:%M %p UTC")
            }
            
        except Exception as e:
            logger.error(f"❌ Error submitting partner application: {e}", exc_info=True)
            await session.rollback()
            return {
                "success": False,
                "error": str(e)
            }
    
    async def _send_admin_notification(self, application: PartnerApplication):
        """Send email and Telegram notification to admin about new application"""
        try:
            if not self.admin_email:
                logger.warning("Admin email not configured - skipping notification")
                return
            
            # Format commission tier display
            tier_display = {
                'bronze': '🥉 Bronze (30%)',
                'silver': '🥈 Silver (40%)',
                'gold': '🥇 Gold (50%)'
            }.get(application.commission_tier, application.commission_tier)
            
            # Format community type display
            community_display = {
                'crypto_trading': 'Crypto Trading Group',
                'nft_community': 'NFT Community',
                'gaming': 'Gaming Channel',
                'marketplace': 'Freelance Marketplace',
                'other': 'Other'
            }.get(application.community_type, application.community_type)
            
            subject = f"🤝 NEW Partner Application - {application.name}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: linear-gradient(135deg, #3DBCC0 0%, #2C9DA2 100%); color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <h1 style="margin: 0 0 10px 0;">🤝 New Partner Application</h1>
                    <div style="display: inline-block; background: rgba(255,255,255,0.2); padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold;">
                        NEW APPLICATION
                    </div>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <div style="background: #e8f4fd; padding: 15px; margin-bottom: 20px; border-left: 4px solid #3498db; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px;">📋 Quick Summary</h3>
                        <p style="margin: 0; line-height: 1.6;">
                            <strong>Applicant:</strong> {application.name}<br>
                            <strong>Commission Tier:</strong> {tier_display}<br>
                            <strong>Community:</strong> {community_display} ({application.audience_size})
                        </p>
                    </div>
                    
                    <h2 style="margin: 20px 0 15px 0; font-size: 18px;">👤 Contact Information</h2>
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                        <tr style="border-bottom: 1px solid #e0e0e0;">
                            <td style="padding: 10px 0; font-weight: bold; color: #3DBCC0;">Name:</td>
                            <td style="padding: 10px 0;">{application.name}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #e0e0e0;">
                            <td style="padding: 10px 0; font-weight: bold; color: #3DBCC0;">Telegram:</td>
                            <td style="padding: 10px 0;">{application.telegram_handle}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #e0e0e0;">
                            <td style="padding: 10px 0; font-weight: bold; color: #3DBCC0;">Email:</td>
                            <td style="padding: 10px 0;">{application.email}</td>
                        </tr>
                    </table>
                    
                    <h2 style="margin: 20px 0 15px 0; font-size: 18px;">🏢 Community Details</h2>
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                        <tr style="border-bottom: 1px solid #e0e0e0;">
                            <td style="padding: 10px 0; font-weight: bold; color: #3DBCC0;">Community Type:</td>
                            <td style="padding: 10px 0;">{community_display}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #e0e0e0;">
                            <td style="padding: 10px 0; font-weight: bold; color: #3DBCC0;">Audience Size:</td>
                            <td style="padding: 10px 0;">{application.audience_size}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #e0e0e0;">
                            <td style="padding: 10px 0; font-weight: bold; color: #3DBCC0;">Primary Region:</td>
                            <td style="padding: 10px 0;">{application.primary_region}</td>
                        </tr>
                    </table>
                    
                    <h2 style="margin: 20px 0 15px 0; font-size: 18px;">💼 Business Details</h2>
                    <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
                        <tr style="border-bottom: 1px solid #e0e0e0;">
                            <td style="padding: 10px 0; font-weight: bold; color: #3DBCC0;">Expected Volume:</td>
                            <td style="padding: 10px 0;">{application.monthly_volume}</td>
                        </tr>
                        <tr style="border-bottom: 1px solid #e0e0e0;">
                            <td style="padding: 10px 0; font-weight: bold; color: #3DBCC0;">Preferred Tier:</td>
                            <td style="padding: 10px 0;">{tier_display}</td>
                        </tr>
                    </table>
                    
                    <h2 style="margin: 20px 0 15px 0; font-size: 18px;">🎯 Partnership Goals</h2>
                    <div style="background: #f8f9fa; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                        <p style="margin: 0; line-height: 1.6; color: #333;">{application.goals}</p>
                    </div>
                    
                    <div style="background: #fff3cd; padding: 15px; border-left: 4px solid #ffc107; border-radius: 4px; margin-top: 20px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px; color: #856404;">⏰ Next Steps</h3>
                        <ul style="margin: 0; padding-left: 20px; line-height: 1.8;">
                            <li>Review application details above</li>
                            <li>Contact applicant via Telegram ({application.telegram_handle}) or email</li>
                            <li>Assess partnership fit and commission tier</li>
                            <li>Schedule chat if qualified</li>
                        </ul>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #666; font-size: 12px;">
                    <p>Partner Application System - Lockbay</p>
                    <p>Application ID: {application.id} | Submitted: {application.created_at.strftime("%Y-%m-%d %H:%M UTC")}</p>
                </div>
            </div>
            """
            
            # Send email
            email_sent = await self.email_service.send_email(
                to_email=self.admin_email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Admin email sent for partner application: {application.email}")
            else:
                logger.error(f"❌ Failed to send admin email for partner application: {application.email}")
            
            # Send Telegram notifications to all admins
            await self._send_telegram_admin_notification(application, tier_display, community_display)
            
        except Exception as e:
            logger.error(f"❌ Error sending admin notification for partner application: {e}", exc_info=True)
    
    async def _send_applicant_confirmation(self, application: PartnerApplication):
        """Send confirmation email to applicant about their submission"""
        try:
            # Format commission tier display
            tier_display = {
                'bronze': '🥉 Bronze (30%)',
                'silver': '🥈 Silver (40%)',
                'gold': '🥇 Gold (50%)'
            }.get(application.commission_tier, application.commission_tier)
            
            subject = f"✅ Partner Application Received - #{application.id}"
            
            html_content = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; width: 100%; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
                <div style="background: linear-gradient(135deg, #3DBCC0 0%, #2C9DA2 100%); color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0;">
                    <div style="background: white; display: inline-block; padding: 12px; border-radius: 8px; margin-bottom: 15px;">
                        <img src="https://lockbay.replit.app/static/lockbay-logo.png" alt="Lockbay Logo" style="width: 140px; height: auto; display: block;">
                    </div>
                    <h1 style="margin: 0 0 10px 0;">✅ Application Received!</h1>
                    <div style="display: inline-block; background: rgba(255,255,255,0.2); padding: 8px 16px; border-radius: 20px; font-size: 14px; font-weight: bold;">
                        APPLICATION #{application.id}
                    </div>
                </div>
                
                <div style="background: white; padding: 20px; border-radius: 0 0 8px 8px; border: 1px solid #dee2e6;">
                    <p style="font-size: 16px; line-height: 1.6; color: #333;">
                        Hi <strong>{application.name}</strong>,
                    </p>
                    
                    <p style="font-size: 16px; line-height: 1.6; color: #333;">
                        Thank you for applying to the <strong>Lockbay Partner Program</strong>! We've received your application and are excited to review it.
                    </p>
                    
                    <div style="background: #e8f4fd; padding: 15px; margin: 20px 0; border-left: 4px solid #3498db; border-radius: 4px;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px;">📋 Your Application Summary</h3>
                        <table style="width: 100%; border-collapse: collapse;">
                            <tr>
                                <td style="padding: 8px 0; font-weight: bold; color: #3DBCC0;">Application ID:</td>
                                <td style="padding: 8px 0;">#{application.id}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px 0; font-weight: bold; color: #3DBCC0;">Name:</td>
                                <td style="padding: 8px 0;">{application.name}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px 0; font-weight: bold; color: #3DBCC0;">Email:</td>
                                <td style="padding: 8px 0;">{application.email}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px 0; font-weight: bold; color: #3DBCC0;">Telegram:</td>
                                <td style="padding: 8px 0;">{application.telegram_handle}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px 0; font-weight: bold; color: #3DBCC0;">Commission Tier:</td>
                                <td style="padding: 8px 0;">{tier_display}</td>
                            </tr>
                            <tr>
                                <td style="padding: 8px 0; font-weight: bold; color: #3DBCC0;">Submitted:</td>
                                <td style="padding: 8px 0;">{application.created_at.strftime("%B %d, %Y at %I:%M %p UTC")}</td>
                            </tr>
                        </table>
                    </div>
                    
                    <div style="background: #fff3cd; padding: 15px; border-left: 4px solid #ffc107; border-radius: 4px; margin: 20px 0;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px; color: #856404;">⏰ What Happens Next?</h3>
                        <ul style="margin: 0; padding-left: 20px; line-height: 1.8; color: #856404;">
                            <li><strong>24-48 Hours:</strong> Our team will review your application</li>
                            <li><strong>Contact:</strong> We'll reach you via Telegram ({application.telegram_handle}) or email</li>
                            <li><strong>If Approved:</strong> We'll schedule a chat</li>
                            <li><strong>Onboarding:</strong> You'll receive your branded bot and partner dashboard access</li>
                        </ul>
                    </div>
                    
                    <div style="background: #d1ecf1; padding: 15px; border-left: 4px solid #0c5460; border-radius: 4px; margin: 20px 0;">
                        <h3 style="margin: 0 0 10px 0; font-size: 16px; color: #0c5460;">💡 Helpful Information</h3>
                        <p style="margin: 0; line-height: 1.6; color: #0c5460;">
                            <strong>Keep this email</strong> for your records. Your Application ID is <strong>#{application.id}</strong>
                        </p>
                        <p style="margin: 10px 0 0 0; line-height: 1.6; color: #0c5460;">
                            Have questions? Contact us on Telegram: <a href="https://t.me/LockbayAssist" style="color: #0c5460; font-weight: bold;">@LockbayAssist</a>
                        </p>
                    </div>
                    
                    <div style="text-align: center; margin-top: 30px;">
                        <a href="https://t.me/LockbayAssist" style="display: inline-block; padding: 14px 30px; background: linear-gradient(135deg, #3DBCC0 0%, #2C9DA2 100%); color: white; text-decoration: none; border-radius: 8px; font-weight: 600;">
                            Chat with Support
                        </a>
                    </div>
                </div>
                
                <div style="text-align: center; margin-top: 20px; color: #666; font-size: 12px;">
                    <p>Lockbay Partner Program</p>
                    <p>Secure. Transparent. Rewarding.</p>
                </div>
            </div>
            """
            
            # Send email to applicant
            email_sent = await self.email_service.send_email(
                to_email=application.email,
                subject=subject,
                html_content=html_content
            )
            
            if email_sent:
                logger.info(f"✅ Confirmation email sent to applicant: {application.email}")
            else:
                logger.error(f"❌ Failed to send confirmation email to applicant: {application.email}")
            
        except Exception as e:
            logger.error(f"❌ Error sending applicant confirmation: {e}", exc_info=True)
    
    async def _send_telegram_admin_notification(self, application: PartnerApplication, tier_display: str, community_display: str):
        """Send Telegram notification to admins about new partner application"""
        try:
            # Get bot instance from webhook_server module
            try:
                from webhook_server import _bot_application
                if not _bot_application:
                    logger.warning("Bot application not available for admin Telegram notifications")
                    return
                bot = _bot_application.bot
            except ImportError:
                logger.warning("Could not import bot application for admin notifications")
                return
            
            # Get admin IDs from config
            if not Config.ADMIN_IDS:
                logger.warning("No admin IDs configured for Telegram notifications")
                return
            
            # Format Telegram message
            telegram_message = f"""
🤝 <b>NEW PARTNER APPLICATION</b>

📋 <b>Application #{application.id}</b>

👤 <b>Contact Information</b>
• Name: {application.name}
• Telegram: {application.telegram_handle}
• Email: {application.email}

🏢 <b>Community Details</b>
• Type: {community_display}
• Size: {application.audience_size}
• Region: {application.primary_region}

💼 <b>Business Details</b>
• Expected Volume: {application.monthly_volume}
• Preferred Tier: {tier_display}

🎯 <b>Goals</b>
{application.goals}

⏰ <b>Submitted:</b> {application.created_at.strftime("%B %d, %Y at %I:%M %p UTC")}

<b>Action Required:</b> Review and contact applicant via {application.telegram_handle} or {application.email}
"""

            # Send to all admins
            success_count = 0
            for admin_id in Config.ADMIN_IDS:
                try:
                    await bot.send_message(
                        chat_id=admin_id,
                        text=telegram_message,
                        parse_mode='HTML'
                    )
                    success_count += 1
                    logger.info(f"✅ Telegram notification sent to admin {admin_id} for application #{application.id}")
                except Exception as e:
                    logger.error(f"❌ Failed to send Telegram to admin {admin_id}: {e}")
            
            if success_count > 0:
                logger.info(f"✅ Telegram notifications sent to {success_count}/{len(Config.ADMIN_IDS)} admins")
            
        except Exception as e:
            logger.error(f"❌ Error sending Telegram admin notifications: {e}", exc_info=True)
    
    async def _send_applicant_telegram_notification(self, application: PartnerApplication, session: AsyncSession):
        """Send Telegram notification to applicant if they have a Lockbay account"""
        try:
            # Get bot instance from webhook_server module
            try:
                from webhook_server import _bot_application
                if not _bot_application:
                    logger.warning("Bot application not available for applicant Telegram notifications")
                    return
                bot = _bot_application.bot
            except ImportError:
                logger.warning("Could not import bot application for applicant notifications")
                return
            
            # Look up user by telegram handle
            telegram_handle = application.telegram_handle.lstrip('@')  # Remove @ prefix for DB query
            
            stmt = select(User).where(User.username == telegram_handle)
            result = await session.execute(stmt)
            user = result.scalar_one_or_none()
            
            if not user:
                logger.info(f"ℹ️ Applicant {application.telegram_handle} not found in Lockbay - skipping Telegram notification")
                return
            
            # Format commission tier display
            tier_display = {
                'bronze': '🥉 Bronze (30%)',
                'silver': '🥈 Silver (40%)',
                'gold': '🥇 Gold (50%)'
            }.get(application.commission_tier, application.commission_tier)
            
            # Send Telegram message to applicant
            telegram_message = f"""
✅ <b>Partner Application Received!</b>

Hi <b>{application.name}</b>, thanks for applying! 🤝

<b>Application ID:</b> #{application.id}
<b>Tier:</b> {tier_display}
<b>Status:</b> Under review (24-48 hours)

We'll contact you via {application.telegram_handle} or email. Questions? @LockbayAssist

<i>Confirmation sent to {application.email}</i>
"""
            
            await bot.send_message(
                chat_id=user.telegram_id,
                text=telegram_message,
                parse_mode='HTML'
            )
            
            logger.info(f"✅ Telegram notification sent to applicant {application.telegram_handle} (user_id: {user.telegram_id})")
            
        except Exception as e:
            logger.error(f"❌ Error sending Telegram notification to applicant: {e}", exc_info=True)
