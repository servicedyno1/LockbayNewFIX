"""
Fincra Webhook Handler - Simplified Architecture

Implements the architect-approved design:
Direct flow: Provider Confirmation → Wallet Credit → Immediate Notification
No background jobs, no complex queues, no over-engineering.

Handles NGN bank payments from Fincra API.
"""

import logging
import json
import hashlib
import hmac
from decimal import Decimal
from fastapi import APIRouter, HTTPException, Request, Header
from typing import Optional, Dict, Any

from services.simplified_payment_processor import simplified_payment_processor
from config import Config

logger = logging.getLogger(__name__)

# Create FastAPI router
router = APIRouter()


@router.post("/fincra/webhook/simplified")
async def fincra_webhook_simplified(
    request: Request,
    signature: Optional[str] = Header(None, alias="signature"),
):
    """
    SIMPLIFIED: Fincra webhook handler - Architect-approved design
    
    Direct flow: Provider Confirmation → Wallet Credit → Immediate Notification
    No background jobs, no complex state machines, no over-engineering.
    
    Handles NGN bank payment confirmations.
    """
    try:
        # Step 1: Parse request data
        webhook_data = await _parse_fincra_data(request)
        
        # Step 2: Verify signature (keep existing security)
        _verify_fincra_signature(request, webhook_data, signature)
        
        # Step 3: Normalize payload and extract key data
        normalized_data = _normalize_fincra_payload(webhook_data)
        
        # Step 4: Validate user_id before processing (security fix)
        if not normalized_data["user_id"]:
            logger.error(f"❌ INVALID_USER: reference={normalized_data.get('reference')} has no valid user_id")
            raise HTTPException(status_code=400, detail="Invalid reference - no associated user")
        
        # Step 5: SIMPLIFIED - Use provider confirmation as authoritative
        processing_result = _trigger_immediate_processing(
            provider="fincra",
            txid=normalized_data["txid"],
            user_id=normalized_data["user_id"],
            amount=normalized_data["amount"],  # Already Decimal
            currency=normalized_data["currency"],
            confirmed=normalized_data["is_confirmed"],
            order_id=normalized_data["reference"],
            raw_data=normalized_data["raw_payload"],
            payment_type="fiat"  # CRITICAL: Fincra handles fiat NGN payments
        )
        
        # Return simple result
        if processing_result["success"]:
            if processing_result["reason"] == "credited":
                return {"status": "success", "message": "Payment credited to wallet"}
            else:
                return {"status": "success", "message": "Payment processed"}
        else:
            return {"status": "success", "message": "Payment received, will retry processing"}
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ FINCRA_WEBHOOK_SIMPLIFIED: Unexpected error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


async def _parse_fincra_data(request: Request) -> Dict[str, Any]:
    """Parse Fincra webhook data from request"""
    body = await request.body()
    
    if not body:
        raise HTTPException(status_code=400, detail="Empty request body")
    
    try:
        webhook_data = await request.json()
    except Exception as e:
        logger.error(f"❌ FINCRA_JSON: Invalid JSON format: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON format")
    
    logger.info(f"📥 FINCRA_WEBHOOK_SIMPLIFIED: Received webhook: {webhook_data}")
    return webhook_data


def _verify_fincra_signature(
    request: Request, 
    webhook_data: Dict[str, Any], 
    signature: Optional[str]
):
    """Verify Fincra webhook signature (keep existing security logic)"""
    is_production = getattr(Config, "ENVIRONMENT", "development").lower() in ["production", "prod"]
    
    if signature:
        # Verify signature in production using Fincra's method
        webhook_secret = getattr(Config, "FINCRA_WEBHOOK_SECRET", None)
        if webhook_secret:
            # Fincra uses HMAC-SHA256 
            payload = json.dumps(webhook_data, separators=(',', ':'))
            expected_signature = hmac.new(
                webhook_secret.encode(),
                payload.encode(),
                hashlib.sha256
            ).hexdigest()
            
            is_valid = hmac.compare_digest(signature, expected_signature)
        else:
            is_valid = False
            logger.warning("⚠️ FINCRA_SECURITY: No webhook secret configured")
        
        if not is_valid:
            if is_production:
                logger.critical("🚨 PRODUCTION_SECURITY_BREACH: Fincra signature verification FAILED")
                raise HTTPException(status_code=401, detail="Invalid webhook signature")
            else:
                logger.warning("⚠️ DEV_SECURITY: Signature verification failed - processing anyway")
        else:
            logger.info("✅ FINCRA_SECURITY: Signature verified successfully")
    else:
        if is_production:
            logger.critical("🚨 PRODUCTION_SECURITY_BREACH: Missing signature in production")
            raise HTTPException(status_code=401, detail="Missing webhook signature")
        else:
            logger.warning("⚠️ DEV_SECURITY: No signature header found - processing anyway in development")


