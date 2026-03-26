"""
FastAPI Webhook Server for Telegram Bot
Handles incoming webhook requests and health checks with comprehensive audit logging
"""
from fastapi import FastAPI, Request, HTTPException, Query, Form
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import logging
import json
import orjson  # PERFORMANCE: 3-5x faster JSON parsing than stdlib
import time
import asyncio
import os
from datetime import datetime
from decimal import Decimal
from telegram import Update
from typing import Optional

from utils.webhook_audit_logger import (
    audit_payment_webhook, 
    audit_twilio_webhook, 
    log_webhook_request
)

# Import completion time monitoring
from utils.completion_time_integration import (
    track_webhook_processing,
    completion_time_monitor
)

# WEBHOOK PERFORMANCE MONITORING: Track processing times for optimization
webhook_performance_stats = {
    'total_requests': 0,
    'slow_requests': 0,
    'avg_response_time': 0.0,
    'last_reset': time.time()
}

def track_webhook_performance(processing_time_ms: float):
    """Track webhook performance metrics"""
    global webhook_performance_stats
    
    webhook_performance_stats['total_requests'] += 1
    
    # Track slow requests (>500ms)
    if processing_time_ms > 500:
        webhook_performance_stats['slow_requests'] += 1
        logger.warning(f"⚠️ SLOW_WEBHOOK: Processing took {processing_time_ms:.1f}ms (>500ms threshold)")
    
    # Update rolling average
    current_avg = webhook_performance_stats['avg_response_time']
    total = webhook_performance_stats['total_requests']
    webhook_performance_stats['avg_response_time'] = ((current_avg * (total - 1)) + processing_time_ms) / total
    
    # Log performance stats every 100 requests
    if total % 100 == 0:
        slow_rate = (webhook_performance_stats['slow_requests'] / total) * 100
        logger.info(f"📊 WEBHOOK_PERFORMANCE: Avg: {current_avg:.1f}ms, Slow rate: {slow_rate:.1f}% ({webhook_performance_stats['slow_requests']}/{total})")

# SIMPLIFIED ARCHITECTURE: Removed over-engineered webhook optimization imports
# All webhook optimization layers eliminated for direct processing architecture
# (Provider Confirmation → Wallet Credit → Immediate Notification)

# OPTIMIZATION: Single webhook queue backend (SQLite by default, Redis if configured)
# Eliminates dual-queue overhead. Set WEBHOOK_QUEUE_BACKEND=redis to use Redis instead.
_webhook_queue_backend = os.environ.get("WEBHOOK_QUEUE_BACKEND", "sqlite").lower()

if _webhook_queue_backend == "redis":
    from webhook_queue.webhook_inbox.redis_webhook_queue import (
        redis_webhook_queue,
        WebhookEventPriority as RedisWebhookEventPriority
    )
    # Re-export with unified name
    WebhookEventPriority = RedisWebhookEventPriority
else:
    from webhook_queue.webhook_inbox.fast_sqlite_webhook_queue import (
        fast_sqlite_webhook_queue,
        WebhookEventPriority
    )


# SIMPLIFIED ARCHITECTURE: webhook_memory_optimizer removed for direct processing

# Import BlockBee webhook router (secure version)
from handlers.blockbee_webhook_new import router as blockbee_router

logger = logging.getLogger(__name__)


async def enqueue_webhook_with_fallback(
    provider: str,
    endpoint: str,
    payload: dict,
    headers: dict,
    client_ip: str,
    priority: WebhookEventPriority,
    max_retries: int = 3,
    signature: Optional[str] = None,
    metadata: Optional[dict] = None
) -> tuple[bool, str, float]:
    """
    OPTIMIZED: Single-backend webhook enqueue (no dual-queue overhead).
    Backend selected via WEBHOOK_QUEUE_BACKEND env var (sqlite or redis).
    
    Returns:
        (success, event_id, duration_ms)
    """
    try:
        if _webhook_queue_backend == "redis":
            success, event_id, duration_ms = await redis_webhook_queue.enqueue_webhook(
                provider=provider,
                endpoint=endpoint,
                payload=payload,
                headers=headers,
                client_ip=client_ip,
                priority=priority,
                metadata=metadata,
                signature=signature
            )
        else:
            success, event_id, duration_ms = await fast_sqlite_webhook_queue.enqueue_webhook(
                provider=provider,
                endpoint=endpoint,
                payload=payload,
                headers=headers,
                client_ip=client_ip,
                priority=priority,
                max_retries=max_retries,
                signature=signature or "",
                metadata=metadata or {}
            )
        
        if success:
            logger.debug(f"✅ WEBHOOK_ENQUEUE ({_webhook_queue_backend}): {provider}/{endpoint} ({duration_ms:.2f}ms)")
        
        return success, event_id, duration_ms
        
    except Exception as e:
        logger.error(f"❌ WEBHOOK_ENQUEUE_FAILED ({_webhook_queue_backend}): {provider}/{endpoint} - {e}")
        return False, "", 0.0

# Lifespan handler for FastAPI (modern replacement for on_event)
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Modern lifespan handler for FastAPI application.
    Replaces deprecated @app.on_event("startup") and @app.on_event("shutdown").
    
    Startup: Runs before app starts receiving requests
    Shutdown: Runs after app stops receiving requests
    """
    # Startup: Initialize worker-specific systems
    logger.info(f"🔧 Gunicorn worker {os.getpid()} starting...")
    
    # Note: _bot_application is set by production_start.py before gunicorn starts
    # Each worker will inherit the initialized bot reference
    
    # Initialize background systems for this worker
    try:
        await initialize_webhook_systems_in_background()
        logger.info(f"✅ Worker {os.getpid()} initialized successfully")
    except Exception as e:
        logger.error(f"❌ Worker {os.getpid()} initialization failed: {e}")
        import traceback
        traceback.print_exc()
    
    yield  # App is now running and handling requests
    
    # Shutdown: Cleanup (currently no cleanup needed)
    logger.info(f"🔄 Gunicorn worker {os.getpid()} shutting down...")

# Create FastAPI app with modern lifespan handler
app = FastAPI(
    title="Telegram Bot Webhook Server",
    description="High-performance webhook server with cold start elimination",
    lifespan=lifespan
)

# ASGI middleware to strip /api prefix (Emergent platform forwards full path to port 8001)
@app.middleware("http")
async def strip_api_prefix(request: Request, call_next):
    if request.scope["path"].startswith("/api/"):
        request.scope["path"] = request.scope["path"][4:]
    elif request.scope["path"] == "/api":
        request.scope["path"] = "/"
    return await call_next(request)

# Initialize Jinja2 templates for public profile pages
templates = Jinja2Templates(directory="templates")

# Mount static files for logo and assets
app.mount("/static", StaticFiles(directory="static"), name="static")

# Global application reference with startup state tracking
_bot_application = None
_startup_complete = False
_startup_timestamp = None

# REMOVED: Performance optimization systems per simplification requirements

# ENHANCED: Setup real-time admin dashboard
logger.info("🔧 Setting up real-time admin dashboard...")
try:
    from utils.realtime_admin_dashboard import setup_admin_dashboard
    setup_admin_dashboard(app)
    logger.info("✅ Real-time admin dashboard integrated with FastAPI")
except Exception as e:
    logger.error(f"Failed to setup admin dashboard: {e}")

# SECURITY FIX: Disable public performance monitoring routes
logger.info("🔒 Performance monitoring routes disabled for security")
# setup_performance_routes(app)  # DISABLED - potential security risk

# Setup webhook resilience health check
logger.info("🏥 Setting up webhook resilience health check...")
try:
    @app.get("/health")
    async def health_check():
        """Health check endpoint for Reserve VM deployment probe"""
        return {"status": "ok", "service": "LockBay Telegram Bot", "version": "1.0"}
    
    # UNIVERSAL LANDING PAGE - Root domain and /start alias
    @app.get("/")
    @app.get("/start")
    async def landing_page(request: Request, ref: Optional[str] = None):
        """
        Universal landing page for Lockbay at root domain
        
        Shows conversion-optimized landing page for customer acquisition
        with dual buyer/seller personas and special offers
        
        Query Parameters:
            ref: Optional referral code from sharing links
        
        Examples:
            - / (main landing page)
            - /?ref=onarrival1 (landing page with $5 welcome bonus)
            - /start?ref=ABC123 (alias for backwards compatibility)
        """
        try:
            from services.landing_page_service import LandingPageService
            
            # Get landing page data (includes referrer info if ref code provided)
            page_data = LandingPageService.get_landing_page_data(referral_code=ref)
            
            # Render template with data
            return templates.TemplateResponse(
                "landing_page.html",
                {"request": request, **page_data}
            )
            
        except Exception as e:
            logger.error(f"❌ Error rendering landing page: {e}", exc_info=True)
            # Fallback to simple error page
            from config import Config
            return HTMLResponse(content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Lockbay - Secure Trading Platform</title>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
            </head>
            <body style="font-family: sans-serif; text-align: center; padding: 2rem;">
                <h1>Welcome to Lockbay</h1>
                <p>Secure peer-to-peer trading with escrow protection</p>
                <a href="https://t.me/{Config.BOT_USERNAME.replace('@', '')}" 
                   style="display: inline-block; background: #3BB5C8; color: white; 
                          padding: 1rem 2rem; text-decoration: none; border-radius: 6px; margin-top: 1rem;">
                    Open Telegram Bot
                </a>
            </body>
            </html>
            """, status_code=500)
    
    # FEATURES SHOWCASE PAGE
    @app.get("/features")
    async def features_page(request: Request):
        """
        Features showcase page displaying bot interface examples
        
        Shows detailed bot features, command reference, and screenshot gallery
        """
        try:
            return templates.TemplateResponse(
                "features.html",
                {"request": request}
            )
        except Exception as e:
            logger.error(f"❌ Error rendering features page: {e}", exc_info=True)
            return HTMLResponse(content="""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Features - Lockbay</title>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
            </head>
            <body style="font-family: sans-serif; text-align: center; padding: 2rem;">
                <h1>Lockbay Features</h1>
                <p>Error loading features page</p>
                <a href="/" style="color: #3BB5C8;">Go back to home</a>
            </body>
            </html>
            """, status_code=500)
    
    # PUBLIC PROFILE ENDPOINT - Social Proof Pages
    @app.get("/u/{profile_slug}")
    async def public_profile(profile_slug: str, request: Request):
        """
        Public social proof profile page for users
        Allows users to share their trading reputation via URL
        
        Examples:
          - /u/john_trader (user with username)
          - /u/sarah_k8x9df (user without username, auto-generated slug)
        """
        try:
            from services.public_profile_service import PublicProfileService
            
            # Get profile data
            profile_data = PublicProfileService.get_profile_data(profile_slug)
            
            if not profile_data:
                # User not found - return 404 HTML page
                return HTMLResponse(content="""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Profile Not Found - LockBay</title>
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <style>
                        body {
                            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                            background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
                            display: flex;
                            align-items: center;
                            justify-content: center;
                            min-height: 100vh;
                            margin: 0;
                            padding: 1rem;
                        }
                        .error-container {
                            background: white;
                            border-radius: 12px;
                            padding: 3rem 2rem;
                            text-align: center;
                            box-shadow: 0 4px 16px rgba(0,0,0,0.1);
                            max-width: 500px;
                        }
                        h1 {
                            color: #1E3A8A;
                            font-size: 3rem;
                            margin-bottom: 1rem;
                        }
                        p {
                            color: #6B7280;
                            font-size: 1.1rem;
                            margin-bottom: 2rem;
                        }
                        a {
                            background: #1E3A8A;
                            color: white;
                            padding: 0.75rem 2rem;
                            border-radius: 8px;
                            text-decoration: none;
                            font-weight: 600;
                            display: inline-block;
                        }
                        a:hover {
                            background: #3B82F6;
                        }
                    </style>
                </head>
                <body>
                    <div class="error-container">
                        <h1>🔍</h1>
                        <h2 style="color: #1E3A8A; margin-bottom: 1rem;">Profile Not Found</h2>
                        <p>We couldn't find a trader with profile: <strong>""" + profile_slug + """</strong></p>
                        <a href="https://t.me/lockbay_bot">Open LockBay Bot</a>
                    </div>
                </body>
                </html>
                """, status_code=404)
            
            # Render template with profile data
            return templates.TemplateResponse("public_profile.html", {
                "request": request,
                **profile_data
            })
            
        except Exception as e:
            logger.error(f"❌ Error rendering public profile for {profile_slug}: {e}", exc_info=True)
            # Return error HTML page
            return HTMLResponse(content="""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Error - LockBay</title>
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <style>
                    body {
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                        background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        min-height: 100vh;
                        margin: 0;
                        padding: 1rem;
                    }
                    .error-container {
                        background: white;
                        border-radius: 12px;
                        padding: 3rem 2rem;
                        text-align: center;
                        box-shadow: 0 4px 16px rgba(0,0,0,0.1);
                        max-width: 500px;
                    }
                    h1 {
                        color: #DC2626;
                        font-size: 3rem;
                        margin-bottom: 1rem;
                    }
                    p {
                        color: #6B7280;
                        font-size: 1.1rem;
                        margin-bottom: 2rem;
                    }
                    a {
                        background: #1E3A8A;
                        color: white;
                        padding: 0.75rem 2rem;
                        border-radius: 8px;
                        text-decoration: none;
                        font-weight: 600;
                        display: inline-block;
                    }
                    a:hover {
                        background: #3B82F6;
                    }
                </style>
            </head>
            <body>
                <div class="error-container">
                    <h1>⚠️</h1>
                    <h2 style="color: #DC2626; margin-bottom: 1rem;">Something Went Wrong</h2>
                    <p>We encountered an error loading this profile. Please try again later.</p>
                    <a href="https://t.me/lockbay_bot">Open LockBay Bot</a>
                </div>
            </body>
            </html>
            """, status_code=500)
    
    # Privacy Policy Route
    @app.get("/privacy")
    async def privacy_policy(request: Request):
        """
        Privacy Policy page for Lockbay
        
        Explains how we collect, use, store, and protect user data
        in our crypto escrow marketplace platform
        """
        try:
            import os
            # Get website contact email from environment variables
            website_email = os.getenv('WEBSITE_EMAIL', 'hello@lockbay.io')
            
            return templates.TemplateResponse(
                "privacy_policy.html",
                {"request": request, "admin_email": website_email}
            )
        except Exception as e:
            logger.error(f"❌ Error rendering privacy policy: {e}", exc_info=True)
            return HTMLResponse(content="Error loading privacy policy", status_code=500)
    
    # Terms of Service Route
    @app.get("/terms")
    async def terms_of_service(request: Request):
        """
        Terms of Service page for Lockbay
        
        Legal terms and conditions for using our P2P marketplace
        with cryptocurrency escrow services
        """
        try:
            import os
            # Get website contact email from environment variables
            website_email = os.getenv('WEBSITE_EMAIL', 'hello@lockbay.io')
            
            return templates.TemplateResponse(
                "terms_of_service.html",
                {"request": request, "admin_email": website_email}
            )
        except Exception as e:
            logger.error(f"❌ Error rendering terms of service: {e}", exc_info=True)
            return HTMLResponse(content="Error loading terms of service", status_code=500)
    
    # Partner Program Application Routes
    @app.get("/partners/apply")
    async def partner_application_form(request: Request):
        """Display partner program application form"""
        try:
            return templates.TemplateResponse(
                "partner_program/apply.html",
                {"request": request, "error": None}
            )
        except Exception as e:
            logger.error(f"❌ Error rendering partner application form: {e}", exc_info=True)
            return HTMLResponse(content="Error loading application form", status_code=500)
    
    @app.post("/partners/apply")
    async def submit_partner_application(request: Request):
        """Handle partner program application submission"""
        try:
            from services.partner_application_service import PartnerApplicationService
            from database import get_async_session
            
            # Get form data
            form_data = await request.form()
            
            # Validate required fields
            required_fields = [
                'name', 'telegram_handle', 'email', 'community_type',
                'audience_size', 'primary_region', 'monthly_volume',
                'commission_tier', 'goals', 'agree_terms'
            ]
            
            missing_fields = [field for field in required_fields if not form_data.get(field)]
            
            if missing_fields:
                return templates.TemplateResponse(
                    "partner_program/apply.html",
                    {
                        "request": request,
                        "error": f"Please fill in all required fields: {', '.join(missing_fields)}"
                    }
                )
            
            # Submit application
            async with get_async_session() as session:
                service = PartnerApplicationService()
                result = await service.submit_application(
                    session=session,
                    name=str(form_data.get('name')),
                    telegram_handle=str(form_data.get('telegram_handle')),
                    email=str(form_data.get('email')),
                    community_type=str(form_data.get('community_type')),
                    audience_size=str(form_data.get('audience_size')),
                    primary_region=str(form_data.get('primary_region')),
                    monthly_volume=str(form_data.get('monthly_volume')),
                    commission_tier=str(form_data.get('commission_tier')),
                    goals=str(form_data.get('goals'))
                )
            
            if result.get('success'):
                # Show success page
                return templates.TemplateResponse(
                    "partner_program/success.html",
                    {
                        "request": request,
                        "name": result['name'],
                        "email": result['email'],
                        "telegram_handle": result['telegram_handle'],
                        "commission_tier": result['commission_tier'],
                        "submitted_at": result['submitted_at']
                    }
                )
            else:
                # Show error on form
                return templates.TemplateResponse(
                    "partner_program/apply.html",
                    {
                        "request": request,
                        "error": f"Failed to submit application: {result.get('error', 'Unknown error')}"
                    }
                )
                
        except Exception as e:
            logger.error(f"❌ Error processing partner application: {e}", exc_info=True)
            return templates.TemplateResponse(
                "partner_program/apply.html",
                {
                    "request": request,
                    "error": "An unexpected error occurred. Please try again later."
                }
            )
    
    @app.get("/partners/status")
    async def partner_application_status_form(request: Request):
        """Display application status lookup form"""
        try:
            return templates.TemplateResponse(
                "partner_program/status.html",
                {
                    "request": request,
                    "error": None,
                    "application": None
                }
            )
        except Exception as e:
            logger.error(f"❌ Error rendering status form: {e}", exc_info=True)
            return HTMLResponse(content="Error loading status page", status_code=500)
    
    @app.post("/partners/status")
    async def check_partner_application_status(request: Request):
        """Check partner application status by ID or email"""
        try:
            from database import get_async_session
            from models import PartnerApplication
            from sqlalchemy import select
            import time
            from collections import defaultdict
            
            # Simple IP-based rate limiting (5 requests per minute)
            if not hasattr(check_partner_application_status, '_rate_limits'):
                check_partner_application_status._rate_limits = defaultdict(list)  # type: ignore
            
            client_ip = request.client.host if request.client else "unknown"
            now = time.time()
            cutoff = now - 60  # 1 minute window
            
            # Clean old requests
            check_partner_application_status._rate_limits[client_ip] = [  # type: ignore
                req_time for req_time in check_partner_application_status._rate_limits[client_ip]  # type: ignore
                if req_time > cutoff
            ]
            
            # Check rate limit (5 requests per minute)
            if len(check_partner_application_status._rate_limits[client_ip]) >= 5:  # type: ignore
                return templates.TemplateResponse(
                    "partner_program/status.html",
                    {
                        "request": request,
                        "error": "Too many requests. Please wait a minute before trying again.",
                        "application": None
                    },
                    status_code=429
                )
            
            # Add current request
            check_partner_application_status._rate_limits[client_ip].append(now)  # type: ignore
            
            # Get search query
            form_data = await request.form()
            search_query = str(form_data.get('search_query', '')).strip()
            
            if not search_query:
                return templates.TemplateResponse(
                    "partner_program/status.html",
                    {
                        "request": request,
                        "error": "Please enter an Application ID or email address",
                        "application": None
                    }
                )
            
            # Search for application
            async with get_async_session() as session:
                # Try to parse as integer ID first
                try:
                    app_id = int(search_query.replace('#', ''))
                    stmt = select(PartnerApplication).where(PartnerApplication.id == app_id)
                except ValueError:
                    # Search by email (case-insensitive)
                    from sqlalchemy import func
                    stmt = select(PartnerApplication).where(
                        func.lower(PartnerApplication.email) == func.lower(search_query)
                    )
                
                result = await session.execute(stmt)
                application = result.scalar_one_or_none()
            
            if not application:
                return templates.TemplateResponse(
                    "partner_program/status.html",
                    {
                        "request": request,
                        "error": "Application not found. Please check your Application ID or email address.",
                        "application": None
                    }
                )
            
            # Format submitted date
            submitted_at = application.created_at.strftime("%B %d, %Y at %I:%M %p UTC")
            
            return templates.TemplateResponse(
                "partner_program/status.html",
                {
                    "request": request,
                    "error": None,
                    "application": application,
                    "submitted_at": submitted_at
                }
            )
                
        except Exception as e:
            logger.error(f"❌ Error checking application status: {e}", exc_info=True)
            return templates.TemplateResponse(
                "partner_program/status.html",
                {
                    "request": request,
                    "error": "An error occurred while checking your application status. Please try again.",
                    "application": None
                }
            )
    
    @app.get("/health/resilience")
    async def webhook_health():
        """Webhook resilience health check endpoint"""
        return {"status": "ok", "message": "Simplified architecture - direct processing"}
    
    @app.get("/health/webhook")
    async def webhook_system_health():
        """Comprehensive webhook system health check"""
        try:
            stats = {"status": "simplified", "mode": "direct_processing"}
            
            # Check if bot application is ready
            global _bot_application, _startup_complete
            bot_ready = _bot_application is not None and _startup_complete
            
            # Calculate overall health score
            # Use real-time webhook performance stats
            global webhook_performance_stats
            webhook_stats = webhook_performance_stats.copy()
            stats['webhook_stats'] = webhook_stats  # type: ignore[assignment]
            
            total_requests = webhook_stats['total_requests']
            slow_requests = webhook_stats['slow_requests']
            avg_response_time = webhook_stats['avg_response_time']
            
            if total_requests > 0:
                # Calculate performance score (100 = perfect, lower = worse performance)
                slow_rate = (slow_requests / total_requests) * 100
                
                # Performance score based on slow request rate and average response time
                if slow_rate == 0 and avg_response_time <= 200:
                    health_score = 100  # Excellent performance
                elif slow_rate <= 5 and avg_response_time <= 300:
                    health_score = 90   # Good performance
                elif slow_rate <= 10 and avg_response_time <= 400:
                    health_score = 80   # Acceptable performance
                elif slow_rate <= 20 and avg_response_time <= 600:
                    health_score = 60   # Poor performance
                else:
                    health_score = 30   # Bad performance
            else:
                health_score = 100 if bot_ready else 0
            
            # Determine health status
            if health_score >= 95 and bot_ready:
                status = "healthy"
            elif health_score >= 80 and bot_ready:
                status = "degraded"
            elif bot_ready:
                status = "unhealthy"
            else:
                status = "starting"
            
            return JSONResponse(content={
                "status": status,
                "health_score": health_score,
                "bot_ready": bot_ready,
                "webhook_stats": webhook_stats,
                "performance_metrics": {
                    "avg_response_time_ms": avg_response_time,
                    "slow_request_rate": (slow_requests / total_requests * 100) if total_requests > 0 else 0,
                    "total_requests": total_requests,
                    "slow_requests": slow_requests
                },
                "timestamp": time.time()
            })
            
        except Exception as e:
            logger.error(f"Health check error: {e}")
            return JSONResponse(
                content={"status": "error", "error": str(e)},
                status_code=500
            )
    
    @app.get("/health/webhook-performance")
    async def webhook_performance_metrics():
        """Detailed webhook performance metrics endpoint"""
        global webhook_performance_stats
        
        performance_data = webhook_performance_stats.copy()
        current_time = time.time()
        uptime_seconds = current_time - performance_data.get('last_reset', current_time)
        
        # Calculate performance grade
        total = performance_data['total_requests']
        slow = performance_data['slow_requests']
        avg_ms = performance_data['avg_response_time']
        
        if total == 0:
            grade = "N/A"
            slow_rate = 0
        else:
            slow_rate = (slow / total) * 100
            if slow_rate == 0 and avg_ms <= 200:
                grade = "A+ (Excellent)"
            elif slow_rate <= 2 and avg_ms <= 300:
                grade = "A (Very Good)"
            elif slow_rate <= 5 and avg_ms <= 400:
                grade = "B (Good)"
            elif slow_rate <= 10 and avg_ms <= 500:
                grade = "C (Acceptable)"
            elif slow_rate <= 20 and avg_ms <= 750:
                grade = "D (Poor)"
            else:
                grade = "F (Critical)"
        
        return {
            "performance_grade": grade,
            "metrics": {
                "total_webhooks_processed": total,
                "slow_webhooks_count": slow,
                "slow_webhook_rate_percent": round(slow_rate, 2),
                "average_response_time_ms": round(avg_ms, 2),
                "uptime_seconds": round(uptime_seconds, 2),
                "requests_per_hour": round((total / uptime_seconds * 3600), 2) if uptime_seconds > 0 else 0
            },
            "performance_targets": {
                "target_response_time_ms": 500,
                "target_slow_rate_percent": 5.0,
                "current_status": "PASS" if slow_rate <= 5 and avg_ms <= 500 else "FAIL"
            },
            "timestamp": current_time
        }
    
    logger.info("✅ Webhook resilience health checks integrated")
