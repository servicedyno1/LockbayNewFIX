"""
Background Email Queue Service
High-performance email processing with background job queue to eliminate webhook latency

Features:
- Async email queuing for immediate webhook response
- Background processing with ReplitQueue
- Retry logic for failed email deliveries
- Performance monitoring and error handling

RAILWAY COMPATIBILITY: Gracefully degrades to no-op mode when Replit KV unavailable.
The caller should handle fallback to direct email sending (already implemented in email_verification_service.py).
"""

import logging
from datetime import datetime
from typing import Dict, Any, Optional
from dataclasses import dataclass

try:
    from webhook_queue.redis_queue import ReplitQueue, Priority, QueueConfig
    QUEUE_AVAILABLE = True
except ImportError:
    QUEUE_AVAILABLE = False
    ReplitQueue = None
    Priority = None
    QueueConfig = None

from services.email import EmailService
from config import Config

logger = logging.getLogger(__name__)


@dataclass
class EmailJob:
    """Email job definition for background processing"""
    recipient: str
    subject: str
    html_content: str
    text_content: Optional[str] = None
    purpose: str = "general"
    user_id: Optional[int] = None
    created_at: Optional[datetime] = None


class BackgroundEmailQueue:
    """Background email processing queue for high-performance webhook responses
    
    RAILWAY COMPATIBILITY: When Replit KV Store is unavailable, operates in "no-op" mode.
    Queue operations return failure, triggering the caller's fallback to direct email sending.
    """
    
    QUEUE_NAME = "email_processing"
    MAX_RETRIES = 3
    RETRY_DELAYS = [30, 120, 300]  # 30s, 2m, 5m
    
    def __init__(self):
        self._queue_available = QUEUE_AVAILABLE
        self._noop_mode = False
        
        if QUEUE_AVAILABLE and QueueConfig is not None:
            self.queue_config = QueueConfig()
            self.queue_config.default_queue = self.QUEUE_NAME
            self.queue_config.max_workers = 1  # Single worker to prevent duplicate processing
            self.queue_config.retry_delays = self.RETRY_DELAYS
            self.replit_queue = ReplitQueue(config=self.queue_config)
        else:
            self.queue_config = None
            self.replit_queue = None
            self._noop_mode = True
        
        self.email_service = EmailService()
        self._is_initialized = False
        
        # Performance metrics
        self.metrics = {
            "emails_queued": 0,
            "emails_sent": 0,
            "emails_failed": 0,
            "average_processing_time": 0.0,
            "last_processed": None,
            "noop_mode": self._noop_mode
        }
    
    async def initialize(self) -> bool:
        """Initialize the background email queue system
        
        RAILWAY MODE: Returns True but operates in no-op mode.
        Queue operations will fail gracefully, triggering direct email fallback.
        """
        try:
            if self._is_initialized:
                return True
            
            # No-op mode for Railway (Replit KV unavailable)
            if self._noop_mode:
                self._is_initialized = True
                logger.info("📧 Background Email Queue initialized in NO-OP mode (Railway)")
                logger.info("⚠️ Emails will be sent directly instead of queued (fallback mode)")
                return True
                
            logger.info("🚀 Initializing Background Email Queue...")
            
            # Connect to queue
            await self.replit_queue.connect()
            
            # Register email processing function
            self.replit_queue.register_function("send_email_background", self._process_email_job)
            
            # Start background workers
            await self.replit_queue.start_workers([self.QUEUE_NAME])
            
            self._is_initialized = True
            logger.info("✅ Background Email Queue initialized successfully")
            return True
            
        except Exception as e:
            # On failure, switch to no-op mode instead of failing completely
            logger.warning(f"⚠️ Background Email Queue falling back to NO-OP mode: {e}")
            self._noop_mode = True
            self._is_initialized = True
            self.metrics["noop_mode"] = True
            return True  # Return True so startup continues
    
    async def queue_otp_email(
        self,
        recipient: str,
        otp_code: str,
        purpose: str = "registration",
        user_id: Optional[int] = None,
        user_name: str = "User"
    ) -> Dict[str, Any]:
        """
        Queue OTP email for background processing - immediate return for webhook performance
        
        Args:
            recipient: Email address to send to
            otp_code: OTP verification code
            purpose: Purpose of verification (registration, cashout, etc.)
            user_id: Optional user ID for tracking
            user_name: User's display name
            
        Returns:
            Dict with immediate response and job ID
            
        RAILWAY MODE: Returns failure in no-op mode to trigger direct email fallback.
        """
        # No-op mode: Return failure to trigger direct email fallback
        if self._noop_mode or self.replit_queue is None:
            logger.info(f"📧 NO-OP mode: Triggering direct email fallback for {recipient}")
            # IMPORTANT: For testing or fallback, we need to return specific success to avoid infinite loops
            # if the caller handles fallback correctly.
            return {
                "success": False,
                "error": "queue_unavailable",
                "message": "Queue in no-op mode, use direct email sending"
            }
        
        try:
            # Create optimized email content
            subject = f"🔐 Your verification code: {otp_code}"
            html_content = self._create_otp_email_content(
                otp_code=otp_code,
                user_name=user_name,
                purpose=purpose
            )
            
            # Create email job
            email_job = EmailJob(
                recipient=recipient,
                subject=subject,
                html_content=html_content,
                purpose=purpose,
                user_id=user_id,
                created_at=datetime.utcnow()
            )
            
            # Queue for background processing
            job_id = await self.replit_queue.enqueue(
                func_name="send_email_background",
                args=[email_job.__dict__],
                queue=self.QUEUE_NAME,
                priority=Priority.HIGH,  # OTP emails are high priority
                max_retries=self.MAX_RETRIES
            )
            
            # Update metrics
            self.metrics["emails_queued"] += 1
            
            logger.info(f"📤 OTP email queued for {recipient} - Job ID: {job_id}")
            
            return {
                "success": True,
                "job_id": job_id,
                "message": "Email queued for background processing",
                "queued_at": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to queue OTP email for {recipient}: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": "Failed to queue email for processing"
            }
    
    async def queue_welcome_email(
        self,
        recipient: str,
        user_name: str,
        user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Queue welcome email for background processing"""
        if self._noop_mode or self.replit_queue is None:
            logger.debug(f"📧 NO-OP mode: Triggering fallback for welcome email to {recipient}")
            return {
                "success": False,
                "error": "queue_unavailable",
                "message": "Queue in no-op mode"
            }
        try:
            # Create welcome email content
            subject = f"🎉 Welcome to {Config.PLATFORM_NAME}!"
            html_content = self._create_welcome_email_content(user_name)
            
            # Create email job
            email_job = EmailJob(
                recipient=recipient,
                subject=subject,
                html_content=html_content,
                purpose="welcome",
                user_id=user_id,
                created_at=datetime.utcnow()
            )
            
            # Queue for background processing
            job_id = await self.replit_queue.enqueue(
                func_name="send_email_background",
                args=[email_job.__dict__],
                queue=self.QUEUE_NAME,
                priority=Priority.NORMAL,  # Welcome emails are normal priority
                max_retries=self.MAX_RETRIES
            )
            
            # Update metrics
            self.metrics["emails_queued"] += 1
            
            logger.info(f"📤 Welcome email queued for {recipient} - Job ID: {job_id}")
            
            return {
                "success": True,
                "job_id": job_id,
                "message": "Welcome email queued for background processing"
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to queue welcome email for {recipient}: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def queue_dispute_notification_email(
        self,
        recipient: str,
        dispute_id: int,
        escrow_id: str,
        escrow_amount: float,
        sender_info: str,
        sender_role: str,
        current_message: str,
        dispute_status: str,
        action_urls: Dict[str, str],
        admin_panel_url: str,
        user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Queue dispute notification email for background processing - immediate return for performance
        
        Args:
            recipient: Email address to send to (admin or party email)
            dispute_id: ID of the dispute
            escrow_id: ID of the escrow transaction
            escrow_amount: Amount in escrow
            sender_info: Sender's username or identifier
            sender_role: Role of sender (Buyer/Seller/Admin)
            current_message: The current dispute message
            dispute_status: Current status of the dispute
            action_urls: Dict with action button URLs (release_url, refund_url, split_url, custom_split_url)
            admin_panel_url: URL to view full dispute history
            user_id: Optional user ID for tracking
            
        Returns:
            Dict with immediate response and job ID
        """
        if self._noop_mode or self.replit_queue is None:
            logger.debug(f"📧 NO-OP mode: Triggering fallback for dispute notification to {recipient}")
            return {
                "success": False,
                "error": "queue_unavailable",
                "message": "Queue in no-op mode"
            }
        try:
            # Create optimized dispute notification email content
            subject = f"⚖️ Dispute Message: #{dispute_id} | {sender_role} | ${escrow_amount:.2f}"
            html_content = self._create_dispute_notification_content(
                dispute_id=dispute_id,
                escrow_id=escrow_id,
                escrow_amount=escrow_amount,
                sender_info=sender_info,
                sender_role=sender_role,
                current_message=current_message,
                dispute_status=dispute_status,
                action_urls=action_urls,
                admin_panel_url=admin_panel_url
            )
            
            # Create email job
            email_job = EmailJob(
                recipient=recipient,
                subject=subject,
                html_content=html_content,
                purpose="dispute_notification",
                user_id=user_id,
                created_at=datetime.utcnow()
            )
            
            # Queue for background processing
            job_id = await self.replit_queue.enqueue(
                func_name="send_email_background",
                args=[email_job.__dict__],
                queue=self.QUEUE_NAME,
                priority=Priority.HIGH,  # Dispute notifications are high priority
                max_retries=self.MAX_RETRIES
            )
            
            # Update metrics
            self.metrics["emails_queued"] += 1
            
            logger.info(f"📤 Dispute notification email queued for {recipient} (Dispute #{dispute_id}) - Job ID: {job_id}")
            
            return {
                "success": True,
                "job_id": job_id,
                "message": "Dispute notification email queued for background processing",
                "queued_at": datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"❌ Failed to queue dispute notification email for {recipient}: {e}")
            return {
                "success": False,
                "error": str(e),
                "message": "Failed to queue dispute notification email"
            }
    
    async def _process_email_job(self, email_job_data: Dict[str, Any]) -> Dict[str, Any]:
        """Background job processor for email sending"""
        # Ensure asyncio is available in this context
        start_time = datetime.utcnow()
        
        try:
            # Reconstruct email job from data
            email_job = EmailJob(**email_job_data)
            
            logger.info(f"📧 Processing background email job for {email_job.recipient}")
            
            # Send email using the email service
            success = await self.email_service.send_email_async(
                to_email=email_job.recipient,
                subject=email_job.subject,
                html_content=email_job.html_content,
                text_content=email_job.text_content
            )
            
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            
            if success:
                # Update metrics
                self.metrics["emails_sent"] += 1
                self.metrics["last_processed"] = datetime.utcnow().isoformat()
                
                # Update average processing time
                total_processed = self.metrics["emails_sent"] + self.metrics["emails_failed"]
                if total_processed > 0:
                    current_avg = self.metrics["average_processing_time"]
                    self.metrics["average_processing_time"] = (
                        (current_avg * (total_processed - 1) + processing_time) / total_processed
                    )
                
                logger.info(f"✅ Email sent successfully to {email_job.recipient} in {processing_time:.3f}s")
                return {
                    "success": True,
                    "recipient": email_job.recipient,
                    "processing_time": processing_time,
                    "sent_at": datetime.utcnow().isoformat()
                }
            else:
                # Update failure metrics
                self.metrics["emails_failed"] += 1
                
                logger.error(f"❌ Email sending failed for {email_job.recipient}")
                raise Exception("Email service returned failure")
                
        except Exception as e:
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            self.metrics["emails_failed"] += 1
            
            logger.error(f"❌ Background email processing failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "processing_time": processing_time,
                "failed_at": datetime.utcnow().isoformat()
            }
    
    def _create_otp_email_content(
        self,
        otp_code: str,
        user_name: str,
        purpose: str
    ) -> str:
        """Create optimized OTP email content"""
        purpose_configs = {
            'registration': {
                'title': '📧 Email Verification',
                'message': 'Please verify your email address to complete registration.',
                'color': '#007bff'
            },
            'cashout': {
                'title': '🔐 Secure Your Cashout',
                'message': 'Please verify this cashout request with the code below.',
                'color': '#28a745'
            },
            'change_email': {
                'title': '🔄 Email Change Verification',
                'message': 'Please verify your new email address.',
                'color': '#6f42c1'
            }
        }
        
        config = purpose_configs.get(purpose, purpose_configs['registration'])
        
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{config['title']}</title>
        </head>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
            <div style="background: white; border-radius: 10px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                <!-- Header -->
                <div style="text-align: center; margin-bottom: 30px;">
                    <h1 style="color: {config['color']}; margin: 0; font-size: 24px;">{config['title']}</h1>
                    <p style="color: #6c757d; margin: 10px 0 0 0;">{Config.PLATFORM_NAME} Secure Platform</p>
                </div>
                
                <!-- Content -->
                <div style="text-align: center;">
                    <p style="color: #333; font-size: 16px; margin-bottom: 20px;">
                        Hello {user_name}! 👋
                    </p>
                    <p style="color: #333; font-size: 16px; margin-bottom: 30px;">
                        {config['message']}
                    </p>
                    
                    <!-- OTP Code -->
                    <div style="background: linear-gradient(135deg, {config['color']}, {config['color']}dd); padding: 25px; border-radius: 10px; margin: 25px 0;">
                        <p style="color: white; margin: 0 0 10px 0; font-size: 14px;">Your verification code:</p>
                        <div style="font-size: 32px; font-weight: bold; color: white; letter-spacing: 5px; font-family: monospace;">
                            {otp_code}
                        </div>
                    </div>
                    
                    <p style="color: #dc3545; font-size: 14px; margin: 20px 0;">
                        ⏰ This code expires in 15 minutes
                    </p>
                </div>
                
                <!-- Footer -->
                <div style="text-align: center; margin-top: 30px; padding-top: 20px; border-top: 1px solid #e9ecef;">
                    <p style="color: #6c757d; font-size: 12px; margin: 0;">
                        This is an automated message from {Config.PLATFORM_NAME}<br>
                        If you didn't request this, please ignore this email.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
    
    def _create_welcome_email_content(self, user_name: str) -> str:
        """Create welcome email content"""
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Welcome to {Config.PLATFORM_NAME}!</title>
        </head>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
            <div style="background: white; border-radius: 10px; padding: 30px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                <!-- Header -->
                <div style="background: linear-gradient(135deg, #667eea, #764ba2); color: white; padding: 30px; text-align: center; border-radius: 10px; margin: -30px -30px 30px -30px;">
                    <h1 style="margin: 0; font-size: 28px;">🎉 Welcome to {Config.PLATFORM_NAME}!</h1>
                    <p style="margin: 10px 0 0 0; font-size: 16px;">Secure crypto trading made simple</p>
                </div>
                
                <!-- Content -->
                <div>
                    <h2 style="color: #333; margin-bottom: 20px;">Hello {user_name}! 👋</h2>
                    
                    <p style="color: #333; font-size: 16px; line-height: 1.6; margin-bottom: 25px;">
                        Welcome to {Config.PLATFORM_NAME}! You're now part of a secure platform for cryptocurrency transactions 
                        with built-in buyer and seller protection.
                    </p>
                    
                    <h3 style="color: #667eea; margin-bottom: 15px;">🚀 What you can do:</h3>
                    <ul style="color: #333; font-size: 16px; line-height: 1.8; margin-bottom: 25px;">
                        <li>💰 Multi-crypto wallet management</li>
                        <li>🔒 Secure escrow transactions</li>
                        <li>⚡ Auto-cashout functionality</li>
                        <li>🇳🇬 NGN transfers and withdrawals</li>
                        <li>📊 Real-time trading dashboard</li>
                    </ul>
                    
                    <h3 style="color: #28a745; margin-bottom: 15px;">🛡️ Security Features:</h3>
                    <ul style="color: #333; font-size: 16px; line-height: 1.8; margin-bottom: 30px;">
                        <li>🔐 Advanced encryption and secure storage</li>
                        <li>⚡ Fast dispute resolution</li>
                        <li>📱 Two-factor authentication</li>
                        <li>🌐 Multi-currency support</li>
                    </ul>
                    
                    <div style="text-align: center; margin: 30px 0;">
                        <p style="color: #333; font-size: 16px; margin-bottom: 20px;">
                            Ready to start trading securely?
                        </p>
                        <a href="https://t.me/LockBayBot" style="display: inline-block; background: linear-gradient(135deg, #667eea, #764ba2); color: white; padding: 15px 30px; text-decoration: none; border-radius: 8px; font-weight: bold; font-size: 16px;">
                            Open {Config.PLATFORM_NAME}
                        </a>
                    </div>
                </div>
                
                <!-- Footer -->
                <div style="text-align: center; margin-top: 40px; padding-top: 20px; border-top: 1px solid #e9ecef;">
                    <p style="color: #6c757d; font-size: 12px; margin: 0;">
                        Questions? Contact our support team anytime through the bot.<br>
                        This email was sent because you registered for {Config.PLATFORM_NAME}.
                    </p>
                </div>
            </div>
        </body>
        </html>
        """
    
    def _create_dispute_notification_content(
        self,
        dispute_id: int,
        escrow_id: str,
        escrow_amount: float,
        sender_info: str,
        sender_role: str,
        current_message: str,
        dispute_status: str,
        action_urls: Dict[str, str],
        admin_panel_url: str
    ) -> str:
        """
        Create optimized dispute notification email content
        ENHANCEMENT: Now includes trade chat messages for full context
        SECURITY: All user content is HTML-escaped
        """
        import html as html_lib
        from config import Config
        from database import SessionLocal
        from models import Dispute, EscrowMessage, User
        
        # SECURITY: Escape all user-generated content
        escaped_sender_info = html_lib.escape(sender_info)
        escaped_message = html_lib.escape(current_message)
        escaped_escrow_id = html_lib.escape(escrow_id)
        
        # Fetch trade chat messages for context
        trade_chat_html = ""
        try:
            session = SessionLocal()
            try:
                # Get the dispute and escrow
                dispute = session.query(Dispute).filter(Dispute.id == dispute_id).first()
                if dispute and dispute.escrow:
                    escrow = dispute.escrow
                    
                    # Get recent trade chat messages (last 5 for performance)
                    trade_messages = session.query(EscrowMessage).filter(
                        EscrowMessage.escrow_id == escrow.id
                    ).order_by(EscrowMessage.created_at.desc()).limit(5).all()
                    
                    if trade_messages:
                        trade_chat_html = f"""
                        <div style="margin-top: 20px; padding: 15px; background-color: #e8f5e9; border-radius: 5px; border-left: 4px solid #4caf50;">
                            <h4 style="margin-top: 0; color: #2e7d32;">💬 Recent Trade Chat ({len(trade_messages)} messages)</h4>
                            <p style="color: #666; font-size: 12px; margin: 5px 0 10px 0;">Context from trade chat BEFORE dispute</p>
                        """
                        # Show messages in chronological order
                        for msg in reversed(trade_messages):
                            sender = session.query(User).filter(User.id == msg.sender_id).first()
                            # Determine role
                            if msg.sender_id == escrow.buyer_id:
                                role = "Buyer"
                                bg_color = "#e3f2fd"
                            elif msg.sender_id == escrow.seller_id:
                                role = "Seller"
                                bg_color = "#fff3e0"
                            else:
                                role = "Admin"
                                bg_color = "#f3e5f5"
                            
                            sender_name = (sender.first_name or sender.username or f"User #{sender.id}") if sender else f"User #{msg.sender_id}"
                            msg_text = str(msg.content) if msg.content else ""
                            
                            # SECURITY: Escape HTML
                            escaped_sender_name = html_lib.escape(sender_name)
                            escaped_msg_text = html_lib.escape(msg_text)
                            
                            trade_chat_html += f"""
                            <div style="margin-bottom: 8px; padding: 8px; background-color: {bg_color}; border-radius: 4px;">
                                <strong>{escaped_sender_name} ({role}):</strong> {escaped_msg_text[:100]}{'...' if len(escaped_msg_text) > 100 else ''}
                                <br><small style="color: #6c757d;">{msg.created_at.strftime('%Y-%m-%d %H:%M UTC')}</small>
                            </div>
                            """
                        trade_chat_html += "</div>"
            finally:
                session.close()
        except Exception as e:
            logger.error(f"Failed to fetch trade chat messages for dispute email: {e}")
            trade_chat_html = ""  # Fail gracefully
        
        # Build email content
        email_content = f"""
        <div style="background-color: #fff3cd; padding: 15px; border-radius: 5px; border-left: 4px solid #ffc107;">
            <h3 style="color: #856404; margin-top: 0;">⚖️ New Dispute Message</h3>
            <p><strong>Dispute ID:</strong> #{dispute_id}</p>
            <p><strong>Trade ID:</strong> #{escaped_escrow_id}</p>
            <p><strong>Amount:</strong> ${escrow_amount:.2f} USD</p>
            <p><strong>From:</strong> {escaped_sender_info} ({sender_role})</p>
            <p><strong>Status:</strong> {dispute_status}</p>
        </div>
        
        {trade_chat_html}
        
        <div style="margin-top: 20px; padding: 15px; background-color: #f8f9fa; border-radius: 5px;">
            <h4 style="margin-top: 0;">💬 Latest Dispute Message:</h4>
            <div style="margin-bottom: 15px; padding: 10px; background-color: #e3f2fd; border-radius: 5px;">
                <div style="font-weight: bold; color: #333; margin-bottom: 5px;">
                    {escaped_sender_info} ({sender_role})
                </div>
                <div style="color: #555; white-space: pre-wrap; font-family: monospace;">
                    {escaped_message}
                </div>
            </div>
            <div style="text-align: center; margin-top: 15px;">
                <a href="{admin_panel_url}" style="display: inline-block; padding: 10px 20px; background-color: #007bff; color: white; text-decoration: none; border-radius: 5px; font-weight: bold;">
                    📋 View Full Dispute History
                </a>
            </div>
        </div>
        
        <div style="margin-top: 20px; padding: 20px; background-color: #e7f3ff; border-radius: 5px;">
            <p style="margin-top: 0;"><strong>⚡ Action Required:</strong></p>
            <p>Click a button below to resolve this dispute:</p>
            <div style="margin-top: 15px; text-align: center;">
                <a href="{action_urls.get('release_url', '#')}" style="display: inline-block; margin: 5px; padding: 12px 24px; background-color: #28a745; color: white; text-decoration: none; border-radius: 5px; font-weight: bold;">🟢 Release to Seller</a>
                <a href="{action_urls.get('refund_url', '#')}" style="display: inline-block; margin: 5px; padding: 12px 24px; background-color: #007bff; color: white; text-decoration: none; border-radius: 5px; font-weight: bold;">🔵 Refund to Buyer</a>
                <a href="{action_urls.get('split_url', '#')}" style="display: inline-block; margin: 5px; padding: 12px 24px; background-color: #ffc107; color: #333; text-decoration: none; border-radius: 5px; font-weight: bold;">🟡 Split Funds (50/50)</a>
                <a href="{action_urls.get('custom_split_url', '#')}" style="display: inline-block; margin: 5px; padding: 12px 24px; background-color: #6f42c1; color: white; text-decoration: none; border-radius: 5px; font-weight: bold;">⚖️ Custom Split Resolution</a>
            </div>
        </div>
        """
        
        # Create full HTML email template
        platform_name = Config.PLATFORM_NAME
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Dispute Message Alert</title>
        </head>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8f9fa;">
            <div style="background: white; border-radius: 10px; padding: 0; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                <div style="background: #ffc107; color: #333; padding: 20px; text-align: center; border-radius: 10px 10px 0 0;">
                    <h1 style="margin: 0;">⚖️ Dispute Message Alert</h1>
                    <p style="margin: 10px 0 0 0; opacity: 0.9;">{platform_name} Admin Notification</p>
                </div>
                <div style="padding: 25px;">
                    {email_content}
                </div>
                <div style="text-align: center; padding: 20px; color: #6c757d; font-size: 12px; border-top: 1px solid #e9ecef;">
                    <p style="margin: 0;">This is an automated admin notification from {platform_name}</p>
                    <p style="margin: 5px 0 0 0;">{datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        return html_content
    
    async def get_metrics(self) -> Dict[str, Any]:
        """Get email queue performance metrics"""
        try:
            queue_stats = await self.replit_queue.get_queue_stats()
            
            return {
                "queue_metrics": self.metrics,
                "queue_stats": queue_stats,
                "is_initialized": self._is_initialized,
                "workers_active": len(self.replit_queue.workers),
                "email_service_enabled": self.email_service.enabled
            }
        except Exception as e:
            logger.error(f"Failed to get email queue metrics: {e}")
            return {"error": str(e)}
    
    async def health_check(self) -> Dict[str, Any]:
        """Check background email queue health"""
        try:
            if not self._is_initialized:
                return {"status": "unhealthy", "error": "Not initialized"}
                
            queue_health = await self.replit_queue.health_check()
            
            return {
                "status": "healthy" if queue_health.get("status") == "healthy" else "degraded",
                "email_service_enabled": self.email_service.enabled,
                "queue_health": queue_health,
                "metrics": self.metrics
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}
    
    async def shutdown(self):
        """Gracefully shutdown the email queue"""
        try:
            logger.info("🔄 Shutting down Background Email Queue...")
            await self.replit_queue.stop_workers()
            await self.replit_queue.disconnect()
            self._is_initialized = False
            logger.info("✅ Background Email Queue shutdown completed")
        except Exception as e:
            logger.error(f"Error during email queue shutdown: {e}")


# Global singleton instance
background_email_queue = BackgroundEmailQueue()


# Convenience functions
async def queue_otp_email(recipient: str, otp_code: str, purpose: str = "registration", user_id: Optional[int] = None, user_name: str = "User") -> Dict[str, Any]:
    """Convenience function to queue OTP email"""
    return await background_email_queue.queue_otp_email(recipient, otp_code, purpose, user_id, user_name)

async def queue_welcome_email(recipient: str, user_name: str, user_id: Optional[int] = None) -> Dict[str, Any]:
    """Convenience function to queue welcome email"""
    return await background_email_queue.queue_welcome_email(recipient, user_name, user_id)

async def initialize_email_queue() -> bool:
    """Initialize the global email queue"""
    return await background_email_queue.initialize()
