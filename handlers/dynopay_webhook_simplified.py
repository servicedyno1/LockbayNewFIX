"""
DynoPay Webhook Handler - Simplified Architecture

Implements the architect-approved design:
Direct flow: Provider Confirmation → Wallet Credit → Immediate Notification
No background jobs, no complex queues, no over-engineering.
"""

import logging
import json
from decimal import Decimal
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request, Header
from fastapi.responses import JSONResponse
from typing import Optional, Dict, Any
from sqlalchemy import and_, or_, select

from models import Transaction
from services.simplified_payment_processor import simplified_payment_processor
from utils.session_manager import SessionManager

logger = logging.getLogger(__name__)

# Create FastAPI router
router = APIRouter()


@router.api_route("/dynopay/webhook/{reference_id}", methods=["GET", "POST"])
async def dynopay_webhook_simplified(
    reference_id: str,
    request: Request,
    x_signature: Optional[str] = Header(None, alias="X-Signature"),
):
    """
    SIMPLIFIED: DynoPay webhook handler - Architect-approved design
    
    Direct flow: Provider Confirmation → Wallet Credit → Immediate Notification
    No background jobs, no complex state machines, no over-engineering.
    """
    try:
        # Step 1: Parse request data
        webhook_data = await _parse_dynopay_data(request, reference_id)
        
        # Step 2: Verify signature (keep existing security)
        _verify_dynopay_signature(request, webhook_data, x_signature, reference_id)
        
        # Step 3: Normalize payload and extract key data
        normalized_data = _normalize_dynopay_payload(webhook_data, reference_id)
        
        # Step 4: Validate user_id before processing (security fix)
        if not normalized_data["user_id"]:
            logger.error(f"❌ INVALID_USER: reference_id={reference_id} has no valid user_id")
            raise HTTPException(status_code=400, detail="Invalid reference - no associated user")
        
        # Step 5: SIMPLIFIED - Use provider confirmation as authoritative
        processing_result = _trigger_immediate_processing(
            provider="dynopay",
            txid=normalized_data["txid"],
            user_id=normalized_data["user_id"],
            amount=normalized_data["amount"],  # Already Decimal
            coin=normalized_data["coin"],
            confirmed=normalized_data["is_confirmed"],
            order_id=normalized_data["reference_id"],
            raw_data=normalized_data["raw_payload"]
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
        logger.error(f"❌ DYNOPAY_WEBHOOK_SIMPLIFIED: Unexpected error for reference {reference_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


async def _parse_dynopay_data(request: Request, reference_id: str) -> Dict[str, Any]:
    """Parse DynoPay webhook data from request"""
    body = await request.body()
    query_params = dict(request.query_params)
    
    # For GET requests, use query parameters
    if not body and query_params:
        webhook_data = query_params
        logger.info(f"📥 DYNOPAY_GET: Using query parameters for reference {reference_id}")
    else:
        if not body:
            raise HTTPException(status_code=400, detail="Empty request body")
        
        try:
            webhook_data = await request.json()
        except Exception as e:
            logger.error(f"❌ DYNOPAY_JSON: Invalid JSON format: {e}")
            raise HTTPException(status_code=400, detail="Invalid JSON format")
    
    logger.info(f"📥 DYNOPAY_WEBHOOK_SIMPLIFIED: Received webhook for reference {reference_id}: {webhook_data}")
    return webhook_data


def _verify_dynopay_signature(
    request: Request, 
    webhook_data: Dict[str, Any], 
    x_signature: Optional[str], 
    reference_id: str
):
    """Verify DynoPay webhook signature - always enforced regardless of environment"""
    from config import Config
    import hmac
    import hashlib
    
    # CRITICAL SECURITY: Always require signature
    if not x_signature:
        logger.critical(f"🚨 SECURITY_BREACH: Missing signature header - Reference: {reference_id}")
        raise HTTPException(status_code=401, detail="Missing webhook signature")
    
    # CRITICAL SECURITY: Always require secret
    webhook_secret = getattr(Config, "DYNOPAY_WEBHOOK_SECRET", None)
    if not webhook_secret:
        logger.critical(f"🚨 SECURITY_BREACH: DYNOPAY_WEBHOOK_SECRET not configured - Reference: {reference_id}")
        raise HTTPException(status_code=401, detail="Webhook security not configured")
    
    # Verify signature
    expected_signature = hmac.new(
        webhook_secret.encode('utf-8'),
        json.dumps(webhook_data).encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    # Remove any 'sha256=' prefix from signature
    clean_signature = x_signature.replace('sha256=', '') if x_signature.startswith('sha256=') else x_signature
    is_valid = hmac.compare_digest(expected_signature, clean_signature)
    
    if not is_valid:
        logger.critical(f"🚨 SECURITY_BREACH: DynoPay signature verification FAILED - Reference: {reference_id}")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    
    logger.info(f"✅ SECURITY: DynoPay signature verified successfully - Reference: {reference_id}")


def _normalize_dynopay_payload(webhook_data: Dict[str, Any], reference_id: str) -> Dict[str, Any]:
    """
    Normalize DynoPay payload into standardized format for simplified processor
    """
    # Extract core transaction data - DynoPay uses payment_id, transaction_reference, or id
    transaction_id = (
        webhook_data.get("payment_id")
        or webhook_data.get("transaction_reference")
        or webhook_data.get("txId")
        or webhook_data.get("id")
        or ""
    )
    # Amount fields: DynoPay uses 'amount' or 'paid_amount'
    paid_amount = webhook_data.get("paid_amount") or webhook_data.get("amount", 0)
    paid_currency = (webhook_data.get("paid_currency") or webhook_data.get("currency") or "").upper()
    status = webhook_data.get("status", "").lower()
    event = webhook_data.get("event", "").lower()
    
    if not transaction_id:
        raise HTTPException(status_code=400, detail="Missing transaction ID in webhook")
    
    # Extract user ID from reference_id (ESCROW-{user_id}-... or WALLET-YYYYMMDD-HHMMSS-{user_id})
    user_id = None
    if reference_id.startswith("ESCROW-") and "-" in reference_id:
        try:
            parts = reference_id.split("-")
            if len(parts) >= 2:
                user_id = int(parts[1])
        except (ValueError, IndexError):
            logger.warning(f"⚠️ USER_ID_EXTRACT: Could not extract user_id from reference_id: {reference_id}")
    elif reference_id.startswith("WALLET-") and "-" in reference_id:
        try:
            # Format: WALLET-YYYYMMDD-HHMMSS-{user_id}
            user_id = int(reference_id.split("-")[-1])
        except (ValueError, IndexError):
            logger.warning(f"⚠️ USER_ID_EXTRACT: Could not extract user_id from wallet reference: {reference_id}")
    
    # Also try meta_data.user_id as fallback
    if not user_id:
        meta_data = webhook_data.get("meta_data", {})
        if isinstance(meta_data, dict):
            uid = meta_data.get("user_id")
            if uid:
                try:
                    user_id = int(uid)
                except (ValueError, TypeError):
                    pass
    
    # Determine if payment is confirmed - check both status and event fields
    is_confirmed = (
        status in ["completed", "success", "confirmed", "processing"]
        or event in ["payment.confirmed", "payment.completed", "payment.success"]
    )
    
    # Use base_amount (USD) if available, otherwise use crypto amount
    base_amount = webhook_data.get("base_amount")
    if base_amount and webhook_data.get("base_currency", "").upper() == "USD":
        try:
            amount_decimal = Decimal(str(base_amount))
        except (ValueError, TypeError):
            amount_decimal = Decimal('0')
        paid_currency = "USD"
    else:
        try:
            amount_decimal = Decimal(str(paid_amount))
        except (ValueError, TypeError):
            amount_decimal = Decimal('0')
    
    normalized = {
        "provider": "dynopay",
        "txid": transaction_id,
        "reference_id": reference_id,
        "coin": paid_currency,
        "amount": amount_decimal,
        "user_id": user_id,
        "is_confirmed": is_confirmed,
        "raw_payload": webhook_data
    }
    
    logger.info(f"🔄 NORMALIZED: txid={transaction_id}, amount={amount_decimal} {paid_currency}, confirmed={is_confirmed}, user_id={user_id}")
    return normalized


def _trigger_immediate_processing(
    provider: str,
    txid: str,
    user_id: int,
    amount: Decimal,
    coin: str,
    confirmed: bool,
    order_id: str,
    raw_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Simplified: Use provider confirmation as authoritative - no complex state machines
    
    If DynoPay says it's confirmed, credit wallet immediately.
    """
    try:
        # Use simplified processor - treats provider confirmation as authoritative
        result = simplified_payment_processor.process_payment(
            provider=provider,
            txid=txid,
            user_id=user_id,
            amount=amount,
            currency=coin,
            confirmed=confirmed,
            order_id=order_id,
            raw_data=raw_data,
            payment_type="crypto"
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



async def _handle_dynopay_generic_webhook(request: Request, webhook_type: str) -> JSONResponse:
    """
    Generic handler for DynoPay webhooks routed from backend/server.py.
    Extracts reference_id from the payload and delegates to the simplified processor.
    """
    try:
        body = await request.body()
        query_params = dict(request.query_params)

        if not body and query_params:
            webhook_data = query_params
        elif body:
            webhook_data = json.loads(body)
        else:
            return JSONResponse({"error": "Empty request"}, status_code=400)

        logger.info(f"📥 DYNOPAY_{webhook_type.upper()}_WEBHOOK: Received: {webhook_data}")

        # Extract reference_id from meta_data or customer_reference
        meta_data = webhook_data.get("meta_data", {})
        if isinstance(meta_data, str):
            try:
                meta_data = json.loads(meta_data)
            except Exception:
                meta_data = {}
        reference_id = (
            meta_data.get("refId")
            or webhook_data.get("customer_reference")
            or webhook_data.get("reference_id")
            or ""
        )

        if not reference_id:
            # payment.pending webhooks may not include meta_data/reference yet — acknowledge and skip
            event = webhook_data.get("event", "")
            if event == "payment.pending":
                logger.info(f"📥 DYNOPAY_{webhook_type.upper()}: Pending payment acknowledged (no reference yet)")
                return JSONResponse({"status": "success", "message": "Pending payment acknowledged"})
            logger.error(f"❌ DYNOPAY_{webhook_type.upper()}: No reference_id found in webhook payload")
            return JSONResponse({"error": "Missing reference_id"}, status_code=400)

        # Normalize and process
        normalized = _normalize_dynopay_payload(webhook_data, reference_id)

        if not normalized["user_id"]:
            # Try extracting user_id from meta_data directly
            user_id_str = meta_data.get("user_id")
            if user_id_str:
                try:
                    normalized["user_id"] = int(user_id_str)
                except (ValueError, TypeError):
                    pass

        if not normalized["user_id"]:
            logger.error(f"❌ DYNOPAY_{webhook_type.upper()}: Could not determine user_id for reference {reference_id}")
            return JSONResponse({"error": "Cannot determine user"}, status_code=400)

        result = _trigger_immediate_processing(
            provider="dynopay",
            txid=normalized["txid"],
            user_id=normalized["user_id"],
            amount=normalized["amount"],
            coin=normalized["coin"],
            confirmed=normalized["is_confirmed"],
            order_id=normalized["reference_id"],
            raw_data=normalized["raw_payload"],
        )

        if result["success"]:
            return JSONResponse({"status": "success", "message": result.get("message", "Processed")})
        else:
            return JSONResponse({"status": "received", "message": result.get("message", "Will retry")})

    except Exception as e:
        logger.error(f"❌ DYNOPAY_{webhook_type.upper()}_ERROR: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


async def handle_dynopay_wallet_webhook(request: Request):
    """Handle DynoPay wallet deposit webhooks."""
    return await _handle_dynopay_generic_webhook(request, "wallet")


async def handle_dynopay_escrow_webhook(request: Request):
    """Handle DynoPay escrow payment webhooks - updates escrow status instead of crediting wallet."""
    try:
        body = await request.body()
        query_params = dict(request.query_params)

        if not body and query_params:
            webhook_data = query_params
        elif body:
            webhook_data = json.loads(body)
        else:
            return JSONResponse({"error": "Empty request"}, status_code=400)

        logger.info(f"📥 DYNOPAY_ESCROW_WEBHOOK: Received: {webhook_data}")

        # Extract reference_id from meta_data or customer_reference
        meta_data = webhook_data.get("meta_data", {})
        if isinstance(meta_data, str):
            try:
                meta_data = json.loads(meta_data)
            except Exception:
                meta_data = {}
        reference_id = (
            meta_data.get("refId")
            or webhook_data.get("customer_reference")
            or webhook_data.get("reference_id")
            or ""
        )

        if not reference_id:
            event = webhook_data.get("event", "")
            if event == "payment.pending":
                logger.info("📥 DYNOPAY_ESCROW: Pending payment acknowledged (no reference yet)")
                return JSONResponse({"status": "success", "message": "Pending payment acknowledged"})
            logger.error("❌ DYNOPAY_ESCROW: No reference_id found in webhook payload")
            return JSONResponse({"error": "Missing reference_id"}, status_code=400)

        # Normalize
        normalized = _normalize_dynopay_payload(webhook_data, reference_id)
        
        if not normalized["is_confirmed"]:
            logger.info(f"📥 DYNOPAY_ESCROW: Unconfirmed payment for {reference_id}, acknowledging")
            return JSONResponse({"status": "success", "message": "Unconfirmed payment acknowledged"})

        # Process escrow payment: update escrow status to funded
        try:
            from models import Escrow, CryptoDeposit
            
            txid = normalized["txid"]
            usd_amount = normalized["amount"]
            escrow_id_ref = normalized["reference_id"]
            
            with SessionManager.get_session() as session:
                # Check idempotency - already processed?
                existing_deposit = session.execute(
                    select(CryptoDeposit).where(
                        and_(CryptoDeposit.txid == txid, CryptoDeposit.provider == "dynopay")
                    )
                ).scalar_one_or_none()
                
                if existing_deposit and str(existing_deposit.status) == "credited":
                    logger.info(f"✅ ESCROW_ALREADY_PROCESSED: {txid}")
                    return JSONResponse({"status": "success", "message": "Already processed"})
                
                # Find escrow by utid or escrow_id
                escrow = session.execute(
                    select(Escrow).where(
                        or_(Escrow.utid == escrow_id_ref, Escrow.escrow_id == escrow_id_ref)
                    )
                ).scalar_one_or_none()
                
                if not escrow:
                    logger.error(f"❌ ESCROW_NOT_FOUND: {escrow_id_ref}")
                    return JSONResponse({"error": f"Escrow {escrow_id_ref} not found"}, status_code=404)
                
                # Record the crypto deposit
                tx_ref = webhook_data.get("transaction_reference", "")
                address_in = (
                    webhook_data.get("address")
                    or tx_ref
                    or f"dynopay-{txid}"
                )
                
                if not existing_deposit:
                    deposit = CryptoDeposit(
                        provider="dynopay",
                        txid=txid,
                        order_id=escrow_id_ref,
                        address_in=address_in,
                        address_out="",
                        user_id=escrow.buyer_id,
                        coin=normalized["coin"],
                        amount=normalized["amount"],
                        amount_fiat=usd_amount,
                        status="credited",
                        confirmations=1,
                        credited_at=datetime.now(timezone.utc)
                    )
                    session.add(deposit)
                else:
                    existing_deposit.status = "credited"
                    existing_deposit.credited_at = datetime.now(timezone.utc)
                
                # Update escrow status to payment_confirmed
                old_status = escrow.status
                escrow.status = "payment_confirmed"
                escrow.deposit_tx_hash = tx_ref or txid
                escrow.payment_confirmed_at = datetime.now(timezone.utc)
                escrow.updated_at = datetime.now(timezone.utc)
                
                # Create transaction record
                transaction = Transaction(
                    user_id=escrow.buyer_id,
                    transaction_type="escrow_payment",
                    amount=usd_amount,
                    currency="USD",
                    status="completed",
                    transaction_id=f"ESC-{txid[:20]}-{escrow.buyer_id}"[:36],
                    description=f"Escrow payment for {escrow.escrow_id}",
                    blockchain_tx_hash=txid,
                    provider="dynopay",
                    escrow_id=escrow.id,
                )
                session.add(transaction)
                
                session.commit()
                
                logger.info(
                    f"✅ ESCROW_PAYMENT_CONFIRMED: {escrow.escrow_id} (utid={escrow.utid}) - "
                    f"${usd_amount} from {normalized['coin']}, status: {old_status} → payment_confirmed, "
                    f"tx: {txid}"
                )
                
            return JSONResponse({"status": "success", "message": f"Escrow {escrow_id_ref} payment confirmed"})
            
        except Exception as e:
            logger.error(f"❌ ESCROW_PROCESS_ERROR: {e}", exc_info=True)
            return JSONResponse({"error": str(e)}, status_code=500)

    except Exception as e:
        logger.error(f"❌ DYNOPAY_ESCROW_ERROR: {e}", exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


async def handle_dynopay_exchange_webhook(request: Request):
    """Handle DynoPay exchange payment webhooks."""
    return await _handle_dynopay_generic_webhook(request, "exchange")



@router.get("/dynopay/status")
async def dynopay_status_simplified():
    """Health check endpoint for simplified DynoPay integration"""
    try:
        return {
            "status": "operational",
            "service": "DynoPay Webhook Handler (Simplified Architecture)",
            "version": "1.0-simplified",
            "features": [
                "Direct provider confirmation processing",
                "Immediate wallet crediting", 
                "Instant notifications",
                "Concurrency protection"
            ]
        }
    except Exception as e:
        logger.error(f"DynoPay status check failed: {e}")
        return {
            "status": "error",
            "service": "DynoPay Webhook Handler (Simplified)",
            "error": str(e),
        }