except Exception as e:
    logger.error(f"Failed to setup resilience health checks: {e}")

# CRITICAL FIX: Register BlockBee webhook router
logger.info("🔗 Registering BlockBee webhook router...")
try:
    app.include_router(blockbee_router)
    logger.info("✅ BlockBee webhook router registered successfully")
    logger.info("📍 BlockBee webhook endpoint available at: /blockbee/callback/{order_id}")
except Exception as e:
    logger.error(f"❌ Failed to register BlockBee webhook router: {e}")

async def set_bot_application(application):
    """Set the bot application instance for immediate webhook processing"""
    global _bot_application, _startup_complete, _startup_timestamp
    import time
    
    logger.info("🚀 FAST_STARTUP: Setting bot application for immediate uvicorn startup...")
    _bot_application = application
    _startup_complete = True
    _startup_timestamp = time.time()
    
    # REMOVED: Optimization systems - using simplified architecture
    
    logger.info("✅ FAST_STARTUP: Bot application set - ready for uvicorn binding!")

async def initialize_webhook_systems_in_background():
    """Initialize heavy webhook systems in background after uvicorn starts"""
    import asyncio
    
    # Wait a brief moment for uvicorn to complete binding
    await asyncio.sleep(1)
    
    logger.info("✅ BACKGROUND: Simplified webhook systems ready")
    
    try:
        # CRITICAL: Pre-warm crypto rates BEFORE webhooks can be processed
        logger.info("🔥 WEBHOOK_SERVER: Pre-warming critical crypto rates for webhook reliability...")
        from services.fastforex_service import startup_prewarm_critical_rates
        prewarm_success = await startup_prewarm_critical_rates()
        
        if prewarm_success:
            logger.info("✅ WEBHOOK_SERVER: Crypto rates pre-warmed - webhooks ready for immediate processing")
        else:
            logger.warning("⚠️ WEBHOOK_SERVER: Some rates failed to pre-warm - emergency fallback enabled")
        
        # CRITICAL FIX: Initialize webhook intake service to register processors
        logger.info("🚀 WEBHOOK_SERVER: Initializing webhook intake service...")
        from services.webhook_startup_service import initialize_webhook_system
        webhook_status = await initialize_webhook_system()
        
        if webhook_status.get('initialized'):
            logger.info("✅ WEBHOOK_SERVER: Webhook intake service initialized successfully")
            logger.info(f"📋 WEBHOOK_SERVER: Processors registered: {webhook_status.get('processors_registered')}")
        else:
            logger.error(f"❌ WEBHOOK_SERVER: Failed to initialize webhook intake service: {webhook_status.get('error')}")
        
        # SIMPLIFIED: All optimization systems removed
        logger.info("✅ BACKGROUND: Simplified webhook systems complete")
        
    except Exception as e:
        logger.error(f"⚠️ BACKGROUND: Webhook system initialization error (non-critical): {e}")
        # Don't fail - these are optimization features, not core functionality

def _validate_and_fix_webhook_data(data):
    """Validate and attempt to fix basic webhook data structure"""
    try:
        # Basic structure validation
        if not isinstance(data, dict):
            return False
        
        # Check for update_id (required for all Telegram updates)
        if "update_id" not in data:
            logger.warning("Missing update_id in webhook data")
            return False
        
        return True
    except Exception as e:
        logger.error(f"Error validating webhook data: {e}")
        return False

def _fix_malformed_user_data(data):
    """Attempt to fix malformed user data in webhook"""
    try:
        fixed = False
        
        # Function to fix user object in any message/callback_query
        def fix_user_object(user_data):
            if not isinstance(user_data, dict):
                return False
            
            fixed_user = False
            # Add missing required fields with sensible defaults
            if "first_name" not in user_data:
                user_data["first_name"] = "Unknown"
                fixed_user = True
                logger.info("Added missing first_name to user data")
            
            if "is_bot" not in user_data:
                user_data["is_bot"] = False
                fixed_user = True
                logger.info("Added missing is_bot to user data")
            
            # Ensure other common fields exist
            if "last_name" not in user_data:
                user_data["last_name"] = None
            
            if "username" not in user_data:
                user_data["username"] = None
                
            return fixed_user
        
        # Check and fix user data in message
        if "message" in data and isinstance(data["message"], dict):
            message = data["message"]
            if "from" in message:
                if fix_user_object(message["from"]):
                    fixed = True
            
            # Fix missing date field in message (required by Telegram API)
            if "date" not in message:
                import time
                message["date"] = int(time.time())
                logger.info("Added missing date to message data")
                fixed = True
            
            # Ensure message has required fields
            if "message_id" not in message:
                message["message_id"] = 1
                logger.info("Added missing message_id to message data")
                fixed = True
            
            # Fix missing chat fields
            if "chat" in message and isinstance(message["chat"], dict):
                chat = message["chat"]
                if "type" not in chat:
                    chat["type"] = "private"  # Default to private chat
                    logger.info("Added missing type to chat data")
                    fixed = True
                
                if "id" not in chat:
                    # Use from user ID if available, otherwise default
                    chat["id"] = message.get("from", {}).get("id", 0)
                    logger.info("Added missing id to chat data")
                    fixed = True
        
        # Check and fix user data in callback_query
        if "callback_query" in data and isinstance(data["callback_query"], dict):
            if "from" in data["callback_query"]:
                if fix_user_object(data["callback_query"]["from"]):
                    fixed = True
        
        # Check and fix user data in inline_query
        if "inline_query" in data and isinstance(data["inline_query"], dict):
            if "from" in data["inline_query"]:
                if fix_user_object(data["inline_query"]["from"]):
                    fixed = True
        
        # Check and fix user data in chosen_inline_result
        if "chosen_inline_result" in data and isinstance(data["chosen_inline_result"], dict):
            if "from" in data["chosen_inline_result"]:
                if fix_user_object(data["chosen_inline_result"]["from"]):
                    fixed = True
        
        if fixed:
            logger.info("Successfully fixed malformed user data in webhook")
        
        return fixed
        
    except Exception as e:
        logger.error(f"Error fixing malformed user data: {e}")
        return False

@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "Telegram Bot Webhook Server is running"}

@app.get("/health")
async def health_check():
    """Health check endpoint with startup readiness and performance metrics"""
    global _startup_complete, _startup_timestamp
    
    if not _startup_complete or not _bot_application:
        return JSONResponse(
            content={
                "status": "starting", 
                "service": "telegram-bot-webhook",
                "ready": False,
                "message": "Bot application is initializing"
            },
            status_code=503  # Service Unavailable during startup
        )
    
    import time
    uptime = time.time() - _startup_timestamp if _startup_timestamp else 0
    
    # Include performance metrics if available
    performance_report = {}
    try:
        # SIMPLIFIED: webhook_optimizer removed
        performance_report = {"status": "simplified_architecture"}
    except Exception as e:
        logger.debug(f"Performance report unavailable: {e}")
    
    return {
        "status": "healthy", 
        "service": "telegram-bot-webhook",
        "ready": True,
        "uptime_seconds": round(uptime, 2),
        "performance": performance_report
    }

# Webhook warm-up routes to eliminate cold starts
@app.get("/warmup")
async def warmup_endpoint():
    """Warm-up endpoint to pre-heat the webhook processing pipeline"""
    start_time = time.time()
    
    try:
        # Simulate typical webhook processing without actual work
        from utils.database_pool_manager import database_pool
        
        # Warm database connection
        with database_pool.get_session("warmup"):
            pass
        
        # Check if optimizer is warmed up
        optimizer_status = {
            "initialized": True,
            "warmed_up": True  # SIMPLIFIED: always ready in direct processing
        }
        
        warmup_time = (time.time() - start_time) * 1000
        
        return {
            "status": "warmed",
            "warmup_time_ms": round(warmup_time, 2),
            "optimizer": optimizer_status,
            "timestamp": time.time()
        }
        
    except Exception as e:
        logger.error(f"Warmup failed: {e}")
        return JSONResponse(
            content={"status": "warmup_error", "error": str(e)},
            status_code=500
        )


@app.get("/warmup/database")
async def warmup_database():
    """Database-specific warm-up endpoint"""
    start_time = time.time()
    
    try:
        from utils.database_pool_manager import database_pool
        
        # Warm multiple database connections
        for i in range(3):
            with database_pool.get_session(f"warmup_db_{i}"):
                pass
        
        warmup_time = (time.time() - start_time) * 1000
        
        return {
            "status": "database_warmed",
            "connections_warmed": 3,
            "warmup_time_ms": round(warmup_time, 2)
        }
        
    except Exception as e:
        logger.error(f"Database warmup failed: {e}")
        return JSONResponse(
            content={"status": "database_warmup_error", "error": str(e)},
            status_code=500
        )


@app.get("/performance")
async def performance_metrics():
    """Get current webhook performance metrics"""
    try:
        # SIMPLIFIED: webhook_optimizer removed - always ready  
        report = {"status": "simplified_direct_processing"}
        return {
            "status": "ok",
            "metrics": report,
            "timestamp": time.time()
        }
        
    except Exception as e:
        logger.error(f"Performance metrics error: {e}")
        return JSONResponse(
            content={"status": "metrics_error", "error": str(e)},
            status_code=500
        )


@app.post("/webhook")
async def webhook(request: Request):
    """Optimized Telegram webhook handler with P95 <500ms target performance"""
    start_time = time.time()
    trace_id = f"{int(start_time * 1000000) % 100000000:08x}"
    
    # Get client IP for logging
    client_ip = request.client.host if request.client else "unknown"
    
    # Log webhook start with trace ID
    logger.info(f"🔗 WEBHOOK START: telegram from {client_ip} (trace: {trace_id})")
    
    try:
        # FAST PATH: Validate bot token
        if not _bot_application:
            logger.error("❌ Bot application not initialized")
            return JSONResponse(
                content={"error": "Bot not initialized"}, 
                status_code=503
            )
        
        # FAST PATH: Get and validate request body
        try:
            body = await request.body()
            if not body:
                logger.warning("⚠️ Empty webhook body received")
                return JSONResponse(
                    content={"error": "Empty body"}, 
                    status_code=400
                )
            
            data = orjson.loads(body)  # PERFORMANCE: 3-5x faster than json.loads()
            
            # FAST PATH: Basic validation only
            if not isinstance(data, dict) or "update_id" not in data:
                logger.error("❌ Invalid webhook data structure")
                return JSONResponse(
                    content={"error": "Invalid webhook data"}, 
                    status_code=400
                )
            
        except json.JSONDecodeError as e:
            logger.error(f"❌ JSON decode error: {e}")
            return JSONResponse(
                content={"error": "Invalid JSON"}, 
                status_code=400
            )
        
        # SECURITY FIX: Remove malformed data "fixing" to prevent spoofing
        # _fix_malformed_user_data(data)  # DISABLED - potential security risk
        
        # FAST PATH: Create Update object
        update = Update.de_json(data, _bot_application.bot)
        if not update:
            logger.error("❌ Failed to create Update object")
            return JSONResponse(
                content={"error": "Invalid update format"}, 
                status_code=400
            )
        
        # SIMPLIFIED: Direct processing without background deferral
        # All optimization systems removed - direct processing only
        asyncio.create_task(_process_webhook_background_tasks(
                data, trace_id, client_ip
            ))
        
        # Queue update for background processing
        asyncio.create_task(_process_update_background(update, trace_id))
        
        # IMMEDIATE RESPONSE: Return 200 OK within 100ms
        processing_time = (time.time() - start_time) * 1000
        
        # Record webhook latency telemetry
        try:
            from utils.performance_telemetry import telemetry
            telemetry.record_latency('webhook_request', processing_time)
        except Exception:
            pass
        
        # Log slow validation only
        if processing_time > 100:
            logger.warning(f"🐌 Slow webhook validation: {processing_time:.1f}ms (target: <100ms)")
        
        # SIMPLIFIED: Direct background monitoring
        # All optimization systems removed - direct processing only
        asyncio.create_task(_record_webhook_performance(
                processing_time, update, trace_id, True
            ))
        
        # FAST RESPONSE: Return immediately
        response_data = {"ok": True, "processing_time_ms": round(processing_time, 1)}
        logger.info(f"✅ WEBHOOK ACK: telegram (200) in {processing_time:.1f}ms (trace: {trace_id})")
        
        return JSONResponse(content=response_data, status_code=200)
        
    except Exception as e:
        processing_time = (time.time() - start_time) * 1000
        error_msg = str(e)
        
        logger.error(f"❌ WEBHOOK ERROR: telegram in {processing_time:.1f}ms: {error_msg[:100]} (trace: {trace_id})")
        
        # SIMPLIFIED: Direct error processing
        # All optimization systems removed - direct processing only
        asyncio.create_task(_record_webhook_performance(
                processing_time, None, trace_id, False, error_msg
            ))
        
        return JSONResponse(
            content={"error": "Webhook processing failed", "trace_id": trace_id}, 
            status_code=500
        )


