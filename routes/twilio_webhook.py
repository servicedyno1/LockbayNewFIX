"""
Twilio Webhook Routes for SMS Status Updates
FastAPI routes to handle Twilio webhook callbacks
"""

import logging
from fastapi import APIRouter, Request, HTTPException, Header
from typing import Optional
from services.webhook_security_service import WebhookSecurityService
from config import Config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/twilio", tags=["twilio"])


@router.post("/sms/status")
async def handle_sms_status_callback(
    request: Request, x_twilio_signature: Optional[str] = Header(None)
):
    """
    Handle Twilio SMS status callback webhooks
    Updates SMS delivery status and costs
    """
    try:
        # Get raw body for signature validation
        raw_body = await request.body()
        
        # Validate webhook signature for security
        if Config.TWILIO_AUTH_TOKEN:
            if not x_twilio_signature:
                logger.error("Twilio webhook rejected: missing signature header")
                raise HTTPException(status_code=401, detail="Missing webhook signature")
            
            validation_result = WebhookSecurityService.validate_twilio_webhook(
                request=request,
                body=raw_body,
                auth_token=Config.TWILIO_AUTH_TOKEN
            )
            
            if not validation_result.get("valid"):
                logger.error(f"Twilio webhook validation failed: {validation_result.get('error')}")
                raise HTTPException(status_code=401, detail="Invalid webhook signature")
            
            logger.info("Twilio webhook signature validated successfully")
        
        # Get form data from Twilio
        form_data = await request.form()
        webhook_data = dict(form_data)

        # Basic logging for now - webhook handler to be implemented
        logger.info(f"Twilio status callback from {getattr(request.client, 'host', 'unknown')}: {webhook_data}")
        result = {"success": True, "message": "Status callback logged"}

        if result["success"]:
            logger.info(f"Processed Twilio status callback: {result}")
            return {"status": "success", "message": "Status updated"}
        else:
            logger.error(f"Failed to process Twilio callback: {result}")
            raise HTTPException(status_code=500, detail=result.get("error"))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in Twilio status webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/sms/incoming")
async def handle_incoming_sms(
    request: Request, x_twilio_signature: Optional[str] = Header(None)
):
    """
    Handle incoming SMS messages (optional for two-way communication)
    """
    try:
        # Get raw body for signature validation
        raw_body = await request.body()
        
        # Validate webhook signature
        if Config.TWILIO_AUTH_TOKEN:
            if not x_twilio_signature:
                logger.error("Twilio incoming SMS rejected: missing signature header")
                raise HTTPException(status_code=401, detail="Missing webhook signature")
            
            validation_result = WebhookSecurityService.validate_twilio_webhook(
                request=request,
                body=raw_body,
                auth_token=Config.TWILIO_AUTH_TOKEN
            )
            
            if not validation_result.get("valid"):
                logger.error(f"Twilio incoming SMS validation failed: {validation_result.get('error')}")
                raise HTTPException(status_code=401, detail="Invalid webhook signature")
            
            logger.info("Twilio incoming SMS webhook signature validated successfully")
        
        # Get form data from Twilio
        form_data = await request.form()
        webhook_data = dict(form_data)

        # Log incoming SMS
        logger.info(f"Incoming SMS from {getattr(request.client, 'host', 'unknown')}: {webhook_data}")

        # Return TwiML response (empty for now)
        return {
            "content": """<?xml version="1.0" encoding="UTF-8"?>
<Response>
</Response>""",
            "media_type": "application/xml",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in incoming SMS webhook: {e}")
        return {
            "content": """<?xml version="1.0" encoding="UTF-8"?>
<Response>
</Response>""",
            "media_type": "application/xml",
        }


@router.get("/health")
async def twilio_webhook_health():
    """Health check for Twilio webhook endpoints"""
    return {"status": "healthy", "service": "twilio_webhooks"}
