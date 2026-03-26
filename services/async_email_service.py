"""
Async Email Service for High-Performance Email Operations
Optimized email service for onboarding flow to meet <60s completion targets
"""

import logging
import time
from typing import Dict, Any
from concurrent.futures import ThreadPoolExecutor
from services.email import EmailService
from utils.background_task_runner import run_io_task

logger = logging.getLogger(__name__)

class AsyncEmailService:
    """High-performance async email service for onboarding optimization"""
    
    def __init__(self):
        self.email_service = EmailService()
        # Create dedicated thread pool for email operations to avoid blocking
        self.executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="AsyncEmail")
        
    async def send_otp_email_async(
        self, 
        email: str, 
        otp_code: str, 
        purpose: str, 
        user_name: str = "User"
    ) -> Dict[str, Any]:
        """
        Send OTP email asynchronously for maximum performance
        
        Returns:
            Dict with success status and timing info
        """
        try:
            # ARCHITECT FIX: Use safe time measurement
            start_time = time.monotonic()
            
            # Create optimized email content
            html_content = self._create_fast_email_content(
                otp_code=otp_code,
                user_name=user_name,
                purpose=purpose
            )
            
            # ARCHITECT FIX: Use BackgroundTaskRunner for safe async I/O
            success = await run_io_task(
                self._send_email_sync,
                email,
                f"🔐 Your verification code: {otp_code}",
                html_content
            )
            
            duration = time.monotonic() - start_time
            
            if success:
                logger.info(f"✅ ASYNC_EMAIL_SUCCESS: OTP sent to {email} in {duration:.3f}s")
                return {
                    "success": True,
                    "duration": duration,
                    "message": f"Email sent to {email}"
                }
            else:
                logger.error(f"❌ ASYNC_EMAIL_FAILED: Failed to send to {email} after {duration:.3f}s")
                return {
                    "success": False,
                    "duration": duration,
                    "error": "Email sending failed"
                }
                
        except Exception as e:
            logger.error(f"❌ ASYNC_EMAIL_ERROR: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def _send_email_sync(self, email: str, subject: str, html_content: str) -> bool:
        """Synchronous email sending for thread pool execution"""
        try:
            return self.email_service.send_email(
                to_email=email,
                subject=subject,
                html_content=html_content
            )
        except Exception as e:
            logger.error(f"Email send error: {e}")
            return False
    
    async def send_email(self, to_email: str, subject: str, html_content: str) -> bool:
        """
        Send a general email asynchronously
        
        Args:
            to_email: Recipient email address
            subject: Email subject line
            html_content: HTML content of the email
            
        Returns:
            bool: True if email sent successfully
        """
        try:
            success = await run_io_task(
                self._send_email_sync,
                to_email,
                subject,
                html_content
            )
            
            if success:
                logger.info(f"✅ ASYNC_EMAIL_SUCCESS: Email sent to {to_email}")
            else:
                logger.error(f"❌ ASYNC_EMAIL_FAILED: Failed to send to {to_email}")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ ASYNC_EMAIL_ERROR: {e}")
            return False
    
    def _create_fast_email_content(
        self, 
        otp_code: str, 
        user_name: str, 
        purpose: str
    ) -> str:
        """Create optimized, lightweight email content"""
        return f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
            <h2 style="color: #2c3e50;">Hello {user_name}! 👋</h2>
            
            <p style="font-size: 16px; line-height: 1.6;">
                Your verification code for {purpose} is:
            </p>
            
            <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; text-align: center; margin: 20px 0;">
                <h1 style="color: #007bff; font-size: 32px; margin: 0; letter-spacing: 4px;">
                    {otp_code}
                </h1>
            </div>
            
            <p style="color: #6c757d; font-size: 14px;">
                This code expires in 15 minutes. If you didn't request this, please ignore this email.
            </p>
            
            <hr style="border: none; border-top: 1px solid #dee2e6; margin: 30px 0;">
            <p style="color: #6c757d; font-size: 12px; text-align: center;">
                LockBay Secure Escrow Platform
            </p>
        </body>
        </html>
        """
    
    async def health_check(self) -> Dict[str, Any]:
        """Check async email service health"""
        try:
            # ARCHITECT FIX: Use safe time measurement
            start_time = time.monotonic()
            
            # Test email service availability
            if not self.email_service.enabled:
                return {
                    "healthy": False,
                    "error": "Email service disabled"
                }
            
            duration = time.monotonic() - start_time
            
            return {
                "healthy": True,
                "duration": duration,
                "thread_pool_size": self.executor._max_workers,
                "email_service_enabled": self.email_service.enabled
            }
            
        except Exception as e:
            return {
                "healthy": False,
                "error": str(e)
            }
    
    def shutdown(self):
        """Gracefully shutdown the async email service"""
        self.executor.shutdown(wait=True)
        logger.info("🔧 AsyncEmailService shutdown completed")

# Global instance
async_email_service = AsyncEmailService()