# Background task processing functions
async def _process_webhook_background_tasks(data, trace_id, client_ip):
    """Process heavy webhook validation and logging in background"""
    try:
        # SECURITY FIX: Remove malformed data "fixing" to prevent spoofing
        # _fix_malformed_user_data(data)  # DISABLED - potential security risk
        
        # Detailed audit logging
        await log_webhook_request("telegram", client_ip, trace_id, processing_time_ms=0)
        
    except Exception as e:
        logger.debug(f"Background webhook task error (non-critical): {e}")

async def _process_update_background(update, trace_id):
    """Process webhook update in background after timeout"""
    try:
        logger.info(f"🔄 BACKGROUND_PROCESSING: Starting deferred update processing (trace: {trace_id})")
        start_time = time.time()
        
        # Process the update - check bot application is ready
        if _bot_application is None:
            logger.error(f"❌ BACKGROUND_ERROR: Bot application not ready (trace: {trace_id})")
            return
            
        await _bot_application.process_update(update)
        
        processing_time = (time.time() - start_time) * 1000
        logger.info(f"✅ BACKGROUND_COMPLETE: Deferred update processed in {processing_time:.1f}ms (trace: {trace_id})")
        
    except Exception as e:
        logger.error(f"❌ BACKGROUND_ERROR: Failed to process deferred update (trace: {trace_id}): {e}")

async def _record_webhook_performance(processing_time, update, trace_id, success, error_msg=None):
    """Record detailed webhook performance metrics in background"""
    try:
        # Track with completion time monitor (simplified)
        try:
            from utils.completion_time_trends_monitor import completion_time_monitor
            # Note: record_webhook_completion method may not exist - wrapped in try/except
            if hasattr(completion_time_monitor, 'record_webhook_completion'):
                completion_time_monitor.record_webhook_completion(  # type: ignore[misc]
                    processing_time, success, error_msg
                )
        except (ImportError, AttributeError):
            pass  # Skip if monitor not available
        
        # Track latency (simplified)
        logger.debug(f"Webhook latency: {processing_time:.1f}ms (telegram)")
        
        # Audit logging already handled by main webhook endpoint
        # audit_telegram_webhook is a decorator, not a function - removed incorrect calls
        
        # SIMPLIFIED: resilience.record_request_result removed for direct processing
        
    except Exception as e:
        logger.debug(f"Background performance recording error (non-critical): {e}")
        
        # RESILIENCE ENHANCEMENT: Enhanced error recovery with specific handling
        error_type = type(e).__name__
        
        # Handle specific error types
        if "timeout" in str(e).lower() or "asyncio" in error_type.lower():
            logger.warning(f"🔄 TIMEOUT_RECOVERY: Webhook timeout detected - {e}")
            return JSONResponse(
                content={"ok": True, "status": "timeout_recovered", "message": "Request acknowledged despite timeout"}, 
                status_code=200
            )
        
        elif "connection" in str(e).lower() or "network" in str(e).lower():
            logger.warning(f"🌐 CONNECTION_RECOVERY: Network issue detected - {e}")
            return JSONResponse(
                content={"ok": True, "status": "network_recovered", "message": "Request acknowledged despite network issue"}, 
                status_code=200
            )
        
        elif "database" in str(e).lower() or "psycopg" in str(e).lower():
            logger.warning(f"🗄️ DATABASE_RECOVERY: Database issue detected - {e}")
            return JSONResponse(
                content={"ok": True, "status": "db_recovered", "message": "Request acknowledged despite database issue"}, 
                status_code=200
            )
        
        else:
            # Generic error recovery
            logger.error(f"❌ GENERIC_ERROR_RECOVERY: Unhandled error - {e}")
            return JSONResponse(
                content={"ok": True, "status": "error_recovered", "message": "Request acknowledged with error recovery"}, 
                status_code=200
            )


# DynoPay Webhook Endpoints
@app.post("/webhook/dynopay/escrow")
@audit_payment_webhook
@track_webhook_processing("dynopay_escrow")
async def dynopay_escrow_webhook(request: Request):
    """
    QUEUE-BASED ARCHITECTURE: DynoPay escrow webhook intake endpoint.
    Enqueues webhook events to persistent queue for background processing.
    Returns 200 OK immediately to prevent provider retries.
    """
    try:
        # Get webhook data with error handling
        try:
            webhook_data = await request.json()
        except Exception as json_e:
            logger.error(f"Failed to parse DynoPay escrow webhook JSON: {json_e}")
            return HTMLResponse(content="""
            <!DOCTYPE html>
            <html>
            <head><title>Payment Received</title></head>
            <body>
            <h1>Payment Received</h1>
            <p>Your payment has been received and is being processed.</p>
            </body>
            </html>
            """, status_code=200)
        
        # Validate webhook request with error handling
        try:
            from handlers.dynopay_webhook import DynoPayWebhookHandler
            is_valid = await DynoPayWebhookHandler.validate_webhook_request(request)
            if not is_valid:
                logger.warning("DynoPay escrow webhook validation failed - enqueueing anyway")
        except Exception as validation_e:
            logger.warning(f"DynoPay escrow webhook validation error: {validation_e} - enqueueing anyway")
        
        # Extract client IP
        client_ip = request.client.host if request.client else "unknown"
        
        # Enqueue webhook for background processing (Redis-first with SQLite fallback)
        try:
            success, event_id, duration_ms = await enqueue_webhook_with_fallback(
                provider="dynopay",
                endpoint="escrow",
                payload=webhook_data,
                headers=dict(request.headers),
                client_ip=client_ip,
                priority=WebhookEventPriority.HIGH,
                max_retries=3
            )
            
            if success:
                logger.info(f"✅ DYNOPAY_ESCROW: Webhook enqueued successfully (ID: {event_id[:8]}, {duration_ms:.1f}ms)")
            else:
                logger.error("❌ DYNOPAY_ESCROW: Failed to enqueue webhook (circuit breaker may be open)")
                
        except Exception as enqueue_error:
            logger.error(f"❌ DYNOPAY_ESCROW: Error enqueueing webhook: {enqueue_error}")
            # Still return 200 to prevent provider retries
        
        # CRITICAL: Return 200 OK immediately to prevent DynoPay retries
        html_content = """
        <!DOCTYPE html>
        <html>
        <head><title>Payment Received</title></head>
        <body>
        <h1>Payment Received</h1>
        <p>Your payment has been received and is being processed.</p>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content, status_code=200)
        
    except Exception as e:
        logger.error(f"CRITICAL: Unhandled error in DynoPay escrow webhook: {e}", exc_info=True)
        # CRITICAL: Always return 200 to prevent retries
        return HTMLResponse(content="""
        <!DOCTYPE html>
        <html>
        <head><title>Payment Received</title></head>
        <body>
        <h1>Payment Received</h1>
        <p>Your payment is being processed. Thank you!</p>
        </body>
        </html>
        """, status_code=200)


@app.post("/webhook/dynopay/wallet")
@audit_payment_webhook
@track_webhook_processing("dynopay_wallet")
async def dynopay_wallet_webhook(request: Request):
    """
    QUEUE-BASED ARCHITECTURE: DynoPay wallet webhook intake endpoint.
    Enqueues webhook events to persistent queue for background processing.
    Returns 200 OK immediately to prevent provider retries.
    """
    try:
        # Get webhook data with error handling
        try:
            webhook_data = await request.json()
        except Exception as json_e:
            logger.error(f"Failed to parse DynoPay wallet webhook JSON: {json_e}")
            return HTMLResponse(content="""
            <!DOCTYPE html>
            <html>
            <head><title>Deposit Received</title></head>
            <body>
            <h1>Deposit Received</h1>
            <p>Your wallet deposit has been received and is being processed.</p>
            </body>
            </html>
            """, status_code=200)
        
        # Validate webhook request with error handling
        try:
            from handlers.dynopay_webhook import DynoPayWebhookHandler
            is_valid = await DynoPayWebhookHandler.validate_webhook_request(request)
            if not is_valid:
                logger.warning("DynoPay wallet webhook validation failed - enqueueing anyway")
        except Exception as validation_e:
            logger.warning(f"DynoPay wallet webhook validation error: {validation_e} - enqueueing anyway")
        
        # Extract client IP
        client_ip = request.client.host if request.client else "unknown"
        
        # Enqueue webhook for background processing (Redis-first with SQLite fallback)
        try:
            success, event_id, duration_ms = await enqueue_webhook_with_fallback(
                provider="dynopay",
                endpoint="wallet",
                payload=webhook_data,
                headers=dict(request.headers),
                client_ip=client_ip,
                priority=WebhookEventPriority.HIGH,
                max_retries=3
            )
            
            if success:
                logger.info(f"✅ DYNOPAY_WALLET: Webhook enqueued successfully (ID: {event_id[:8]}, {duration_ms:.1f}ms)")
            else:
                logger.error("❌ DYNOPAY_WALLET: Failed to enqueue webhook (circuit breaker may be open)")
                
        except Exception as enqueue_error:
            logger.error(f"❌ DYNOPAY_WALLET: Error enqueueing webhook: {enqueue_error}")
            # Still return 200 to prevent provider retries
        
        # CRITICAL: Return 200 OK immediately to prevent DynoPay retries
        html_content = """
        <!DOCTYPE html>
        <html>
        <head><title>Deposit Received</title></head>
        <body>
        <h1>Deposit Received</h1>
        <p>Your wallet deposit has been received and is being processed.</p>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content, status_code=200)
        
    except Exception as e:
        logger.error(f"CRITICAL: Unhandled error in DynoPay wallet webhook: {e}", exc_info=True)
        # CRITICAL: Always return 200 to prevent retries
        return HTMLResponse(content="""
        <!DOCTYPE html>
        <html>
        <head><title>Deposit Received</title></head>
        <body>
        <h1>Deposit Received</h1>
        <p>Your wallet deposit is being processed. Thank you!</p>
        </body>
        </html>
        """, status_code=200)