def _normalize_fincra_payload(webhook_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize Fincra payload into standardized format for simplified processor
    """
    # Extract core transaction data from Fincra payload
    data = webhook_data.get("data", {})
    event_type = webhook_data.get("event", "")
    
    # Extract transaction details
    business_id = data.get("business_id", "")
    reference = data.get("reference", "")
    amount_received = data.get("amountReceived", 0)
    currency = data.get("currency", "NGN").upper()
    
    if not reference:
        raise HTTPException(status_code=400, detail="Missing reference in webhook")
    
    if not business_id:
        # Use reference as txid if business_id is not available
        txid = reference
    else:
        txid = business_id
    
    # Extract user ID from reference (format: LKBY_VA_wallet_funding_{user_id}_{timestamp})
    user_id = None
    if reference and "_" in reference:
        try:
            parts = reference.split("_")
            if len(parts) >= 2:
                # User ID is second to last part for wallet funding references
                user_id = int(parts[-2])
        except (ValueError, IndexError):
            logger.warning(f"⚠️ USER_ID_EXTRACT: Could not extract user_id from reference: {reference}")
    
    # Determine if payment is confirmed
    # Fincra sends different event types - "disbursement.successful" means confirmed
    is_confirmed = event_type.lower() in ["disbursement.successful", "transfer.successful", "payout.successful"]
    
    try:
        amount_decimal = Decimal(str(amount_received))
    except (ValueError, TypeError):
        amount_decimal = Decimal('0')
    
    normalized = {
        "provider": "fincra",
        "txid": txid,
        "reference": reference,
        "currency": currency,
        "amount": amount_decimal,
        "user_id": user_id,
        "is_confirmed": is_confirmed,
        "raw_payload": webhook_data
    }
    
    logger.info(f"🔄 NORMALIZED: txid={txid}, amount={amount_decimal} {currency}, confirmed={is_confirmed}, user_id={user_id}")
    return normalized


def _trigger_immediate_processing(
    provider: str,
    txid: str,
    user_id: int,
    amount: Decimal,
    currency: str,
    confirmed: bool,
    order_id: str,
    raw_data: Dict[str, Any],
    payment_type: str = "fiat"
) -> Dict[str, Any]:
    """
    Simplified: Use provider confirmation as authoritative - no complex state machines
    
    If Fincra says it's confirmed, credit wallet immediately.
    """
    try:
        # Use simplified processor - treats provider confirmation as authoritative
        result = simplified_payment_processor.process_payment(
            provider=provider,
            txid=txid,
            user_id=user_id,
            amount=amount,
            currency=currency,
            confirmed=confirmed,
            order_id=order_id,
            raw_data=raw_data,
            payment_type=payment_type
        )
        
        if result["success"]:
            logger.info(f"✅ SIMPLIFIED_SUCCESS: {txid} - {result.get('reason')}")
        else:
            logger.warning(f"⚠️ SIMPLIFIED_DEFERRED: {txid} - {result.get('reason')}")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ SIMPLIFIED_ERROR: txid={txid}, error={e}")
        return {
            "success": False,
            "reason": "processing_error",
            "message": f"Failed to process payment: {e}"
        }


@router.get("/fincra/status/simplified")
async def fincra_status_simplified():
    """Health check endpoint for simplified Fincra integration"""
    try:
        return {
            "status": "operational",
            "service": "Fincra Webhook Handler (Simplified Architecture)",
            "version": "1.0-simplified",
            "features": [
                "Direct provider confirmation processing",
                "NGN to USD conversion", 
                "Immediate wallet crediting",
                "Instant notifications",
                "Concurrency protection"
            ]
        }
    except Exception as e:
        logger.error(f"Fincra status check failed: {e}")
        return {
            "status": "error",
            "service": "Fincra Webhook Handler (Simplified)",
            "error": str(e),
        }