@app.post("/webhook/dynopay/exchange")
@audit_payment_webhook
async def dynopay_exchange_webhook(request: Request):
    """
    QUEUE-BASED ARCHITECTURE: DynoPay exchange webhook intake endpoint.
    Enqueues webhook events to persistent queue for background processing.
    Returns 200 OK immediately to prevent provider retries.
    """
    try:
        # Get webhook data with error handling
        try:
            webhook_data = await request.json()
        except Exception as json_e:
            logger.error(f"Failed to parse DynoPay exchange webhook JSON: {json_e}")
            return HTMLResponse(content="""
            <!DOCTYPE html>
            <html>
            <head><title>Payment Received</title></head>
            <body>
            <h1>Payment Received</h1>
            <p>Your exchange payment has been received and is being processed.</p>
            </body>
            </html>
            """, status_code=200)
        
        # Validate webhook request with error handling
        try:
            from handlers.dynopay_exchange_webhook import DynoPayExchangeWebhookHandler
            is_valid = await DynoPayExchangeWebhookHandler.validate_exchange_webhook_request(request)
            if not is_valid:
                logger.warning("DynoPay exchange webhook validation failed - enqueueing anyway")
        except Exception as validation_e:
            logger.warning(f"DynoPay exchange webhook validation error: {validation_e} - enqueueing anyway")
        
        # Extract client IP
        client_ip = request.client.host if request.client else "unknown"
        
        # Enqueue webhook for background processing (Redis-first with SQLite fallback)
        try:
            success, event_id, duration_ms = await enqueue_webhook_with_fallback(
                provider="dynopay",
                endpoint="exchange",
                payload=webhook_data,
                headers=dict(request.headers),
                client_ip=client_ip,
                priority=WebhookEventPriority.HIGH,
                max_retries=3
            )
            
            if success:
                logger.info(f"✅ DYNOPAY_EXCHANGE: Webhook enqueued successfully (ID: {event_id[:8]}, {duration_ms:.1f}ms)")
            else:
                logger.error("❌ DYNOPAY_EXCHANGE: Failed to enqueue webhook (circuit breaker may be open)")
                
        except Exception as enqueue_error:
            logger.error(f"❌ DYNOPAY_EXCHANGE: Error enqueueing webhook: {enqueue_error}")
            # Still return 200 to prevent provider retries
        
        # CRITICAL: Return 200 OK immediately to prevent DynoPay retries
        html_content = """
        <!DOCTYPE html>
        <html>
        <head><title>Payment Received</title></head>
        <body>
        <h1>Payment Received</h1>
        <p>Your exchange payment has been received and is being processed.</p>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content, status_code=200)
        
    except Exception as e:
        logger.error(f"CRITICAL: Unhandled error in DynoPay exchange webhook: {e}", exc_info=True)
        # CRITICAL: Always return 200 to prevent retries
        return HTMLResponse(content="""
        <!DOCTYPE html>
        <html>
        <head><title>Payment Received</title></head>
        <body>
        <h1>Payment Received</h1>
        <p>Your exchange payment is being processed. Thank you!</p>
        </body>
        </html>
        """, status_code=200)


@app.get("/webhook/dynopay/status")
async def dynopay_status():
    """Health check endpoint for DynoPay webhooks"""
    try:
        from services.payment_processor_manager import payment_manager
        from services.dynopay_service import dynopay_service
        
        status = {
            "service": "DynoPay webhook handler",
            "status": "active",
            "provider_available": dynopay_service.is_available(),
            "failover_enabled": payment_manager.is_failover_enabled()
        }
        return JSONResponse(content=status)
        
    except Exception as e:
        logger.error(f"Error getting DynoPay status: {e}")
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)


# Payment Processor Status Endpoint
@app.get("/webhook/payment-status")
async def payment_processor_status():
    """Get status of all payment processors"""
    try:
        from services.payment_processor_manager import payment_manager
        
        status = payment_manager.get_provider_status()
        return JSONResponse(content=status)
        
    except Exception as e:
        logger.error(f"Error getting payment processor status: {e}")
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)


# Admin Email Action Endpoints
@app.post("/admin/complete-cashout/{cashout_id}")
async def admin_complete_cashout(request: Request, cashout_id: str, token: str = Query(None), form_token: str = Form(None, alias="token")):
    """Handle admin cashout completion from email button"""
    try:
        from services.admin_email_actions import AdminEmailActionService
        
        # Get token from query parameter or form data
        actual_token = token or form_token
        
        if not actual_token:
            logger.warning(f"No token provided for cashout completion: {cashout_id}")
            raise HTTPException(status_code=400, detail="Token is required")
        
        # SECURITY FIX: Use atomic token consumption to prevent race conditions
        token_result = await AdminEmailActionService.atomic_consume_admin_token(
            cashout_id=cashout_id,
            token=actual_token,
            action="RETRY",  # Complete action maps to RETRY
            ip_address=request.client.host if request.client else "unknown",
            user_agent=request.headers.get('user-agent') or "unknown"
        )
        
        if not token_result.get("valid", False):
            logger.warning(f"🚨 SECURITY: Invalid token for cashout completion {cashout_id}: {token_result.get('error')}")
            raise HTTPException(status_code=401, detail=f"Invalid or expired token: {token_result.get('error', 'Unknown error')}")
        
        # Process the cashout completion
        result = await AdminEmailActionService.complete_cashout_from_email(cashout_id)
        
        if result.get('success'):
            # Return success page
            return HTMLResponse(content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Cashout Completed</title>
                <style>
                    body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                    .success {{ color: green; font-size: 24px; margin: 20px; }}
                    .details {{ color: #666; font-size: 16px; }}
                </style>
            </head>
            <body>
                <div class="success">✅ Cashout Completed Successfully!</div>
                <div class="details">
                    <p><strong>Cashout ID:</strong> {cashout_id}</p>
                    <p><strong>Status:</strong> {result.get('status', 'Processing')}</p>
                    <p><strong>Amount:</strong> ${result.get('amount', 'N/A')}</p>
                    <p><strong>Currency:</strong> {result.get('currency', 'N/A')}</p>
                    <br>
                    <p>The user will receive their crypto shortly.</p>
                </div>
            </body>
            </html>
            """)
        else:
            # Return error page
            error_msg = result.get('error', 'Unknown error')
            return HTMLResponse(content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Cashout Failed</title>
                <style>
                    body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                    .error {{ color: red; font-size: 24px; margin: 20px; }}
                    .details {{ color: #666; font-size: 16px; }}
                </style>
            </head>
            <body>
                <div class="error">❌ Cashout Failed</div>
                <div class="details">
                    <p><strong>Cashout ID:</strong> {cashout_id}</p>
                    <p><strong>Error:</strong> {error_msg}</p>
                    <br>
                    <p>Please use the Telegram admin panel to manually process this cashout.</p>
                </div>
            </body>
            </html>
            """, status_code=400)
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error handling admin cashout completion: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# Fincra Webhook Endpoint
@app.post("/webhook/api/fincra/webhook")
@audit_payment_webhook  
@track_webhook_processing("fincra")
async def fincra_webhook_endpoint(request: Request):
    """
    QUEUE-BASED ARCHITECTURE: Fincra webhook intake endpoint.
    Enqueues webhook events to persistent queue for background processing.
    Returns 200 OK immediately to prevent provider retries.
    """
    try:
        # CRITICAL FIX: Capture raw body BEFORE parsing for signature verification
        raw_body = await request.body()
        
        # Get webhook data with error handling
        try:
            webhook_data = await request.json()
        except Exception as json_e:
            logger.error(f"Failed to parse Fincra webhook JSON: {json_e}")
            return JSONResponse(
                content={"status": "ok", "message": "Webhook received"},
                status_code=200
            )
        
        # Extract client IP
        client_ip = request.client.host if request.client else "unknown"
        
        # Enqueue webhook for background processing with raw body in metadata (Redis-first with SQLite fallback)
        try:
            success, event_id, duration_ms = await enqueue_webhook_with_fallback(
                provider="fincra",
                endpoint="payment",
                payload=webhook_data,
                headers=dict(request.headers),
                client_ip=client_ip,
                priority=WebhookEventPriority.HIGH,
                max_retries=3,
                metadata={"raw_body": raw_body.decode('utf-8')}
            )
            
            if success:
                logger.info(f"✅ FINCRA_PAYMENT: Webhook enqueued successfully (ID: {event_id[:8]}, {duration_ms:.1f}ms)")
            else:
                logger.error("❌ FINCRA_PAYMENT: Failed to enqueue webhook (circuit breaker may be open)")
                
        except Exception as enqueue_error:
            logger.error(f"❌ FINCRA_PAYMENT: Error enqueueing webhook: {enqueue_error}")
            # Still return 200 to prevent provider retries
        
        # CRITICAL: Return 200 OK immediately to prevent Fincra retries
        return JSONResponse(
            content={"status": "ok"},
            status_code=200
        )
        
    except Exception as e:
        logger.error(f"CRITICAL: Unhandled error in Fincra webhook: {e}", exc_info=True)
        # CRITICAL: Always return 200 to prevent retries
        return JSONResponse(
            content={"status": "ok"},
            status_code=200
        )


# REMOVED: Conflicting uvicorn.run() call that was blocking port 5000
# uvicorn startup is now handled exclusively by main.py's async server.serve()
# This prevents port binding conflicts and startup timeouts

# GET endpoint for complete cashout (for email links)
@app.get("/admin/complete-cashout/{cashout_id}")
async def admin_complete_cashout_get(request: Request, cashout_id: str, token: str):
    """Handle admin cashout completion from email link (GET)"""
    return await admin_complete_cashout(request, cashout_id, token=token, form_token="")  # type: ignore[arg-type]


# Admin Cashout Cancellation Endpoints
@app.post("/admin/cancel-cashout/{cashout_id}")
async def admin_cancel_cashout(request: Request, cashout_id: str, token: str = Query(None), form_token: str = Form(None, alias="token")):
    """Handle admin cashout cancellation with automatic refund"""
    try:
        from services.admin_email_actions import AdminEmailActionService
        
        # Get token from query parameter or form data
        actual_token = token or form_token
        
        if not actual_token:
            logger.warning(f"No token provided for cashout cancellation: {cashout_id}")
            raise HTTPException(status_code=400, detail="Token is required")
        
        # SECURITY FIX: Use atomic token consumption to prevent race conditions
        token_result = await AdminEmailActionService.atomic_consume_admin_token(
            cashout_id=cashout_id,
            token=actual_token,
            action="DECLINE",  # Cancel action maps to DECLINE
            ip_address=request.client.host if request.client else "unknown",  # type: ignore[arg-type]
            user_agent=request.headers.get('user-agent') or "unknown"
        )
        
        if not token_result.get("valid", False):
            logger.warning(f"🚨 SECURITY: Invalid token for cashout cancellation {cashout_id}: {token_result.get('error')}")
            raise HTTPException(status_code=401, detail=f"Invalid or expired token: {token_result.get('error', 'Unknown error')}")
        
        # Process the cashout cancellation and refund (token already consumed atomically)
        # Pass token_consumed=True to skip token validation since we already consumed it
        result = await AdminEmailActionService.cancel_cashout_from_email(
            cashout_id, 
            actual_token, 
            ip_address=request.client.host if request.client else "unknown",  # type: ignore[arg-type]
            user_agent=request.headers.get('user-agent') or "unknown",
            token_already_consumed=True  # Skip token validation since we consumed it atomically
        )
        
        if result.get('success'):
            # Return success page
            return HTMLResponse(content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Cashout Cancelled & Refunded</title>
                <style>
                    body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; max-width: 600px; margin: 0 auto; }}
                    .success {{ background: #fff3cd; border: 1px solid #ffeaa7; padding: 20px; border-radius: 5px; color: #856404; margin: 20px 0; }}
                    .refund {{ background: #d1ecf1; border: 1px solid #bee5eb; padding: 15px; border-radius: 5px; color: #0c5460; margin: 15px 0; }}
                    .details {{ color: #666; font-size: 16px; margin: 10px 0; }}
                </style>
            </head>
            <body>
                <div class="success">
                    <h1>🔄 Cashout Cancelled Successfully</h1>
                </div>
                <div class="details">
                    <p><strong>Cashout ID:</strong> {cashout_id}</p>
                    <p><strong>Original Amount:</strong> {result.get('amount')} {result.get('currency')}</p>
                    <p><strong>Status:</strong> {result.get('status', 'Cancelled').title()}</p>
                </div>
                <div class="refund">
                    <h3>💰 Automatic Refund Processed</h3>
                    <p><strong>Refunded Amount:</strong> {result.get('refund_amount', result.get('amount'))} {result.get('currency')}</p>
                    <p>User's wallet balance has been fully restored.</p>
                </div>
                <p>The user has been notified via Telegram and email.</p>
            </body>
            </html>
            """)
        else:
            # Return error page
            error_msg = result.get('error', 'Unknown error')
            return HTMLResponse(content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Cancellation Failed</title>
                <style>
                    body {{ font-family: Arial, sans-serif; text-align: center; padding: 50px; }}
                    .error {{ color: red; font-size: 24px; margin: 20px; }}
                    .details {{ color: #666; font-size: 16px; }}
                </style>
            </head>
            <body>
                <div class="error">❌ Cancellation Failed</div>
                <div class="details">
                    <p><strong>Cashout ID:</strong> {cashout_id}</p>
                    <p><strong>Error:</strong> {error_msg}</p>
                    <br>
                    <p>Please use the Telegram admin panel to manually handle this cashout.</p>
                </div>
            </body>
            </html>
            """, status_code=400)
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error handling admin cashout cancellation: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# GET endpoint for cancel cashout (for email links)
@app.get("/admin/cancel-cashout/{cashout_id}")
async def admin_cancel_cashout_get(request: Request, cashout_id: str, token: str):
    """Handle admin cashout cancellation from email link (GET)"""
    return await admin_cancel_cashout(request, cashout_id, token=token, form_token="")  # type: ignore[arg-type]


# Admin Dispute Resolution Email Endpoints
@app.get("/admin/resolve-dispute/buyer/{dispute_id}")
async def resolve_dispute_buyer_favor(dispute_id: str, token: str):
    """Resolve dispute in buyer's favor via email action"""
    try:
        from services.admin_email_actions import AdminDisputeEmailService
        
        result = await AdminDisputeEmailService.resolve_buyer_favor_from_email(dispute_id, token)
        
        if result["success"]:
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Dispute Resolved - Buyer Wins</title>
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }}
                    .success {{ background: #d4edda; color: #155724; padding: 15px; border-radius: 5px; border: 1px solid #c3e6cb; }}
                    .details {{ background: #f8f9fa; padding: 15px; margin: 20px 0; border-radius: 5px; }}
                </style>
            </head>
            <body>
                <h1>🎉 Dispute Resolved Successfully</h1>
                <div class="success">
                    <h3>✅ Buyer Wins - Full Refund Processed</h3>
                    <p><strong>Dispute ID:</strong> {dispute_id}</p>
                    <p><strong>Resolution:</strong> Admin ruled in buyer's favor</p>
                    <p><strong>Amount Refunded:</strong> ${result['amount_refunded']:.2f}</p>
                </div>
                <div class="details">
                    <p><strong>What happened:</strong></p>
                    <ul>
                        <li>✅ Buyer has been refunded the full amount minus platform fees</li>
                        <li>✅ Both parties have been notified via Telegram and email</li>
                        <li>✅ Dispute status updated to resolved</li>
                        <li>✅ Transaction audit trail created</li>
                    </ul>
                </div>
                <p><em>This action has been logged for admin audit purposes.</em></p>
            </body>
            </html>
            """
            return HTMLResponse(content=html_content, status_code=200)
        else:
            error_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Dispute Resolution Failed</title>
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }}
                    .error {{ background: #f8d7da; color: #721c24; padding: 15px; border-radius: 5px; border: 1px solid #f5c6cb; }}
                </style>
            </head>
            <body>
                <h1>❌ Dispute Resolution Failed</h1>
                <div class="error">
                    <p><strong>Error:</strong> {result['error']}</p>
                    <p><strong>Dispute ID:</strong> {dispute_id}</p>
                </div>
                <p>Please contact the technical team or try again later.</p>
            </body>
            </html>
            """
            return HTMLResponse(content=error_html, status_code=400)
            
    except Exception as e:
        logger.error(f"Error processing buyer favor resolution: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/admin/resolve-dispute/seller/{dispute_id}")
async def resolve_dispute_seller_favor(dispute_id: str, token: str):
    """Resolve dispute in seller's favor via email action"""
    try:
        from services.admin_email_actions import AdminDisputeEmailService
        
        result = await AdminDisputeEmailService.resolve_seller_favor_from_email(dispute_id, token)
        
        if result["success"]:
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Dispute Resolved - Seller Wins</title>
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }}
                    .success {{ background: #d4edda; color: #155724; padding: 15px; border-radius: 5px; border: 1px solid #c3e6cb; }}
                    .details {{ background: #f8f9fa; padding: 15px; margin: 20px 0; border-radius: 5px; }}
                </style>
            </head>
            <body>
                <h1>🛡️ Dispute Resolved Successfully</h1>
                <div class="success">
                    <h3>✅ Seller Wins - Funds Released</h3>
                    <p><strong>Dispute ID:</strong> {dispute_id}</p>
                    <p><strong>Resolution:</strong> Admin ruled in seller's favor</p>
                    <p><strong>Amount Released:</strong> ${result['amount_released']:.2f}</p>
                </div>
                <div class="details">
                    <p><strong>What happened:</strong></p>
                    <ul>
                        <li>✅ Seller has received the full escrow amount</li>
                        <li>✅ Both parties have been notified via Telegram and email</li>
                        <li>✅ Dispute status updated to resolved</li>
                        <li>✅ Transaction audit trail created</li>
                    </ul>
                </div>
                <p><em>This action has been logged for admin audit purposes.</em></p>
            </body>
            </html>
            """
            return HTMLResponse(content=html_content, status_code=200)
        else:
            error_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Dispute Resolution Failed</title>
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }}
                    .error {{ background: #f8d7da; color: #721c24; padding: 15px; border-radius: 5px; border: 1px solid #f5c6cb; }}
                </style>
            </head>
            <body>
                <h1>❌ Dispute Resolution Failed</h1>
                <div class="error">
                    <p><strong>Error:</strong> {result['error']}</p>
                    <p><strong>Dispute ID:</strong> {dispute_id}</p>
                </div>
                <p>Please contact the technical team or try again later.</p>
            </body>
            </html>
            """
            return HTMLResponse(content=error_html, status_code=400)
            
    except Exception as e:
        logger.error(f"Error processing seller favor resolution: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/admin/escalate-dispute/{dispute_id}")
async def escalate_dispute_manual_review(dispute_id: str, token: str):
    """Escalate dispute for manual review via email action"""
    try:
        from services.admin_email_actions import AdminDisputeEmailService
        
        result = AdminDisputeEmailService.escalate_dispute_from_email(dispute_id, token)
        
        if result["success"]:
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Dispute Escalated</title>
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }}
                    .warning {{ background: #fff3cd; color: #856404; padding: 15px; border-radius: 5px; border: 1px solid #ffeaa7; }}
                    .details {{ background: #f8f9fa; padding: 15px; margin: 20px 0; border-radius: 5px; }}
                </style>
            </head>
            <body>
                <h1>🔄 Dispute Escalated Successfully</h1>
                <div class="warning">
                    <h3>⚠️ Manual Review Required</h3>
                    <p><strong>Dispute ID:</strong> {dispute_id}</p>
                    <p><strong>Status:</strong> Under Manual Review</p>
                </div>
                <div class="details">
                    <p><strong>Next steps:</strong></p>
                    <ul>
                        <li>🔍 This dispute requires in-platform admin attention</li>
                        <li>📋 Admin must review evidence and messages manually</li>
                        <li>⚖️ Complex resolution may involve custom split or additional investigation</li>
                        <li>📞 Consider contacting both parties for additional information</li>
                    </ul>
                </div>
                <p><em>Please log into the admin panel to continue resolution process.</em></p>
            </body>
            </html>
            """
            return HTMLResponse(content=html_content, status_code=200)
        else:
            error_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Dispute Escalation Failed</title>
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }}
                    .error {{ background: #f8d7da; color: #721c24; padding: 15px; border-radius: 5px; border: 1px solid #f5c6cb; }}
                </style>
            </head>
            <body>
                <h1>❌ Dispute Escalation Failed</h1>
                <div class="error">
                    <p><strong>Error:</strong> {result['error']}</p>
                    <p><strong>Dispute ID:</strong> {dispute_id}</p>
                </div>
                <p>Please contact the technical team or try again later.</p>
            </body>
            </html>
            """
            return HTMLResponse(content=error_html, status_code=400)
            
    except Exception as e:
        logger.error(f"Error processing dispute escalation: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/admin/resolve-dispute/split/{dispute_id}")
async def resolve_dispute_split_interface(
    dispute_id: str, 
    token: str, 
    buyer: Optional[float] = None, 
    seller: Optional[float] = None
):
    """
    Show custom split resolution interface for complex disputes
    OR automatically process if buyer/seller percentages are provided
    """
    try:
        from services.admin_email_actions import AdminDisputeEmailService, AdminEmailActionService
        from database import SessionLocal
        from models import Dispute, Escrow, User, AdminActionToken
        from datetime import datetime
        
        # Check if this is an automatic 50/50 split request
        is_auto_split = (buyer is not None and seller is not None and 
                        buyer == 50 and seller == 50)
        
        # Validate database-backed token (without consuming it for interface, consume for auto-split)
        if is_auto_split:
            # Automatically process 50/50 split
            logger.info(f"🟡 Auto-processing 50/50 split for dispute {dispute_id}")
            
            # Consume token atomically
            token_validation = await AdminEmailActionService.atomic_consume_admin_token(
                cashout_id=dispute_id,
                token=token,
                action='CUSTOM_SPLIT'
            )
            
            if not token_validation.get('valid'):
                return HTMLResponse(content=f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Invalid Token</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }}
                        .error {{ background: #f8d7da; color: #721c24; padding: 15px; border-radius: 5px; }}
                    </style>
                </head>
                <body>
                    <h1>❌ Invalid or Expired Token</h1>
                    <div class="error">
                        <p>This dispute resolution link has expired or is invalid.</p>
                        <p><strong>Error:</strong> {token_validation.get('error', 'Unknown error')}</p>
                    </div>
                </body>
                </html>
                """, status_code=401)
            
            # Process 50/50 split
            result = await AdminDisputeEmailService.process_custom_split_from_email(
                dispute_id=dispute_id,
                buyer_percentage=50.0,
                reason="Fair 50/50 split resolution via admin quick action",
                token=token
            )
            
            if result.get("success"):
                return HTMLResponse(content=f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>50/50 Split Applied</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; max-width: 700px; margin: 50px auto; padding: 20px; }}
                        .success {{ background: #d4edda; color: #155724; padding: 20px; border-radius: 8px; margin: 20px 0; border: 2px solid #c3e6cb; }}
                        .split-display {{ display: flex; justify-content: space-around; margin: 30px 0; }}
                        .party {{ text-align: center; padding: 20px; border-radius: 8px; }}
                        .buyer {{ background: #d1ecf1; border: 2px solid #bee5eb; }}
                        .seller {{ background: #d4edda; border: 2px solid #c3e6cb; }}
                        .amount {{ font-size: 28px; font-weight: bold; margin: 10px 0; }}
                    </style>
                </head>
                <body>
                    <h1>🟡 50/50 Split Resolved Successfully</h1>
                    
                    <div class="success">
                        <h3>✅ Fair Resolution Applied</h3>
                        <p><strong>Dispute ID:</strong> {dispute_id}</p>
                        <p><strong>Resolution:</strong> Equal 50/50 split between both parties</p>
                    </div>
                    
                    <div class="split-display">
                        <div class="party buyer">
                            <h4>🛒 Buyer Received</h4>
                            <div class="amount">${float(result.get('buyer_amount', 0)):.2f}</div>
                            <small>50% of net amount</small>
                        </div>
                        <div class="party seller">
                            <h4>🏪 Seller Received</h4>
                            <div class="amount">${float(result.get('seller_amount', 0)):.2f}</div>
                            <small>50% of net amount</small>
                        </div>
                    </div>
                    
                    <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0;">
                        <h4>✅ Actions Completed</h4>
                        <ul>
                            <li>✅ Funds split equally between buyer and seller</li>
                            <li>✅ Both parties notified via Telegram and email</li>
                            <li>✅ Rating system activated for feedback</li>
                            <li>✅ Dispute status updated to resolved</li>
                            <li>✅ Platform fees retained: ${float(result.get('platform_fees', 0)):.2f}</li>
                        </ul>
                    </div>
                    
                    <p><em>This fair resolution has been logged for admin audit purposes.</em></p>
                </body>
                </html>
                """, status_code=200)
            else:
                return HTMLResponse(content=f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>50/50 Split Failed</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }}
                        .error {{ background: #f8d7da; color: #721c24; padding: 15px; border-radius: 5px; }}
                    </style>
                </head>
                <body>
                    <h1>❌ 50/50 Split Failed</h1>
                    <div class="error">
                        <p><strong>Error:</strong> {result.get('error', 'Unknown error')}</p>
                        <p><strong>Dispute ID:</strong> {dispute_id}</p>
                    </div>
                    <p>Please contact the technical team or try again later.</p>
                </body>
                </html>
                """, status_code=400)
        
        # If not auto-split, show the interface (original behavior)
        session = SessionLocal()
        try:
            token_record = session.query(AdminActionToken).filter(
                AdminActionToken.token == token,
                AdminActionToken.cashout_id == str(dispute_id),
                AdminActionToken.action == 'CUSTOM_SPLIT',
                AdminActionToken.used_at.is_(None),
                AdminActionToken.expires_at > datetime.utcnow()
            ).first()
            
            if not token_record:
                return HTMLResponse(content="""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Invalid Token</title>
                    <style>
                        body { font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }
                        .error { background: #f8d7da; color: #721c24; padding: 15px; border-radius: 5px; }
                    </style>
                </head>
                <body>
                    <h1>❌ Invalid or Expired Token</h1>
                    <div class="error">
                        <p>This dispute resolution link has expired or is invalid.</p>
                        <p>Please request a new admin email for this dispute.</p>
                    </div>
                </body>
                </html>
                """, status_code=401)
        finally:
            session.close()
        
        # Get dispute details for interface
        session = SessionLocal()
        try:
            dispute = session.query(Dispute).filter(
                Dispute.id == dispute_id
            ).first()
            
            if not dispute:
                return HTMLResponse(content="""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>Dispute Not Found</title>
                    <style>
                        body { font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }
                        .error { background: #f8d7da; color: #721c24; padding: 15px; border-radius: 5px; }
                    </style>
                </head>
                <body>
                    <h1>❌ Dispute Not Found</h1>
                    <div class="error">
                        <p>The specified dispute could not be found or has already been resolved.</p>
                    </div>
                </body>
                </html>
                """, status_code=404)
            
            # Query escrow directly to avoid lazy loading issue with SQLAlchemy 2.0
            from models import Escrow
            escrow = session.query(Escrow).filter(Escrow.id == dispute.escrow_id).first()
            if not escrow:
                return HTMLResponse(content="Escrow not found", status_code=404)
            
            buyer = session.query(User).filter(User.id == escrow.buyer_id).first()
            seller = session.query(User).filter(User.id == escrow.seller_id).first()
            
            # Calculate amounts
            escrow_amount = Decimal(str(escrow.amount))
            buyer_fee = Decimal(str(escrow.buyer_fee_amount or 0))  # type: ignore[arg-type]
            seller_fee = Decimal(str(escrow.seller_fee_amount or 0))  # type: ignore[arg-type]
            total_fees = buyer_fee + seller_fee
            net_amount = escrow_amount - total_fees
            
            # Convert to float for template calculations
            net_amount_float = float(net_amount)
            escrow_amount_float = float(escrow_amount)
            buyer_fee_float = float(buyer_fee)
            seller_fee_float = float(seller_fee)
            total_fees_float = float(total_fees)
            
            # Create interactive split interface
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Custom Split Resolution - {dispute_id}</title>
                <style>
                    body {{ 
                        font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
                        max-width: 900px; 
                        margin: 30px auto; 
                        padding: 20px; 
                        background: #f8f9fa;
                    }}
                    .container {{ background: white; padding: 30px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
                    .dispute-header {{ background: #e9ecef; padding: 20px; border-radius: 8px; margin-bottom: 25px; }}
                    .split-calculator {{ background: #fff3cd; padding: 20px; border-radius: 8px; margin: 20px 0; }}
                    .amount-display {{ font-size: 24px; font-weight: bold; margin: 10px 0; }}
                    .slider-container {{ margin: 20px 0; }}
                    .slider {{ width: 100%; height: 8px; border-radius: 5px; background: #ddd; outline: none; }}
                    .result-display {{ display: flex; justify-content: space-between; margin: 20px 0; }}
                    .party-result {{ 
                        flex: 1; 
                        margin: 0 10px; 
                        padding: 20px; 
                        border-radius: 8px; 
                        text-align: center;
                        transition: all 0.3s ease;
                    }}
                    .buyer-result {{ background: #d1ecf1; border: 2px solid #bee5eb; }}
                    .seller-result {{ background: #d4edda; border: 2px solid #c3e6cb; }}
                    .fee-info {{ background: #f8f9fa; padding: 15px; border-radius: 5px; margin: 15px 0; }}
                    .action-buttons {{ text-align: center; margin: 30px 0; }}
                    .btn {{ 
                        padding: 12px 30px; 
                        margin: 0 10px; 
                        border: none; 
                        border-radius: 6px; 
                        font-size: 16px; 
                        font-weight: bold; 
                        cursor: pointer; 
                        transition: all 0.3s ease;
                    }}
                    .btn-primary {{ background: #007bff; color: white; }}
                    .btn-primary:hover {{ background: #0056b3; }}
                    .btn-success {{ background: #28a745; color: white; }}
                    .btn-success:hover {{ background: #1e7e34; }}
                    .reason-section {{ margin: 25px 0; }}
                    .reason-textarea {{ 
                        width: 100%; 
                        height: 100px; 
                        padding: 10px; 
                        border: 1px solid #ddd; 
                        border-radius: 5px; 
                        font-family: inherit;
                        resize: vertical;
                    }}
                    .quick-splits {{ margin: 20px 0; }}
                    .quick-split-btn {{ 
                        background: #6c757d; 
                        color: white; 
                        padding: 8px 16px; 
                        margin: 5px; 
                        border: none; 
                        border-radius: 4px; 
                        cursor: pointer;
                    }}
                    .quick-split-btn:hover {{ background: #545b62; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="dispute-header">
                        <h1>⚖️ Custom Split Resolution</h1>
                        <div style="display: flex; justify-content: space-between;">
                            <div>
                                <p><strong>Dispute ID:</strong> {dispute_id}</p>
                                <p><strong>Escrow ID:</strong> #{escrow.escrow_id}</p>
                                <p><strong>Total Amount:</strong> ${escrow_amount_float:.2f}</p>
                            </div>
                            <div>
                                <p><strong>🛒 Buyer:</strong> {getattr(buyer, 'first_name', None) or getattr(buyer, 'username', None) or 'Anonymous'}</p>
                                <p><strong>🏪 Seller:</strong> {getattr(seller, 'first_name', None) or getattr(seller, 'username', None) or 'Anonymous'}</p>
                                <p><strong>💰 Net Amount:</strong> ${net_amount_float:.2f} (after ${total_fees_float:.2f} fees)</p>
                            </div>
                        </div>
                    </div>
                    
                    <div class="split-calculator">
                        <h3>💡 Split Calculator</h3>
                        <p>Drag the slider to adjust how much each party receives from the net amount (${net_amount_float:.2f}):</p>
                        
                        <div class="quick-splits">
                            <strong>Quick Options:</strong>
                            <button class="quick-split-btn" onclick="setPercentage(0)">Buyer 100%</button>
                            <button class="quick-split-btn" onclick="setPercentage(25)">Buyer 75%</button>
                            <button class="quick-split-btn" onclick="setPercentage(50)">Equal Split</button>
                            <button class="quick-split-btn" onclick="setPercentage(75)">Seller 75%</button>
                            <button class="quick-split-btn" onclick="setPercentage(100)">Seller 100%</button>
                        </div>
                        
                        <div class="slider-container">
                            <label for="splitSlider">Buyer receives: <span id="buyerPercent">50</span>%</label>
                            <input type="range" id="splitSlider" class="slider" min="0" max="100" value="50" 
                                   oninput="updateSplit(this.value)">
                            <div style="display: flex; justify-content: space-between; margin-top: 5px;">
                                <span>All to Buyer</span>
                                <span>All to Seller</span>
                            </div>
                        </div>
                        
                        <div class="result-display">
                            <div class="party-result buyer-result">
                                <h4>🛒 Buyer Receives</h4>
                                <div class="amount-display" id="buyerAmount">${net_amount_float * 0.5:.2f}</div>
                                <small id="buyerNote">50% of net amount</small>
                            </div>
                            <div class="party-result seller-result">
                                <h4>🏪 Seller Receives</h4>
                                <div class="amount-display" id="sellerAmount">${net_amount_float * 0.5:.2f}</div>
                                <small id="sellerNote">50% of net amount</small>
                            </div>
                        </div>
                        
                        <div class="fee-info">
                            <p><strong>Platform Fee Handling:</strong> ${total_fees_float:.2f} in platform fees will be retained by LockBay</p>
                            <p>• Buyer Fee: ${buyer_fee_float:.2f} • Seller Fee: ${seller_fee_float:.2f}</p>
                        </div>
                    </div>
                    
                    <div class="reason-section">
                        <h3>📝 Resolution Reasoning (Optional)</h3>
                        <textarea id="resolutionReason" class="reason-textarea" 
                                  placeholder="Explain why this split is fair based on the evidence and dispute details...
                                  
Example: 'Based on the evidence provided, the item was delivered but not as described. Buyer provided photos showing significant quality issues. Seller failed to respond to buyer's initial concerns. Split 70/30 in buyer's favor reflects partial delivery with quality issues.'"
                                  ></textarea>
                    </div>
                    
                    <div class="action-buttons">
                        <button class="btn btn-success" onclick="processCustomSplit()">
                            ✅ Apply Custom Split Resolution
                        </button>
                        <button class="btn btn-primary" onclick="window.history.back()">
                            ← Back to Email
                        </button>
                    </div>
                </div>
                
                <script>
                    const netAmount = {net_amount_float};
                    
                    function updateSplit(buyerPercent) {{
                        const sellerPercent = 100 - buyerPercent;
                        const buyerAmount = (netAmount * buyerPercent / 100);
                        const sellerAmount = (netAmount * sellerPercent / 100);
                        
                        document.getElementById('buyerPercent').textContent = buyerPercent;
                        document.getElementById('buyerAmount').textContent = '$' + buyerAmount.toFixed(2);
                        document.getElementById('sellerAmount').textContent = '$' + sellerAmount.toFixed(2);
                        document.getElementById('buyerNote').textContent = buyerPercent + '% of net amount';
                        document.getElementById('sellerNote').textContent = sellerPercent + '% of net amount';
                    }}
                    
                    function setPercentage(percent) {{
                        document.getElementById('splitSlider').value = percent;
                        updateSplit(percent);
                    }}
                    
                    function processCustomSplit() {{
                        const buyerPercent = document.getElementById('splitSlider').value;
                        const reason = document.getElementById('resolutionReason').value.trim();
                        
                        // Validate minimum length if reason is provided
                        if (reason && reason.length < 20) {{
                            alert('If providing a reason, please make it at least 20 characters for clarity.');
                            return;
                        }}
                        
                        const confirmed = confirm(
                            `Confirm Custom Split Resolution:\\n\\n` +
                            `Buyer: ${{(netAmount * buyerPercent / 100).toFixed(2)}} ({{buyerPercent}}%)\\n` +
                            `Seller: ${{(netAmount * (100 - buyerPercent) / 100).toFixed(2)}} ({{100 - buyerPercent}}%)\\n\\n` +
                            `This action cannot be undone. Proceed?`
                        );
                        
                        if (confirmed) {{
                            // Submit the resolution
                            const form = document.createElement('form');
                            form.method = 'POST';
                            form.action = '/admin/process-split-resolution/{dispute_id}';
                            
                            const tokenInput = document.createElement('input');
                            tokenInput.type = 'hidden';
                            tokenInput.name = 'token';
                            tokenInput.value = '{token}';
                            form.appendChild(tokenInput);
                            
                            const percentInput = document.createElement('input');
                            percentInput.type = 'hidden';
                            percentInput.name = 'buyer_percentage';
                            percentInput.value = buyerPercent;
                            form.appendChild(percentInput);
                            
                            const reasonInput = document.createElement('input');
                            reasonInput.type = 'hidden';
                            reasonInput.name = 'reason';
                            reasonInput.value = reason;
                            form.appendChild(reasonInput);
                            
                            document.body.appendChild(form);
                            form.submit();
                        }}
                    }}
                </script>
            </body>
            </html>
            """
            
            return HTMLResponse(content=html_content, status_code=200)
            
        finally:
            session.close()
            
    except Exception as e:
        logger.error(f"Error showing split resolution interface: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.post("/admin/process-split-resolution/{dispute_id}")
async def process_custom_split_resolution(dispute_id: str, request: Request):
    """Process custom split resolution from web interface"""
    try:
        from services.admin_email_actions import AdminEmailActionService, AdminDisputeEmailService
        
        # Get form data
        form_data = await request.form()
        token = str(form_data.get('token', ''))  # type: ignore[arg-type]
        buyer_percentage = float(form_data.get('buyer_percentage', 50))  # type: ignore[arg-type]
        reason = str(form_data.get('reason', '')).strip()  # type: ignore[arg-type]
        
        # Validate token (reason is optional)
        if not token:
            return HTMLResponse(content="""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Invalid Input</title>
                <style>
                    body { font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }
                    .error { background: #f8d7da; color: #721c24; padding: 15px; border-radius: 5px; }
                </style>
            </head>
            <body>
                <h1>❌ Invalid Input</h1>
                <div class="error">
                    <p>Missing security token. Please use the link from your email.</p>
                </div>
                <button onclick="window.history.back()">← Go Back</button>
            </body>
            </html>
            """, status_code=400)
        
        # Atomically consume token (validate + mark as used in one operation)
        token_result = await AdminEmailActionService.atomic_consume_admin_token(
            cashout_id=str(dispute_id),
            token=token,
            action='CUSTOM_SPLIT',
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get('user-agent')
        )
        
        if not token_result.get("valid", False):
            return HTMLResponse(content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Invalid Token</title>
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }}
                    .error {{ background: #f8d7da; color: #721c24; padding: 15px; border-radius: 5px; }}
                </style>
            </head>
            <body>
                <h1>❌ Invalid or Expired Token</h1>
                <div class="error">
                    <p>This dispute resolution link has expired or is invalid.</p>
                    <p><strong>Error:</strong> {token_result.get('error', 'Unknown error')}</p>
                </div>
            </body>
            </html>
            """, status_code=401)
        
        # Process the custom split resolution using the dispute email service
        # This ensures NET amount is split and platform fees are retained (consistent with web UI display)
        result = await AdminDisputeEmailService.process_custom_split_from_email(
            dispute_id=dispute_id,
            buyer_percentage=buyer_percentage,
            reason=reason if reason else "Custom split resolution via admin web interface",
            token=token
        )
        
        # Note: AdminDisputeEmailService.process_custom_split_from_email() handles notifications internally
        
        if result.get("success"):
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Custom Split Resolution Applied</title>
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 700px; margin: 50px auto; padding: 20px; }}
                    .success {{ background: #d4edda; color: #155724; padding: 20px; border-radius: 8px; margin: 20px 0; }}
                    .details {{ background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0; }}
                    .split-summary {{ display: flex; justify-content: space-between; margin: 20px 0; }}
                    .party-summary {{ 
                        flex: 1; 
                        margin: 0 10px; 
                        padding: 15px; 
                        border-radius: 6px; 
                        text-align: center;
                    }}
                    .buyer-summary {{ background: #d1ecf1; border: 2px solid #bee5eb; }}
                    .seller-summary {{ background: #d4edda; border: 2px solid #c3e6cb; }}
                    .amount {{ font-size: 20px; font-weight: bold; }}
                </style>
            </head>
            <body>
                <h1>⚖️ Custom Split Resolution Applied Successfully</h1>
                
                <div class="success">
                    <h3>✅ Dispute Resolved with Custom Split</h3>
                    <p><strong>Dispute ID:</strong> {dispute_id}</p>
                    <p><strong>Resolution Type:</strong> Custom Split Decision</p>
                </div>
                
                <div class="split-summary">
                    <div class="party-summary buyer-summary">
                        <h4>🛒 Buyer Received</h4>
                        <div class="amount">${float(result.get('buyer_amount', 0)):.2f}</div>
                        <small>{buyer_percentage:.1f}% of net amount</small>
                    </div>
                    <div class="party-summary seller-summary">
                        <h4>🏪 Seller Received</h4>
                        <div class="amount">${float(result.get('seller_amount', 0)):.2f}</div>
                        <small>{100 - buyer_percentage:.1f}% of net amount</small>
                    </div>
                </div>
                
                <div class="details">
                    <h4>📋 Resolution Summary</h4>
                    <p><strong>Net Amount Split:</strong> ${float(result.get('net_amount', 0)):.2f}</p>
                    <p><strong>Platform Fees Retained:</strong> ${float(result.get('platform_fees', 0)):.2f}</p>
                    <p><strong>Escrow ID:</strong> {result.get('escrow_id', 'N/A')}</p>
                    
                    <h4>📝 Admin Reasoning</h4>
                    <div style="background: white; padding: 15px; border-radius: 5px; border-left: 4px solid #007bff;">
                        {reason}
                    </div>
                    
                    <h4>✅ Actions Completed</h4>
                    <ul>
                        <li>✅ Funds distributed according to custom split</li>
                        <li>✅ Both parties notified via Telegram and email</li>
                        <li>✅ Dispute status updated to resolved</li>
                        <li>✅ Admin reasoning recorded for audit trail</li>
                        <li>✅ Platform fees properly allocated</li>
                    </ul>
                </div>
                
                <p><em>This custom resolution has been logged for admin audit purposes with full reasoning documentation.</em></p>
            </body>
            </html>
            """
            return HTMLResponse(content=html_content, status_code=200)
        else:
            error_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Split Resolution Failed</title>
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }}
                    .error {{ background: #f8d7da; color: #721c24; padding: 15px; border-radius: 5px; }}
                </style>
            </head>
            <body>
                <h1>❌ Custom Split Resolution Failed</h1>
                <div class="error">
                    <p><strong>Error:</strong> {result.get('error', 'Unknown error')}</p>
                    <p><strong>Dispute ID:</strong> {dispute_id}</p>
                </div>
                <p>Please contact the technical team or try again later.</p>
                <button onclick="window.history.back()">← Go Back</button>
            </body>
            </html>
            """
            return HTMLResponse(content=error_html, status_code=400)
            
    except Exception as e:
        logger.error(f"Error processing custom split resolution: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/webhook/admin/funding/fund_and_complete/{cashout_id}")
async def admin_fund_and_complete_cashout(cashout_id: str, token: str):
    """Handle admin action: Fund external service account and complete cashout"""
    try:
        from services.admin_funding_notifications import AdminFundingNotificationService
        from services.admin_funding_actions import AdminFundingActionService
        
        # Validate token
        if not AdminFundingNotificationService.validate_funding_token(cashout_id, "fund_and_complete", token):
            return HTMLResponse(
                content="""
                <html><body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                    <h2>🔒 Invalid or Expired Token</h2>
                    <p>This funding action link has expired or is invalid.</p>
                    <p>Please request a new funding notification email.</p>
                </body></html>
                """,
                status_code=401
            )
        
        # Process funding action
        result = await AdminFundingActionService.fund_and_complete_cashout(cashout_id)
        
        if result.get('success'):
            return HTMLResponse(
                content=f"""
                <html><body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                    <h2>✅ Cashout Funding Action Recorded</h2>
                    <p><strong>Cashout ID:</strong> {cashout_id}</p>
                    <p><strong>Action:</strong> Fund Service & Complete Transaction</p>
                    <p><strong>Status:</strong> {result.get('message', 'Processing')}</p>
                    <hr style="margin: 30px 0;">
                    <p><strong>Next Steps:</strong></p>
                    <ol style="text-align: left; display: inline-block;">
                        <li>Fund the external service account (Fincra/Kraken)</li>
                        <li>The cashout will automatically retry and complete</li>
                        <li>User will be notified upon successful completion</li>
                    </ol>
                    <p style="color: #666; font-size: 12px; margin-top: 30px;">
                        Action processed at {result.get('timestamp', 'now')}
                    </p>
                </body></html>
                """
            )
        else:
            error_msg = result.get('error', 'Unknown error')
            return HTMLResponse(
                content=f"""
                <html><body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                    <h2>❌ Funding Action Failed</h2>
                    <p><strong>Cashout ID:</strong> {cashout_id}</p>
                    <p><strong>Error:</strong> {error_msg}</p>
                    <p>Please check the admin panel or contact technical support.</p>
                </body></html>
                """,
                status_code=500
            )
            
    except Exception as e:
        logger.error(f"Error in funding action for {cashout_id}: {e}")
        return HTMLResponse(
            content=f"""
            <html><body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                <h2>⚠️ System Error</h2>
                <p>An error occurred while processing the funding action.</p>
                <p><strong>Error:</strong> {str(e)}</p>
                <p>Please contact technical support.</p>
            </body></html>
            """,
            status_code=500
        )


@app.get("/webhook/admin/funding/cancel_and_refund/{cashout_id}")
async def admin_cancel_and_refund_cashout(cashout_id: str, token: str):
    """Handle admin action: Cancel cashout and refund user gracefully"""
    try:
        from services.admin_funding_notifications import AdminFundingNotificationService
        from services.admin_funding_actions import AdminFundingActionService
        
        # Validate token
        if not AdminFundingNotificationService.validate_funding_token(cashout_id, "cancel_and_refund", token):
            return HTMLResponse(
                content="""
                <html><body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                    <h2>🔒 Invalid or Expired Token</h2>
                    <p>This cancellation action link has expired or is invalid.</p>
                    <p>Please request a new funding notification email.</p>
                </body></html>
                """,
                status_code=401
            )
        
        # Process cancellation action
        result = await AdminFundingActionService.cancel_and_refund_cashout(cashout_id)
        
        if result.get('success'):
            return HTMLResponse(
                content=f"""
                <html><body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                    <h2>✅ Cashout Cancelled & User Refunded</h2>
                    <p><strong>Cashout ID:</strong> {cashout_id}</p>
                    <p><strong>Action:</strong> Cancelled with Full Refund</p>
                    <p><strong>Amount Refunded:</strong> ${result.get('refund_amount', 'N/A')}</p>
                    <p><strong>User Notification:</strong> {result.get('user_notified', 'Sent')}</p>
                    <hr style="margin: 30px 0;">
                    <p style="color: #28a745;"><strong>✅ User has been automatically refunded and notified</strong></p>
                    <p style="color: #666; font-size: 12px; margin-top: 30px;">
                        Action completed at {result.get('timestamp', 'now')}
                    </p>
                </body></html>
                """
            )
        else:
            error_msg = result.get('error', 'Unknown error')
            return HTMLResponse(
                content=f"""
                <html><body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                    <h2>❌ Cancellation Failed</h2>
                    <p><strong>Cashout ID:</strong> {cashout_id}</p>
                    <p><strong>Error:</strong> {error_msg}</p>
                    <p>Please check the admin panel or manually process the refund.</p>
                </body></html>
                """,
                status_code=500
            )
            
    except Exception as e:
        logger.error(f"Error in cancellation action for {cashout_id}: {e}")
        return HTMLResponse(
            content=f"""
            <html><body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                <h2>⚠️ System Error</h2>
                <p>An error occurred while processing the cancellation.</p>
                <p><strong>Error:</strong> {str(e)}</p>
                <p>Please contact technical support.</p>
            </body></html>
            """,
            status_code=500
        )


@app.get("/webhook/admin/address_config/retry/{cashout_id}")
async def admin_retry_after_address_config(cashout_id: str, token: str):
    """Handle admin action: Retry cashout after adding address to Kraken"""
    try:
        from services.admin_funding_notifications import AdminFundingNotificationService
        from services.auto_cashout import AutoCashoutService
        
        # Validate token
        if not AdminFundingNotificationService.validate_funding_token(cashout_id, "retry_after_address_config", token):
            return HTMLResponse(
                content="""
                <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f8f9fa;">
                    <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                        <h1 style="color: #dc3545;">❌ Invalid or Expired Link</h1>
                        <p>This action link is invalid or has expired. Action links are valid for 24 hours for security.</p>
                        <p>Please contact support if you need assistance.</p>
                    </div>
                </body></html>
                """,
                status_code=400
            )
        
        # Process retry action
        from database import async_managed_session
        async with async_managed_session() as session:
            try:
                from models import Cashout, CashoutStatus
                from utils.cashout_state_validator import CashoutStateValidator
                
                # Use async query with select() - refresh from database to get latest cashout_metadata
                from sqlalchemy import select as sql_select  # type: ignore[attr-defined]
                result = await session.execute(
                    sql_select(Cashout).filter_by(cashout_id=cashout_id)
                )
                cashout = result.scalar_one_or_none()
                
                # Force refresh from DB to get latest data including JSONB columns
                if cashout:
                    await session.refresh(cashout)
                
                if not cashout:
                    logger.error(f"Cashout {cashout_id} not found for address config retry")
                    return HTMLResponse(
                        content="""
                        <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f8f9fa;">
                            <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                                <h1 style="color: #dc3545;">❌ Cashout Not Found</h1>
                                <p>Cashout {cashout_id} could not be found in the system.</p>
                            </div>
                        </body></html>
                        """,
                        status_code=404
                    )
                
                # Check if cashout is SUCCESS but awaiting backend completion (address config scenario)
                # FIXED: Handle both Enum and string status values
                cashout_status = cashout.status.value if hasattr(cashout.status, 'value') else cashout.status  # type: ignore[attr-defined]
                if cashout_status == 'success':
                    # CRITICAL FIX: backend_pending is stored in admin_notes, not cashout_metadata
                    # Parse admin_notes to extract the metadata JSON
                    backend_pending = False
                    backend_pending_metadata = None
                    if cashout.admin_notes:  # type: ignore[attr-defined]
                        import re
                        # Extract metadata dict from admin_notes text
                        metadata_match = re.search(r"Metadata: (\{.+\})", cashout.admin_notes, re.DOTALL)  # type: ignore[attr-defined]
                        if metadata_match:
                            try:
                                import ast
                                metadata_str = metadata_match.group(1)
                                # Use ast.literal_eval for Python dict syntax
                                backend_pending_metadata = ast.literal_eval(metadata_str)
                                backend_pending = backend_pending_metadata.get('backend_pending', False)
                            except Exception as parse_error:
                                logger.warning(f"Failed to parse metadata from admin_notes: {parse_error}")
                    
                    if backend_pending:
                        
                        # Call Kraken API to complete the withdrawal now that address is configured
                        from services.kraken_service import get_kraken_service
                        
                        try:
                            # Extract cashout details
                            usd_net_amount = float(cashout.net_amount)  # type: ignore[attr-defined]  # This is USD amount after fees
                            
                            # CRITICAL FIX: Extract crypto currency from metadata, NOT from cashout.currency (which is wallet currency - USD)
                            currency = None
                            if backend_pending_metadata and 'currency' in backend_pending_metadata:
                                currency = backend_pending_metadata['currency']
                            elif backend_pending_metadata and 'crypto_currency' in backend_pending_metadata:
                                currency = backend_pending_metadata['crypto_currency']
                            elif backend_pending_metadata and 'asset' in backend_pending_metadata:
                                currency = backend_pending_metadata['asset']
                            elif backend_pending_metadata and 'technical_details' in backend_pending_metadata and 'currency' in backend_pending_metadata['technical_details']:
                                currency = backend_pending_metadata['technical_details']['currency']
                            elif backend_pending_metadata and 'technical_details' in backend_pending_metadata and 'crypto_currency' in backend_pending_metadata['technical_details']:
                                currency = backend_pending_metadata['technical_details']['crypto_currency']
                            elif backend_pending_metadata and 'technical_details' in backend_pending_metadata and 'asset' in backend_pending_metadata['technical_details']:
                                currency = backend_pending_metadata['technical_details']['asset']
                            
                            if not currency:
                                raise Exception(f"No crypto currency found in cashout metadata for {cashout_id}")
                            
                            # CRITICAL FIX: Extract destination from metadata (not stored in destination_address column)
                            destination = None
                            if backend_pending_metadata and 'destination' in backend_pending_metadata:
                                destination = backend_pending_metadata['destination']
                            elif backend_pending_metadata and 'technical_details' in backend_pending_metadata and 'destination' in backend_pending_metadata['technical_details']:
                                destination = backend_pending_metadata['technical_details']['destination']
                            
                            if not destination:
                                # Fallback to destination_address column if metadata doesn't have it
                                destination = cashout.destination_address  # type: ignore[attr-defined]
                            
                            if not destination:
                                raise Exception("No destination address found in cashout metadata or destination_address column")
                            
                            # IMPORTANT: Save destination to database column for proper record-keeping
                            if not cashout.destination_address:  # type: ignore[attr-defined]
                                cashout.destination_address = destination  # type: ignore[attr-defined]
                                await session.commit()
                            
                            # CRITICAL: Convert USD to crypto amount before sending to Kraken
                            from services.fastforex_service import fastforex_service
                            from decimal import Decimal
                            
                            # Get current crypto to USD rate
                            crypto_usd_rate = await fastforex_service.get_crypto_to_usd_rate(currency.upper())
                            if not crypto_usd_rate:
                                raise Exception(f"Unable to get {currency} exchange rate")
                            
                            # Convert USD amount to crypto amount
                            crypto_amount = Decimal(str(usd_net_amount)) / Decimal(str(crypto_usd_rate))
                            amount = float(crypto_amount)
                            
                            # Generate unique transaction ID for idempotency (use cashout UTID if available)
                            transaction_id = cashout.utid or f"ADMIN_RETRY_{cashout_id}_{int(datetime.utcnow().timestamp())}"  # type: ignore[attr-defined]
                            
                            # Get Kraken service instance
                            kraken_service = get_kraken_service()
                            
                            # Call Kraken API to send crypto with required idempotency context
                            # CRITICAL FIX: force_fresh=True to bypass address cache (user just added address to Kraken)
                            withdrawal_result = await kraken_service.withdraw_crypto(
                                currency=currency,
                                amount=amount,
                                address=destination,
                                cashout_id=cashout_id,
                                session=session,
                                transaction_id=transaction_id,
                                force_fresh=True  # Bypass cache - admin just configured the address in Kraken
                            )
                            
                            if withdrawal_result.get("success"):
                                # Update cashout with transaction ID
                                cashout.external_tx_id = withdrawal_result.get("refid")  # type: ignore[attr-defined]
                                cashout.admin_notes = f"Backend completed via admin retry at {datetime.utcnow().isoformat()}"  # type: ignore[assignment]
                                cashout.updated_at = datetime.utcnow()  # type: ignore[attr-defined]
                                await session.commit()
                                
                                # Send EMAIL-ONLY notification to user after successful withdrawal (no duplicate Telegram notification)
                                try:
                                    from services.withdrawal_notification_service import WithdrawalNotificationService
                                    from models import User
                                    from sqlalchemy import select as sql_select  # type: ignore[attr-defined]
                                    
                                    # Get user details for notification using async pattern
                                    user_result = await session.execute(
                                        sql_select(User).filter_by(id=cashout.user_id)  # type: ignore[attr-defined]
                                    )
                                    user = user_result.scalar_one_or_none()
                                    
                                    if user and user.email:
                                        # Send email-only notification for admin retry (user already got Telegram notification)
                                        notification_service = WithdrawalNotificationService()
                                        
                                        # Send email notification only (no Telegram - user already notified)
                                        email_sent = await notification_service._send_email_notification(
                                            user_email=user.email,
                                            cashout_id=cashout_id,
                                            amount=amount,  # Crypto amount
                                            currency=currency,
                                            blockchain_hash=withdrawal_result.get("refid", ""),  # Kraken refid as blockchain hash
                                            usd_amount=float(usd_net_amount),
                                            destination_address=destination,
                                            pending_funding=False
                                        )
                                        
                                        if email_sent:
                                            logger.info(f"✅ EMAIL_SENT: User {user.telegram_id} ({user.email}) notified via email about admin retry completion for {cashout_id}")
                                        else:
                                            logger.warning(f"⚠️ EMAIL_FAILED: Could not send email to {user.email} about cashout {cashout_id}")
                                    elif user and not user.email:
                                        logger.warning(f"⚠️ NO_EMAIL: User {user.telegram_id} has no email - skipping notification for admin retry {cashout_id}")
                                    else:
                                        logger.error(f"❌ USER_NOT_FOUND: Cannot notify user for cashout {cashout_id}")
                                        
                                except Exception as notification_error:
                                    # Don't fail the withdrawal if notification fails - log it
                                    logger.error(f"❌ NOTIFICATION_ERROR: Failed to notify user about cashout {cashout_id}: {notification_error}")
                                
                                return HTMLResponse(
                                    content=f"""
                                    <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f8f9fa;">
                                        <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                                            <h1 style="color: #28a745;">✅ Transaction Completed!</h1>
                                            <p><strong>Cashout {cashout_id} has been successfully sent via Kraken</strong></p>
                                            <p>Transaction ID: <code>{withdrawal_result.get("refid")}</code></p>
                                            <p>The crypto has been sent to the user's address.</p>
                                            <hr>
                                            <p style="color: #6c757d; font-size: 14px;">Completed at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
                                        </div>
                                    </body></html>
                                    """,
                                    status_code=200
                                )
                            else:
                                error_msg = withdrawal_result.get("error", "Unknown error")
                                return HTMLResponse(
                                    content=f"""
                                    <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f8f9fa;">
                                        <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                                            <h1 style="color: #ffc107;">⚠️ Kraken API Error</h1>
                                            <p><strong>Failed to complete withdrawal for {cashout_id}</strong></p>
                                            <p style="background: #f8d7da; padding: 10px; border-radius: 5px;">{error_msg}</p>
                                            <p>Please check Kraken dashboard or try again.</p>
                                        </div>
                                    </body></html>
                                    """,
                                    status_code=500
                                )
                        except Exception as api_error:
                            logger.error(f"Kraken API error during retry: {api_error}")
                            return HTMLResponse(
                                content=f"""
                                <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f8f9fa;">
                                    <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                                        <h1 style="color: #dc3545;">❌ API Error</h1>
                                        <p>Failed to call Kraken API: {str(api_error)}</p>
                                    </div>
                                </body></html>
                                """,
                                status_code=500
                            )
                    else:
                        # SUCCESS cashout but no backend processing needed
                        logger.warning(f"Cannot retry {cashout_id} - already completed successfully")
                        return HTMLResponse(
                            content=f"""
                            <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f8f9fa;">
                                <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                                    <h1 style="color: #17a2b8;">ℹ️ Already Completed</h1>
                                    <p><strong>Cashout {cashout_id} is already successfully completed</strong></p>
                                    <p>This transaction has been finalized and cannot be retried.</p>
                                </div>
                            </body></html>
                            """,
                            status_code=200
                        )
                
                # Check if cashout is in terminal states (including SUCCESS without backend_pending)
                # FIXED: Handle both Enum and string status values
                terminal_states = ['success', 'failed', 'cancelled']
                if cashout_status in terminal_states:
                    logger.warning(f"Cannot retry {cashout_id} - in terminal state: {cashout_status}")
                    return HTMLResponse(
                        content=f"""
                        <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f8f9fa;">
                            <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                                <h1 style="color: #dc3545;">❌ Cannot Retry</h1>
                                <p><strong>Cashout {cashout_id} is in terminal state: {cashout_status}</strong></p>
                                <p>This transaction cannot be retried. It's either completed or finalized.</p>
                            </div>
                        </body></html>
                        """,
                        status_code=200
                    )
                
                # For non-terminal states (PENDING, ADMIN_PENDING, APPROVED, etc.), reset to approved and retry
                CashoutStateValidator.validate_and_transition(
                    cashout,
                    CashoutStatus.APPROVED,
                    cashout_id=cashout_id,
                    force=False
                )
                cashout.error_message = None  # type: ignore[assignment]
                cashout.updated_at = datetime.utcnow()  # type: ignore[attr-defined]
                await session.commit()
            
                logger.info(f"✅ ADMIN_ACTION: {cashout_id} reset to approved for retry by admin")
                
                # Trigger immediate retry
                retry_result = await AutoCashoutService.process_approved_cashout(cashout.cashout_id, admin_approved=True)  # type: ignore[arg-type]
                
                if retry_result.get("success"):
                    return HTMLResponse(
                        content=f"""
                        <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f8f9fa;">
                            <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                                <h1 style="color: #28a745;">✅ Success!</h1>
                                <p><strong>Cashout {cashout_id} has been successfully retried.</strong></p>
                                <p>The transaction is now processing with the configured address.</p>
                                <hr>
                                <p style="color: #6c757d; font-size: 14px;">Action completed at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
                            </div>
                        </body></html>
                        """,
                        status_code=200
                    )
                else:
                    error_msg = retry_result.get("error", "Unknown error")
                    return HTMLResponse(
                        content=f"""
                        <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f8f9fa;">
                            <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                                <h1 style="color: #ffc107;">⚠️ Retry Issue</h1>
                                <p><strong>Cashout {cashout_id} retry encountered an issue:</strong></p>
                                <p style="background: #f8d7da; padding: 10px; border-radius: 5px;">{error_msg}</p>
                                <p>The cashout has been reset to pending status. Please check the admin panel for more details.</p>
                            </div>
                        </body></html>
                        """,
                        status_code=200
                    )
                
            except Exception as e:
                logger.error(f"Error in database operation for {cashout_id}: {e}")
                return HTMLResponse(
                    content="""
                    <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f8f9fa;">
                        <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                            <h1 style="color: #dc3545;">❌ Database Error</h1>
                            <p>An error occurred while accessing the database.</p>
                            <p>Please contact support for assistance.</p>
                        </div>
                    </body></html>
                    """,
                    status_code=500
                )
            
    except Exception as e:
        logger.error(f"Error in admin address config retry for {cashout_id}: {e}")
        return HTMLResponse(
            content="""
            <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f8f9fa;">
                <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <h1 style="color: #dc3545;">❌ Server Error</h1>
                    <p>An error occurred while processing the retry action.</p>
                    <p>Please contact support for assistance.</p>
                </div>
            </body></html>
            """,
            status_code=500
        )


@app.get("/webhook/admin/address_config/cancel/{cashout_id}")
async def admin_cancel_address_config(cashout_id: str, token: str):
    """Handle admin action: Cancel cashout and refund when address cannot be configured"""
    try:
        from services.admin_funding_notifications import AdminFundingNotificationService
        from services.admin_funding_actions import AdminFundingActionService
        
        # Validate token
        if not AdminFundingNotificationService.validate_funding_token(cashout_id, "cancel_address_config", token):
            return HTMLResponse(
                content="""
                <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f8f9fa;">
                    <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                        <h1 style="color: #dc3545;">❌ Invalid or Expired Link</h1>
                        <p>This action link is invalid or has expired. Action links are valid for 24 hours for security.</p>
                        <p>Please contact support if you need assistance.</p>
                    </div>
                </body></html>
                """,
                status_code=400
            )
        
        # Process cancel and refund action
        result = await AdminFundingActionService.cancel_and_refund_cashout(cashout_id)
        
        if result["success"]:
            return HTMLResponse(
                content=f"""
                <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f8f9fa;">
                    <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                        <h1 style="color: #28a745;">✅ Cashout Cancelled & Refunded</h1>
                        <p><strong>Cashout {cashout_id} has been successfully cancelled.</strong></p>
                        <p>The user has been refunded automatically and notified of the cancellation.</p>
                        <hr>
                        <p style="color: #6c757d; font-size: 14px;">Action completed at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
                    </div>
                </body></html>
                """,
                status_code=200
            )
        else:
            return HTMLResponse(
                content=f"""
                <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f8f9fa;">
                    <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                        <h1 style="color: #dc3545;">❌ Cancellation Failed</h1>
                        <p><strong>Error cancelling cashout {cashout_id}:</strong></p>
                        <p style="background: #f8d7da; padding: 10px; border-radius: 5px;">{result.get('error', 'Unknown error')}</p>
                        <p>Please contact support for manual resolution.</p>
                    </div>
                </body></html>
                """,
                status_code=500
            )
            
    except Exception as e:
        logger.error(f"Error in admin address config cancel for {cashout_id}: {e}")
        return HTMLResponse(
            content="""
            <html><body style="font-family: Arial, sans-serif; margin: 40px; background: #f8f9fa;">
                <div style="background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1);">
                    <h1 style="color: #dc3545;">❌ Server Error</h1>
                    <p>An error occurred while processing the cancellation.</p>
                    <p>Please contact support for assistance.</p>
                </div>
            </body></html>
            """,
            status_code=500
        )


@app.get("/admin/dispute/{dispute_id}/resolve")
async def resolve_dispute_from_email(
    dispute_id: int,
    request: Request,
    token: str = Query(...),
    action: str = Query(...)
):
    """Handle dispute resolution from admin email button clicks"""
    try:
        from services.admin_email_actions import AdminEmailActionService
        
        # Get IP and user agent for audit trail
        ip_address = request.client.host
        user_agent = request.headers.get("user-agent")
        
        # Resolve dispute
        result = await AdminEmailActionService.resolve_dispute_from_email(
            dispute_id=dispute_id,
            action=action,
            token=token,
            ip_address=ip_address,
            user_agent=user_agent
        )
        
        if result.get("success"):
            return HTMLResponse(f"""
            <html>
                <head><title>Dispute Resolved</title></head>
                <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                    <h1 style="color: #28a745;">✅ Dispute Resolved</h1>
                    <p style="font-size: 18px;">Dispute #{dispute_id} has been successfully resolved.</p>
                    <p><strong>Resolution:</strong> {action.replace('_', ' ').title()}</p>
                    <p><strong>Escrow ID:</strong> {result.get('escrow_id')}</p>
                    <p><strong>Amount:</strong> ${result.get('amount', 0):.2f}</p>
                    <p style="margin-top: 30px; color: #6c757d;">You can close this window.</p>
                </body>
            </html>
            """)
        else:
            return HTMLResponse(f"""
            <html>
                <head><title>Error</title></head>
                <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                    <h1 style="color: #dc3545;">❌ Resolution Failed</h1>
                    <p style="font-size: 18px;">{result.get('error', 'Unknown error')}</p>
                    <p style="margin-top: 30px; color: #6c757d;">Please contact support if this persists.</p>
                </body>
            </html>
            """, status_code=400)
            
    except Exception as e:
        logger.error(f"❌ Error resolving dispute {dispute_id} from email: {e}")
        return HTMLResponse(f"""
        <html>
            <head><title>Error</title></head>
            <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                <h1 style="color: #dc3545;">❌ Error</h1>
                <p style="font-size: 18px;">{str(e)}</p>
            </body>
        </html>
        """, status_code=500)


@app.get("/admin/auto-resolve/{dispute_id}")
async def auto_resolve_dispute(dispute_id: str, token: str):
    """Auto-resolve dispute using AI analysis"""
    try:
        from services.admin_email_actions import AdminDisputeEmailService, AdminEmailActionService
        import config
        
        # Check if auto-resolution is enabled in config
        if not getattr(config, 'DISPUTE_AUTO_RESOLUTION_ENABLED', False):  # type: ignore[misc]
            return HTMLResponse(content="""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Auto-Resolution Disabled</title>
                <style>
                    body { font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }
                    .warning { background: #fff3cd; color: #856404; padding: 15px; border-radius: 5px; border: 1px solid #ffeaa7; }
                </style>
            </head>
            <body>
                <h1>🔒 Auto-Resolution Disabled</h1>
                <div class="warning">
                    <p>Automatic dispute resolution is currently disabled by system configuration.</p>
                    <p>Please use manual resolution options instead.</p>
                </div>
            </body>
            </html>
            """, status_code=200)
        
        # Atomically consume token (validate + mark as used in one operation)
        token_result = await AdminEmailActionService.atomic_consume_admin_token(
            cashout_id=str(dispute_id),
            token=token,
            action='CUSTOM_SPLIT',
            ip_address=None,
            user_agent=None
        )
        
        if not token_result.get("valid", False):
            return HTMLResponse(content=f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Invalid Token</title>
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 50px auto; padding: 20px; }}
                    .error {{ background: #f8d7da; color: #721c24; padding: 15px; border-radius: 5px; }}
                </style>
            </head>
            <body>
                <h1>❌ Invalid or Expired Token</h1>
                <div class="error">
                    <p>This dispute resolution link has expired or is invalid.</p>
                    <p><small>Error: {token_result.get('error', 'Unknown error')}</small></p>
                </div>
            </body>
            </html>
            """, status_code=401)
        
        # Process auto-resolution (token already consumed atomically above)
        result = await AdminDisputeEmailService.process_auto_resolution(dispute_id)
        
        if result["success"]:
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Auto-Resolution Completed</title>
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 700px; margin: 50px auto; padding: 20px; }}
                    .success {{ background: #d4edda; color: #155724; padding: 20px; border-radius: 8px; margin: 20px 0; }}
                    .ai-details {{ background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0; }}
                    .confidence-high {{ color: #28a745; font-weight: bold; }}
                </style>
            </head>
            <body>
                <h1>🤖 Auto-Resolution Completed Successfully</h1>
                
                <div class="success">
                    <h3>✅ Dispute Auto-Resolved</h3>
                    <p><strong>Dispute ID:</strong> {dispute_id}</p>
                    <p><strong>AI Decision:</strong> {result['action'].replace('_', ' ').title()}</p>
                    <p><strong>Confidence:</strong> <span class="confidence-high">{result['confidence']}%</span></p>
                </div>
                
                <div class="ai-details">
                    <h4>🎯 AI Analysis Summary</h4>
                    <p><strong>Reasoning:</strong> {result['reason']}</p>
                    <p><strong>Resolution Type:</strong> Automated based on evidence patterns</p>
                    
                    <h4>✅ Actions Completed</h4>
                    <ul>
                        <li>✅ AI analyzed dispute evidence and messages</li>
                        <li>✅ Resolution applied with high confidence threshold</li>
                        <li>✅ Both parties notified automatically</li>
                        <li>✅ Funds distributed according to AI decision</li>
                        <li>✅ Complete audit trail created for review</li>
                        <li>✅ Admin action logged for compliance</li>
                    </ul>
                </div>
                
                <p><em>This auto-resolution was processed using advanced AI analysis and has been fully logged for audit purposes. All parties have been notified of the resolution.</em></p>
            </body>
            </html>
            """
            return HTMLResponse(content=html_content, status_code=200)
        else:
            # Auto-resolution failed - show manual options
            reason = result.get('error', 'Insufficient confidence for automatic resolution')
            if result.get('disabled_by_config'):
                reason = "Auto-resolution is disabled by system configuration"
            
            error_html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>Auto-Resolution Not Available</title>
                <style>
                    body {{ font-family: Arial, sans-serif; max-width: 700px; margin: 50px auto; padding: 20px; }}
                    .warning {{ background: #fff3cd; color: #856404; padding: 20px; border-radius: 8px; margin: 20px 0; }}
                    .manual-options {{ background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0; }}
                    .btn {{ 
                        display: inline-block; 
                        padding: 12px 24px; 
                        margin: 10px; 
                        text-decoration: none; 
                        border-radius: 6px; 
                        font-weight: bold;
                        color: white;
                    }}
                    .btn-success {{ background: #28a745; }}
                    .btn-primary {{ background: #007bff; }}
                    .btn-warning {{ background: #ffc107; color: #212529; }}
                    .btn-info {{ background: #17a2b8; }}
                </style>
            </head>
            <body>
                <h1>⚠️ Auto-Resolution Not Available</h1>
                
                <div class="warning">
                    <h3>🤖 AI Analysis Complete - Manual Review Required</h3>
                    <p><strong>Dispute ID:</strong> {dispute_id}</p>
                    <p><strong>Reason:</strong> {reason}</p>
                </div>
                
                <div class="manual-options">
                    <h4>📋 Manual Resolution Options</h4>
                    <p>The AI analysis is available but requires human judgment for final decision.</p>
                    
                    <div style="text-align: center; margin: 20px 0;">
                        <a href="/admin/resolve-dispute/buyer/{dispute_id}?token={token}" class="btn btn-success">
                            ✅ Buyer Wins
                        </a>
                        <a href="/admin/resolve-dispute/seller/{dispute_id}?token={token}" class="btn btn-primary">
                            🛡️ Seller Wins
                        </a>
                        <a href="/admin/resolve-dispute/split/{dispute_id}?token={token}" class="btn btn-warning">
                            ⚖️ Custom Split
                        </a>
                        <a href="/admin/escalate-dispute/{dispute_id}?token={token}" class="btn btn-info">
                            🔄 Escalate
                        </a>
                    </div>
                </div>
                
                <p><em>AI analysis data is available to support your manual decision-making process.</em></p>
            </body>
            </html>
            """
            return HTMLResponse(content=error_html, status_code=200)  # Not an error, just requires manual
            
    except Exception as e:
        logger.error(f"Error processing auto-resolution: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# Admin Payment Recovery Endpoint
@app.post("/admin/match-payment")
async def match_orphaned_payment(
    address: str = Form(...),
    escrow_id: str = Form(...),
    admin_token: str = Form(...)
):
    """
    Admin recovery endpoint to manually match orphaned payment addresses with escrows.
    
    This fixes cases where payment address was generated but not saved to database,
    causing webhooks to fail matching and preventing escrow completion.
    
    Args:
        address: The crypto payment address (e.g., LTC/BTC/ETH address)
        escrow_id: The escrow ID to associate with this address
        admin_token: Admin authentication token
    """
    try:
        from database import SessionLocal
        from models import Escrow
        from config import Config
        from sqlalchemy import update as sqlalchemy_update
        
        logger.info(f"🔧 ADMIN_RECOVERY: Attempting to match address {address} with escrow {escrow_id}")
        
        # Validate admin token (simple check - enhance with proper auth in production)
        expected_token = getattr(Config, 'ADMIN_EMAIL_SECRET', 'fallback_secret_key_change_me')
        if admin_token != expected_token:
            logger.warning("❌ ADMIN_RECOVERY: Invalid admin token")
            raise HTTPException(status_code=401, detail="Invalid admin token")
        
        # Update escrow with deposit address
        session = SessionLocal()
        try:
            # Check if escrow exists
            escrow = session.query(Escrow).filter(
                Escrow.escrow_id == escrow_id
            ).first()
            
            if not escrow:
                logger.error(f"❌ ADMIN_RECOVERY: Escrow {escrow_id} not found")
                return JSONResponse(
                    content={
                        "success": False,
                        "message": f"Escrow {escrow_id} not found"
                    },
                    status_code=404
                )
            
            # Check if escrow already has a deposit address
            # Compare the actual values, not column objects
            current_address = escrow.deposit_address
            if current_address is not None and current_address != address:  # type: ignore[arg-type]
                logger.warning(f"⚠️ ADMIN_RECOVERY: Escrow {escrow_id} already has different address {current_address}")
                return JSONResponse(
                    content={
                        "success": False,
                        "message": f"Escrow already has different deposit address: {escrow.deposit_address}"
                    },
                    status_code=400
                )
            
            # Update deposit address
            update_stmt = sqlalchemy_update(Escrow).where(
                Escrow.id == escrow.id
            ).values(
                deposit_address=address
            )
            
            result = session.execute(update_stmt)
            session.commit()
            
            if result.rowcount > 0:
                logger.info(f"✅ ADMIN_RECOVERY: Successfully matched address {address} with escrow {escrow_id}")
                
                # Optionally trigger payment check immediately
                try:
                    from services.payment_processor_manager import payment_manager
                    
                    # Try to get payment status from provider
                    # This will trigger webhook if payment already exists
                    asyncio.create_task(
                        payment_manager.get_payment_status(
                            address, 
                            payment_manager.primary_provider
                        )
                    )
                    logger.info(f"🔄 ADMIN_RECOVERY: Initiated payment status check for {address}")
                except Exception as check_error:
                    logger.warning(f"⚠️ ADMIN_RECOVERY: Could not check payment status: {check_error}")
                
                return JSONResponse(
                    content={
                        "success": True,
                        "message": f"Successfully matched address {address} with escrow {escrow_id}",
                        "escrow_id": escrow_id,
                        "address": address
                    },
                    status_code=200
                )
            else:
                logger.error(f"❌ ADMIN_RECOVERY: Failed to update escrow {escrow_id}")
                return JSONResponse(
                    content={
                        "success": False,
                        "message": "Database update failed"
                    },
                    status_code=500
                )
                
        finally:
            session.close()
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ ADMIN_RECOVERY: Error matching payment: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@app.get("/admin/match-payment-form")
async def match_payment_form():
    """Admin form to manually match orphaned payments"""
    html_content = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Admin Payment Recovery</title>
        <style>
            body { 
                font-family: Arial, sans-serif; 
                max-width: 800px; 
                margin: 50px auto; 
                padding: 20px; 
                background: #f5f5f5;
            }
            .container { 
                background: white; 
                padding: 30px; 
                border-radius: 10px; 
                box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            }
            h1 { color: #333; margin-bottom: 10px; }
            .warning { 
                background: #fff3cd; 
                border: 1px solid #ffc107; 
                padding: 15px; 
                border-radius: 5px; 
                margin: 20px 0;
            }
            .form-group { margin: 20px 0; }
            label { 
                display: block; 
                margin-bottom: 5px; 
                font-weight: bold; 
                color: #555;
            }
            input[type="text"] { 
                width: 100%; 
                padding: 10px; 
                border: 1px solid #ddd; 
                border-radius: 5px; 
                font-size: 14px;
                box-sizing: border-box;
            }
            button { 
                background: #28a745; 
                color: white; 
                padding: 12px 30px; 
                border: none; 
                border-radius: 5px; 
                font-size: 16px; 
                cursor: pointer;
                margin-top: 10px;
            }
            button:hover { background: #218838; }
            .info { 
                background: #d1ecf1; 
                border: 1px solid #bee5eb; 
                padding: 15px; 
                border-radius: 5px; 
                margin: 20px 0;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔧 Admin Payment Recovery</h1>
            <p>Manually match orphaned payment addresses with escrows</p>
            
            <div class="warning">
                <strong>⚠️ Admin Only:</strong> This tool is for recovering orphaned payments 
                where the deposit address was generated but not saved to the database.
            </div>
            
            <div class="info">
                <strong>📋 Use Case:</strong> When a payment was sent to an address but the 
                escrow doesn't have the deposit_address field populated, preventing webhook matching.
            </div>
            
            <form method="POST" action="/admin/match-payment">
                <div class="form-group">
                    <label for="address">Payment Address (BTC/LTC/ETH/USDT):</label>
                    <input 
                        type="text" 
                        id="address" 
                        name="address" 
                        placeholder="e.g., LiPTY9zFYfQEBeKn6iBBCMxwYzsJGWJB2HLTC" 
                        required
                    >
                </div>
                
                <div class="form-group">
                    <label for="escrow_id">Escrow ID:</label>
                    <input 
                        type="text" 
                        id="escrow_id" 
                        name="escrow_id" 
                        placeholder="e.g., ES093025XU7Y" 
                        required
                    >
                </div>
                
                <div class="form-group">
                    <label for="admin_token">Admin Token:</label>
                    <input 
                        type="text" 
                        id="admin_token" 
                        name="admin_token" 
                        placeholder="Enter admin secret token" 
                        required
                    >
                </div>
                
                <button type="submit">🔗 Match Payment Address</button>
            </form>
            
            <div class="info" style="margin-top: 30px;">
                <strong>📝 Instructions:</strong>
                <ol>
                    <li>Get the payment address from the user or payment provider logs</li>
                    <li>Get the escrow ID from the database or user report</li>
                    <li>Enter your admin token (ADMIN_EMAIL_SECRET from config)</li>
                    <li>Click "Match Payment Address" to link them</li>
                    <li>System will automatically check for existing payments</li>
                </ol>
            </div>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)


# Support Email Reply Webhook
@app.post("/webhook/email/support-reply")
@audit_twilio_webhook
async def handle_support_email_reply(request: Request):
    """Handle admin email replies and send them to users in bot with audit logging and webhook authentication"""
    try:
        from database import SessionLocal
        from models import SupportTicket, SupportMessage, User
        from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
        from config import Config
        import re
        import os
        
        # SECURITY: Verify webhook authenticity using custom header
        from fastapi import HTTPException
        webhook_token = os.getenv("BREVO_WEBHOOK_SECRET")
        if webhook_token:
            request_token = request.headers.get("X-Webhook-Token") or request.headers.get("X-Brevo-Token")
            if not request_token or request_token != webhook_token:
                logger.error("❌ Unauthorized webhook attempt - invalid or missing token")
                raise HTTPException(status_code=401, detail="Unauthorized")
        else:
            logger.warning("⚠️ BREVO_WEBHOOK_SECRET not set - webhook authentication disabled (SECURITY RISK)")
        
        data = await request.json()
        logger.info(f"📧 Received authenticated support email reply: {data}")
        
        # Handle Brevo inbound email format
        if 'items' in data and len(data['items']) > 0:
            # Brevo format
            email_data = data['items'][0]
            email_from = email_data.get('sender', {}).get('address', '')
            email_subject = email_data.get('subject', '')
            email_body = email_data.get('bodyText', '')
        else:
            # Fallback format (for testing)
            email_from = data.get('from', '')
            email_subject = data.get('subject', '')
            email_body = data.get('text', '')
        
        # Extract ticket ID from subject line (format: "Re: Support Ticket SUP-001")
        ticket_match = re.search(r'SUP-(\d+)', email_subject)
        if not ticket_match:
            logger.warning(f"No ticket ID found in email subject: {email_subject}")
            return {"status": "error", "message": "No ticket ID found"}
            
        ticket_number = int(ticket_match.group(1))
        ticket_id = f"SUP-{ticket_number:03d}"
        
        session = SessionLocal()
        try:
            # Find ticket
            ticket = session.query(SupportTicket).filter(
                SupportTicket.ticket_id == ticket_id  # type: ignore[attr-defined]
            ).first()
            
            if not ticket:
                logger.error(f"Ticket {ticket_id} not found for email reply")
                return {"status": "error", "message": f"Ticket {ticket_id} not found"}
                
            # Find admin user by email (or use default admin) - case-insensitive
            from sqlalchemy import func
            admin_user = session.query(User).filter(
                func.lower(User.email) == func.lower(email_from),
                User.is_admin == True
            ).first()
            
            if not admin_user:
                logger.warning(f"Admin user not found for email: {email_from}, using system admin")
                # Get first admin user as fallback
                admin_user = session.query(User).filter(User.is_admin == True).first()
                
            if not admin_user:
                logger.error("No admin users found in system")
                return {"status": "error", "message": "No admin users configured"}
            
            # Clean email body (remove signatures, quotes, etc.)
            cleaned_message = clean_email_reply(email_body)
            
            # Create support message record
            support_message = SupportMessage(
                ticket_id=ticket.id,
                sender_id=admin_user.id,
                message=cleaned_message,
                is_admin_reply=True
            )
            session.add(support_message)
            
            # Update ticket status and assignment (updated_at auto-updates via onupdate)
            if ticket.status == "open":  # type: ignore[attr-defined]
                ticket.status = "assigned"  # type: ignore[attr-defined]
                ticket.assigned_to = admin_user.id  # type: ignore[attr-defined]
            session.commit()
            
            # Send message to user via bot
            bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)  # type: ignore[attr-defined]
            admin_name = admin_user.first_name or "Admin"  # type: ignore[attr-defined]
            
            user_message = f"""💬 Support Reply - {ticket_id}

👨‍💼 {admin_name} replied:
{cleaned_message}

You can continue the conversation by replying below."""

            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("💬 Continue Chat", callback_data=f"support_chat_open:{ticket.id}")],  # type: ignore[attr-defined]
                [InlineKeyboardButton("✅ Mark Resolved", callback_data=f"support_resolve_ticket:{ticket.id}")]  # type: ignore[attr-defined]
            ])
            
            await bot.send_message(
                chat_id=ticket.user.telegram_id,
                text=user_message,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            
            logger.info(f"✅ Admin email reply forwarded to user for ticket {ticket_id}")
            return {"status": "success", "message": f"Reply forwarded to user for {ticket_id}"}
            
        finally:
            session.close()
        
    except Exception as e:
        logger.error(f"Error processing support email reply: {e}")
        return {"status": "error", "message": str(e)}


def clean_email_reply(email_body: str) -> str:
    """Clean email reply by removing signatures, quotes, and email metadata"""
    lines = email_body.split('\n')
    cleaned_lines = []
    
    for line in lines:
        line = line.strip()
        
        # Skip empty lines at start
        if not cleaned_lines and not line:
            continue
            
        # Stop at common reply indicators
        if any(indicator in line.lower() for indicator in [
            'from:', 'sent:', 'to:', 'subject:', 'on ', 'wrote:',
            '-----original message-----', '________________________________'
        ]):
            break
            
        # Stop at signature indicators
        if line.startswith('--') or line.startswith('_'):
            break
            
        cleaned_lines.append(line)
    
    # Remove trailing empty lines
    while cleaned_lines and not cleaned_lines[-1]:
        cleaned_lines.pop()
    
    return '\n'.join(cleaned_lines).strip()


# Admin Action Webhook Endpoints for Secure Email Actions
@app.get("/webhook/admin_action/{action}/{token}")
async def process_admin_action(request: Request, action: str, token: str):
    """
    Process secure admin actions from email links
    
    Args:
        action: Action type (retry, refund, decline)
        token: Secure action token
        
    Returns:
        HTML confirmation page with action results
    """
    start_time = time.time()
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "Unknown")
    
    try:
        from services.admin_failure_service import admin_failure_service
        from utils.database_pool_manager import database_pool
        from utils.helpers import format_amount
        
        # Log the webhook request for audit trail - parameters simplified
        # Note: log_webhook_request has different signature, using basic logging instead
        logger.info(f"Admin action webhook: {action} from {client_ip}")
        
        # Validate action type
        valid_actions = ['retry', 'refund', 'decline']
        if action not in valid_actions:
            logger.warning(f"Invalid admin action attempted: {action} from IP {client_ip}")
            return create_admin_action_response(
                "error", "Invalid Action", 
                f"The action '{action}' is not supported. Valid actions are: {', '.join(valid_actions)}"
            )
        
        # Process the action using admin failure service
        with database_pool.get_session("admin_webhook_action") as session:
            # Validate and use token
            is_valid, action_token, error_message = admin_failure_service.validate_and_use_token(
                session, token, action.upper(), client_ip, user_agent
            )
            
            if not is_valid:
                logger.warning(f"Invalid token used for {action} from IP {client_ip}: {error_message}")
                return create_admin_action_response(
                    "error", "Token Invalid", 
                    f"The action link is invalid or has expired. {error_message}"
                )
            
            # Get transaction details for display
            cashout_id = action_token.cashout_id  # type: ignore[attr-defined]
            details = admin_failure_service.get_failure_details(session, cashout_id)  # type: ignore[arg-type]
            
            if not details:
                logger.error(f"Cashout {cashout_id} not found for admin action {action}")
                return create_admin_action_response(
                    "error", "Transaction Not Found", 
                    f"The transaction {cashout_id} could not be found."
                )
            
            cashout = details['cashout']
            user = details['user']
            amount_str = format_amount(cashout['amount'], cashout['currency'])
            
            # Execute the requested action - initialize variables first
            admin_user_id = action_token.admin_user_id or 0  # type: ignore[attr-defined]
            admin_email = action_token.admin_email  # type: ignore[attr-defined]
            success = False
            message = "Unknown action"
            action_description = "Unknown Action"
            success_message = "Action completed"
            
            if action == 'retry':
                success, message = admin_failure_service.retry_transaction(
                    session, cashout_id, admin_user_id,  # type: ignore[arg-type]
                    f"Retry via secure email action by {admin_email}"
                )
                action_description = "Transaction Retry"
                success_message = f"Transaction {cashout_id[:8]}... has been queued for retry processing."
                
            elif action == 'refund':
                success, message = admin_failure_service.refund_transaction(
                    session, cashout_id, admin_user_id,  # type: ignore[arg-type]
                    f"Refund via secure email action by {admin_email}"
                )
                action_description = "Transaction Refund"
                success_message = f"Transaction {cashout_id[:8]}... has been refunded to the user's wallet."
                
            elif action == 'decline':
                success, message = admin_failure_service.decline_transaction(
                    session, cashout_id, admin_user_id,  # type: ignore[arg-type]
                    f"Declined via secure email action by {admin_email} - requires review"
                )
                action_description = "Transaction Decline"
                success_message = f"Transaction {cashout_id[:8]}... has been permanently declined."
            
            # Update token with result
            action_token.mark_as_used(  # type: ignore[attr-defined]
                ip_address=client_ip,
                user_agent=user_agent,
                result="SUCCESS" if success else "FAILED",
                error=message if not success else None
            )
            session.commit()
            
            # Log the action result
            processing_time = time.time() - start_time
            if success:
                logger.info(f"Admin action {action} successful for cashout {cashout_id} by {admin_email} (IP: {client_ip})")
            else:
                logger.error(f"Admin action {action} failed for cashout {cashout_id} by {admin_email}: {message}")
            
            # Track completion time
            if hasattr(completion_time_monitor, 'record_completion'):
                completion_time_monitor.record_completion(  # type: ignore[attr-defined]
                    "admin_action_webhook", f"admin_{action}", 
                    int(processing_time * 1000), success
                )
            
            # Create response page
            if success:
                return create_admin_action_response(
                    "success", action_description, success_message,
                    {
                        'cashout_id': cashout_id,
                        'amount': amount_str,
                        'user_name': user['name'],
                        'admin_email': admin_email,
                        'action': action,
                        'processing_time': f"{processing_time:.2f}s"
                    }
                )
            else:
                return create_admin_action_response(
                    "error", f"{action_description} Failed", 
                    f"Failed to process {action}: {message}",
                    {
                        'cashout_id': cashout_id,
                        'amount': amount_str,
                        'user_name': user['name'],
                        'error': message
                    }
                )
                
    except Exception as e:
        processing_time = time.time() - start_time
        logger.error(f"Error processing admin action {action} with token {token[:8]}...: {e}")
        
        # Track failed completion
        if hasattr(completion_time_monitor, 'record_completion'):
            completion_time_monitor.record_completion(  # type: ignore[attr-defined]
                "admin_action_webhook", f"admin_{action}", 
                int(processing_time * 1000), False
            )
        
        return create_admin_action_response(
            "error", "Processing Error", 
            f"An error occurred while processing your request: {str(e)}"
        )


def create_admin_action_response(status: str, title: str, message: str, 
                               details: dict | None = None) -> HTMLResponse:  # type: ignore[misc]
    """Create HTML response page for admin actions"""
    
    # Status-based styling
    status_styles = {
        'success': {
            'color': '#155724',
            'bg_color': '#d4edda',
            'border_color': '#c3e6cb',
            'icon': '✅'
        },
        'error': {
            'color': '#721c24',
            'bg_color': '#f8d7da',
            'border_color': '#f5c6cb',
            'icon': '❌'
        },
        'warning': {
            'color': '#856404',
            'bg_color': '#fff3cd',
            'border_color': '#ffeaa7',
            'icon': '⚠️'
        }
    }
    
    style = status_styles.get(status, status_styles['error'])
    
    # Build details section if provided
    details_html = ""
    if details:
        details_html = "<div style='margin-top: 20px; padding: 15px; background: #f8f9fa; border-radius: 8px;'>"
        details_html += "<h4 style='margin-top: 0; color: #495057;'>Transaction Details</h4>"
        
        for key, value in details.items():
            if key and value:
                # Format key for display
                display_key = key.replace('_', ' ').title()
                details_html += f"<p style='margin: 5px 0;'><strong>{display_key}:</strong> {value}</p>"
        
        details_html += "</div>"
    
    # Create comprehensive HTML response
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title} - Admin Action Result</title>
        <style>
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                line-height: 1.6;
                margin: 0;
                padding: 20px;
                background-color: #f5f5f5;
                color: #333;
            }}
            .container {{
                max-width: 600px;
                margin: 50px auto;
                background: white;
                border-radius: 12px;
                box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                overflow: hidden;
            }}
            .header {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 30px;
                text-align: center;
            }}
            .header h1 {{
                margin: 0;
                font-size: 24px;
                font-weight: 600;
            }}
            .content {{
                padding: 30px;
            }}
            .status-box {{
                padding: 20px;
                border-radius: 8px;
                border: 1px solid {style['border_color']};
                background-color: {style['bg_color']};
                color: {style['color']};
                margin-bottom: 20px;
            }}
            .status-title {{
                font-size: 20px;
                font-weight: 600;
                margin-bottom: 10px;
                display: flex;
                align-items: center;
                gap: 10px;
            }}
            .status-message {{
                font-size: 16px;
                line-height: 1.5;
            }}
            .details {{
                margin-top: 20px;
                padding: 15px;
                background: #f8f9fa;
                border-radius: 8px;
            }}
            .details h4 {{
                margin-top: 0;
                color: #495057;
                font-size: 16px;
            }}
            .details p {{
                margin: 8px 0;
                font-size: 14px;
            }}
            .footer {{
                background: #f8f9fa;
                padding: 20px 30px;
                text-align: center;
                border-top: 1px solid #dee2e6;
                color: #6c757d;
                font-size: 14px;
            }}
            .timestamp {{
                margin-top: 10px;
                font-size: 12px;
                opacity: 0.8;
            }}
            .security-note {{
                background: #e3f2fd;
                border-left: 4px solid #2196f3;
                padding: 15px;
                margin-top: 20px;
                border-radius: 0 8px 8px 0;
            }}
            .security-note h5 {{
                margin: 0 0 10px 0;
                color: #1976d2;
                font-size: 14px;
            }}
            .security-note p {{
                margin: 0;
                font-size: 13px;
                color: #424242;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🛡️ Admin Action Portal</h1>
                <p style="margin: 10px 0 0 0; opacity: 0.9;">Secure Transaction Management</p>
            </div>
            
            <div class="content">
                <div class="status-box">
                    <div class="status-title">
                        <span style="font-size: 24px;">{style['icon']}</span>
                        {title}
                    </div>
                    <div class="status-message">
                        {message}
                    </div>
                </div>
                
                {details_html}
                
                <div class="security-note">
                    <h5>🔒 Security Information</h5>
                    <p>This action was processed using a secure, single-use token. 
                    All admin actions are logged and audited for security purposes. 
                    The action link you used is now expired and cannot be reused.</p>
                </div>
                
                <div class="timestamp">
                    <strong>Processed:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
                </div>
            </div>
            
            <div class="footer">
                <p>Admin Action Webhook System</p>
                <p>If you have questions about this action, please contact your system administrator.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return HTMLResponse(content=html_content)


@app.get("/webhook/queue/status")
async def webhook_queue_status():
    """
    Comprehensive webhook queue status endpoint.
    
    Provides real-time information about:
    - Queue health and statistics
    - Circuit breaker states
    - Processing performance metrics
    - Database connectivity status
    """
    try:
        start_time = time.time()
        
        # SIMPLIFIED: No legacy queue statistics - using direct processing architecture
        # Legacy queue system removed - return simplified status instead
        
        # Get circuit breaker status (keep this for database health monitoring)
        resilience_status = {"status": "simplified_architecture"}
        
        # Simplified status instead of legacy queue/processor stats
        simplified_stats = {
            "architecture": "direct_processing",
            "legacy_queue": "decommissioned",
            "active_handlers": ["blockbee", "dynopay", "fincra"]
        }
        
        # Calculate overall health (simplified - no legacy queue health checks)
        circuit_breaker_health = resilience_status['overall_status']
        
        overall_health = "healthy"
        if circuit_breaker_health == "critical":
            overall_health = "critical"
        elif circuit_breaker_health == "degraded":
            overall_health = "degraded"
        
        response_time = (time.time() - start_time) * 1000
        
        return JSONResponse({
            "status": overall_health,
            "service": "simplified_webhook_processing",
            "response_time_ms": round(response_time, 1),
            "timestamp": time.time(),
            "architecture": simplified_stats,
            "circuit_breaker_status": resilience_status,
            "simplified_processing": "direct_handlers_active",
            "endpoints": {
                "blockbee_payment": "/blockbee/callback/{order_id}",
                "dynopay_payment": "/webhook/dynopay/payment", 
                "dynopay_exchange": "/webhook/dynopay/exchange",
                "fincra_payment": "/fincra/webhook/simplified",
                "status": "/webhook/queue/status"
            }
        })
        
    except Exception as e:
        logger.error(f"❌ WEBHOOK_QUEUE_STATUS: Error retrieving status: {e}")
        return JSONResponse({
            "status": "error",
            "error": str(e),
            "timestamp": time.time()
        }, status_code=500)


# Admin Action Status Endpoint for monitoring
@app.get("/webhook/admin_action/status")
async def admin_action_status():
    """Health check endpoint for admin action webhooks"""
    try:
        from datetime import datetime
        from services.admin_failure_service import admin_failure_service
        from utils.database_pool_manager import database_pool
        
        with database_pool.get_session("admin_status_check") as session:
            # Test database connectivity and basic functionality
            pending_count = len(admin_failure_service.get_pending_failures(
                session, limit=1, offset=0
            ).get('failures', []))
            
            return JSONResponse({
                "status": "healthy",
                "service": "admin_action_webhooks",
                "timestamp": datetime.utcnow().isoformat(),
                "pending_failures": pending_count,
                "endpoints": [
                    "/webhook/admin_action/retry/{token}",
                    "/webhook/admin_action/refund/{token}",
                    "/webhook/admin_action/decline/{token}"
                ]
            })
            
    except Exception as e:
        logger.error(f"Admin action status check failed: {e}")
        from datetime import datetime as dt  # Ensure datetime is available
        return JSONResponse({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": dt.utcnow().isoformat()
        }, status_code=500)

