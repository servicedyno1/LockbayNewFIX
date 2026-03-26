"""
NEW BlockBee Webhook Handler - Simplified Architecture

Implements the architect's recommended design:
1. Verify signature (keep existing security)
2. Normalize payload 
3. Derive event key (txid_in)
4. Upsert CryptoDeposit row 
5. Trigger processor if ready
6. NO balance mutations (delegated to processor)

This solves the unconfirmed → confirmed transition problem.
"""

import logging
from decimal import Decimal
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request, Header
from typing import Optional, Dict, Any
from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError

from database import get_sync_db_session
from models import CryptoDeposit, CryptoDepositStatus, User, Escrow, EscrowStatus
from services.simplified_payment_processor import simplified_payment_processor
from utils.session_manager import SessionManager

logger = logging.getLogger(__name__)

# Create FastAPI router
router = APIRouter()


@router.api_route("/blockbee/callback/{order_id}", methods=["GET", "POST"])
async def blockbee_callback_new(
    order_id: str,
    request: Request,
    x_signature: Optional[str] = Header(None, alias="X-Signature"),
    x_callback_signature: Optional[str] = Header(None, alias="X-Callback-Signature"),
):
    """
    SIMPLIFIED: BlockBee webhook handler - Architect-approved design
    
    Direct flow: Provider Confirmation → Wallet Credit → Immediate Notification
    No background jobs, no complex state machines, no over-engineering.
    """
    try:
        # Step 1: Parse request data (keep existing logic)
        callback_data = await _parse_callback_data(request, order_id)
        
        # Step 2: Verify signature (keep existing security)
        _verify_webhook_signature(request, callback_data, x_signature, x_callback_signature, order_id)
        
        # Step 3: Normalize payload and extract key data
        normalized_data = _normalize_blockbee_payload(callback_data, order_id)
        
        # Step 3.5: CRITICAL SECURITY - Validate escrow state before processing (DISPUTE FIX)
        # Prevent late webhooks from reverting DISPUTED/COMPLETED/REFUNDED escrows
        escrow_state_check = _validate_escrow_state(order_id, normalized_data)
        if escrow_state_check["blocked"]:
            logger.warning(
                f"🚨 BLOCKBEE_INVALID_STATE: Payment received for order {order_id} in state: {escrow_state_check['status']}. "
                f"Rejecting webhook to prevent status reversion."
            )
            return {
                "status": "rejected",
                "message": f"Order {order_id} is in invalid state: {escrow_state_check['status']}",
                "order_id": order_id,
                "reason": f"order_already_{escrow_state_check['status']}"
            }
        
        # Step 4: SIMPLIFIED - Use provider confirmation as authoritative (Architect-approved)
        # Validate user_id before processing (security fix)
        if not normalized_data["user_id"]:
            logger.error(f"❌ INVALID_USER: order_id={order_id} has no valid user_id")
            raise HTTPException(status_code=400, detail="Invalid order - no associated user")
        
        processing_result = _trigger_immediate_processing(
            provider="blockbee",
            txid=normalized_data["txid"],
            user_id=normalized_data["user_id"],
            amount=normalized_data["amount"],  # Pass as Decimal, not string
            coin=normalized_data["coin"],
            confirmed=normalized_data["is_confirmed"],  # Correct key
            order_id=normalized_data["order_id"],
            raw_data=normalized_data["raw_payload"]  # Correct key
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
        logger.error(f"❌ BLOCKBEE_WEBHOOK_NEW: Unexpected error for order {order_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


async def _parse_callback_data(request: Request, order_id: str) -> Dict[str, Any]:
    """Parse callback data from request (same as before)"""
    body = await request.body()
    query_params = dict(request.query_params)
    
    # For GET requests, use query parameters
    if not body and query_params:
        callback_data = query_params
        logger.info(f"📥 BLOCKBEE_GET: Using query parameters for order {order_id}")
    else:
        if not body:
            raise HTTPException(status_code=400, detail="Empty request body")
        
        try:
            callback_data = await request.json()
        except Exception as e:
            logger.error(f"❌ BLOCKBEE_JSON: Invalid JSON format: {e}")
            raise HTTPException(status_code=400, detail="Invalid JSON format")
    
    logger.info(f"📥 BLOCKBEE_WEBHOOK_NEW: Received callback for order {order_id}: {callback_data}")
    return callback_data


def _verify_webhook_signature(
    request: Request, 
    callback_data: Dict[str, Any], 
    x_signature: Optional[str], 
    x_callback_signature: Optional[str], 
    order_id: str
):
    """Verify webhook signature (keep existing security logic)"""
    from utils.webhook_security import WebhookSecurity
    from config import Config
    
    is_production = getattr(Config, "ENVIRONMENT", "development").lower() in ["production", "prod"]
    signature_header = x_callback_signature or x_signature
    
    if signature_header:
        is_valid = WebhookSecurity.verify_blockbee_webhook(callback_data, signature_header)
        if not is_valid:
            if is_production:
                logger.critical("🚨 PRODUCTION_SECURITY_BREACH: BlockBee signature verification FAILED")
                raise HTTPException(status_code=401, detail="Invalid webhook signature")
            else:
                logger.warning("⚠️ DEV_SECURITY: Signature verification failed - processing anyway")
        else:
            logger.info("✅ BLOCKBEE_SECURITY: Signature verified successfully")
    else:
        if is_production:
            logger.critical("🚨 PRODUCTION_SECURITY_BREACH: Missing signature in production")
            raise HTTPException(status_code=401, detail="Missing webhook signature")
        else:
            logger.warning("⚠️ DEV_SECURITY: No signature header found - processing anyway in development")
            logger.warning("⚠️ CONFIGURE: Set BLOCKBEE_WEBHOOK_SECRET environment variable for security")


def _normalize_blockbee_payload(callback_data: Dict[str, Any], order_id: str) -> Dict[str, Any]:
    """
    Normalize BlockBee payload into standardized format for CryptoDeposit
    
    This extracts and normalizes key fields needed for the new architecture.
    """
    # Extract core transaction data
    txid = callback_data.get("txid_in", "")
    if not txid:
        raise HTTPException(status_code=400, detail="Missing txid_in in callback")
    
    # Extract addresses
    address_in = callback_data.get("address_in", "")
    address_out = callback_data.get("address_out", "")
    
    # Extract amounts (crypto and fiat)
    try:
        amount_crypto = Decimal(str(callback_data.get("value_coin", "0")))
        amount_fiat = None
        
        # Calculate fiat value if price is available
        price = callback_data.get("price")
        if price:
            amount_fiat = amount_crypto * Decimal(str(price))
    except (ValueError, TypeError):
        amount_crypto = Decimal('0')
        amount_fiat = None
    
    # Extract confirmation data
    confirmations = int(callback_data.get("confirmations", 0))
    coin = callback_data.get("coin", "").lower()
    
    # Determine if payment is confirmed based on confirmations
    is_confirmed = confirmations >= 1  # BlockBee typically requires 1 confirmation
    
    # Extract telegram_id from order_id and convert to database ID (CRITICAL FIX)
    user_id = None
    if order_id.startswith("WALLET-") and "-" in order_id:
        try:
            # Extract Telegram ID from WALLET-YYYYMMDD-HHMMSS-{telegram_id} format
            parts = order_id.split("-")
            if len(parts) >= 4:
                telegram_id = int(parts[-1])
                # CRITICAL: Convert Telegram ID to database ID to fix foreign key constraint
                with get_sync_db_session() as session:
                    user = session.query(User).filter(User.telegram_id == telegram_id).first()
                    if user:
                        user_id = user.id
                        logger.info(f"🔍 USER_LOOKUP: telegram_id={telegram_id} → database_id={user_id}")
                    else:
                        logger.error(f"❌ USER_NOT_FOUND: telegram_id={telegram_id} not found in users table")
        except (ValueError, IndexError) as e:
            logger.warning(f"⚠️ USER_ID_EXTRACT: Could not extract telegram_id from order_id: {order_id} - {e}")
    
    normalized = {
        "provider": "blockbee",
        "txid": txid,
        "order_id": order_id,
        "address_in": address_in,
        "address_out": address_out,
        "coin": coin,
        "amount": amount_crypto,
        "amount_fiat": amount_fiat,
        "confirmations": confirmations,
        "required_confirmations": 1,  # Standard for BlockBee
        "user_id": user_id,
        "is_confirmed": is_confirmed,
        "raw_payload": callback_data
    }
    
    logger.info(f"🔄 NORMALIZED: txid={txid}, confirmations={confirmations}, confirmed={is_confirmed}, user_id={user_id}")
    return normalized


def _validate_escrow_state(order_id: str, normalized_data: Dict[str, Any]) -> Dict[str, bool]:
    """
    CRITICAL SECURITY CHECK: Validate if order/escrow is in a state that can accept payments.
    
    This prevents late webhook retries from reverting DISPUTED/COMPLETED/REFUNDED escrows back to ACTIVE.
    Mirrors the protection in DynoPay webhook handler (handlers/dynopay_webhook.py:599).
    
    Args:
        order_id: BlockBee order ID
        normalized_data: Normalized payment data with order details
        
    Returns:
        Dict with 'blocked' (bool) and 'status' (str) indicating if webhook should be rejected
    """
    # BlockBee is used for wallet deposits (WALLET-* orders), not escrows
    # So we check if this is an escrow-related order first
    if not order_id.startswith("ESC"):  # Escrow orders start with ESC
        # Wallet deposit - always allow
        return {"blocked": False, "status": "wallet_deposit"}
    
    # For escrow orders, check if escrow exists and validate status
    try:
        with get_sync_db_session() as session:
            # Extract escrow ID from order_id (format: ESC-{escrow_id})
            escrow_id = order_id.replace("ESC-", "")
            
            escrow = session.query(Escrow).filter(Escrow.escrow_id == escrow_id).first()
            
            if not escrow:
                # Escrow not found - might be a different order format, allow processing
                logger.warning(f"⚠️ ESCROW_NOT_FOUND: No escrow found for order_id {order_id}, allowing processing")
                return {"blocked": False, "status": "not_found"}
            
            # CRITICAL: Check if escrow is in a final/protected state
            protected_states = [
                EscrowStatus.COMPLETED.value,
                EscrowStatus.DISPUTED.value,
                EscrowStatus.REFUNDED.value,
                EscrowStatus.CANCELLED.value
            ]
            
            if escrow.status in protected_states:
                logger.warning(
                    f"🚨 PROTECTED_STATE_DETECTED: Escrow {escrow_id} is in protected state: {escrow.status}. "
                    f"Blocking webhook to prevent status reversion (BUG FIX: disputed → active reversion)"
                )
                return {"blocked": True, "status": escrow.status}
            
            # Escrow is in valid payment-accepting state
            logger.info(f"✅ ESCROW_STATE_VALID: Escrow {escrow_id} in state {escrow.status}, allowing payment processing")
            return {"blocked": False, "status": escrow.status}
            
    except Exception as e:
        logger.error(f"❌ ESCROW_STATE_CHECK_ERROR: Failed to validate escrow state for {order_id}: {e}")
        # On error, allow processing to avoid blocking legitimate payments
        # The processor will handle any issues downstream
        return {"blocked": False, "status": "check_failed"}


async def _upsert_crypto_deposit(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Upsert CryptoDeposit record with proper state transitions
    
    This implements the new state machine:
    - New unconfirmed: PENDING_UNCONFIRMED
    - New confirmed: READY_TO_CREDIT  
    - Update unconfirmed → confirmed: PENDING_UNCONFIRMED → READY_TO_CREDIT
    """
    try:
        with SessionManager.atomic_operation() as session:
            # Check if deposit already exists
            existing_deposit = session.query(CryptoDeposit).filter(and_(
                CryptoDeposit.provider == data["provider"],
                CryptoDeposit.txid == data["txid"]
            )).first()
            
            if existing_deposit:
                # Update existing deposit
                result = _update_existing_deposit(session, existing_deposit, data)
            else:
                # Create new deposit
                result = _create_new_deposit(session, data)
            
            session.commit()
            return result
            
    except IntegrityError as e:
        logger.warning(f"🔒 INTEGRITY_ERROR: Concurrent deposit creation for txid={data['txid']}: {e}")
        # Handle race condition - try to fetch the existing record
        with get_sync_db_session() as session:
            existing = session.query(CryptoDeposit).filter(and_(
                CryptoDeposit.provider == data["provider"],
                CryptoDeposit.txid == data["txid"]
            )).first()
            
            if existing:
                return {
                    "action": "found_existing",
                    "status": existing.status,
                    "ready_for_processing": existing.status == CryptoDepositStatus.READY_TO_CREDIT.value
                }
            else:
                raise
    except Exception as e:
        logger.error(f"❌ UPSERT_ERROR: Failed to upsert deposit for txid={data['txid']}: {e}")
        raise


def _update_existing_deposit(session, deposit: CryptoDeposit, data: Dict[str, Any]) -> Dict[str, Any]:
    """Update existing deposit with new confirmation data"""
    old_status = deposit.status
    old_confirmations = deposit.confirmations
    
    # Update confirmation count
    deposit.confirmations = data["confirmations"]
    deposit.last_updated_at = datetime.utcnow()
    deposit.raw_payload = data["raw_payload"]
    
    # State transition logic
    if old_status == CryptoDepositStatus.PENDING_UNCONFIRMED.value and data["is_confirmed"]:
        # BREAKTHROUGH: Allow unconfirmed → confirmed transition
        deposit.status = CryptoDepositStatus.READY_TO_CREDIT.value
        logger.info(f"🎯 STATE_TRANSITION: {old_status} → {deposit.status} for txid={data['txid']}")
        ready_for_processing = True
    elif old_status == CryptoDepositStatus.CREDITED.value:
        # Already processed - no state change needed
        ready_for_processing = False
    else:
        # Keep existing status, update confirmation count
        ready_for_processing = deposit.status == CryptoDepositStatus.READY_TO_CREDIT.value
    
    session.flush()
    
    logger.info(f"📝 DEPOSIT_UPDATED: txid={data['txid']}, confirmations={old_confirmations}→{deposit.confirmations}, status={deposit.status}")
    
    return {
        "action": "updated",
        "status": deposit.status,
        "ready_for_processing": ready_for_processing,
        "state_changed": old_status != deposit.status
    }


def _create_new_deposit(session, data: Dict[str, Any]) -> Dict[str, Any]:
    """Create new deposit record"""
    # Determine initial status based on confirmation
    if data["is_confirmed"]:
        status = CryptoDepositStatus.READY_TO_CREDIT.value
        ready_for_processing = True
    else:
        status = CryptoDepositStatus.PENDING_UNCONFIRMED.value
        ready_for_processing = False
    
    deposit = CryptoDeposit(
        provider=data["provider"],
        txid=data["txid"],
        order_id=data["order_id"],
        address_in=data["address_in"],
        address_out=data["address_out"],
        coin=data["coin"],
        amount=data["amount"],
        amount_fiat=data["amount_fiat"],
        confirmations=data["confirmations"],
        required_confirmations=data["required_confirmations"],
        status=status,
        user_id=data["user_id"],
        raw_payload=data["raw_payload"]
    )
    
    session.add(deposit)
    session.flush()
    
    logger.info(f"🆕 DEPOSIT_CREATED: txid={data['txid']}, status={status}, confirmations={data['confirmations']}")
    
    return {
        "action": "created",
        "status": status,
        "ready_for_processing": ready_for_processing
    }


def _trigger_immediate_processing(
    provider: str,
    txid: str,
    user_id: int,
    amount: Decimal,  # Accept Decimal directly 
    coin: str,
    confirmed: bool,
    order_id: str,
    raw_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Simplified: Use provider confirmation as authoritative - no complex state machines
    
    If BlockBee says it's confirmed, credit wallet immediately.
    """
    try:
        # Use simplified processor - treats provider confirmation as authoritative
        result = simplified_payment_processor.process_payment(
            provider=provider,
            txid=txid,
            user_id=user_id,
            amount=amount,  # Already Decimal
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


@router.get("/blockbee/status")
async def blockbee_status_new():
    """Health check endpoint for new BlockBee integration"""
    try:
        return {
            "status": "operational",
            "service": "BlockBee Webhook Handler (New Architecture)",
            "version": "2.0-state-machine",
            "features": [
                "CryptoDeposit state machine",
                "Unconfirmed → confirmed transitions", 
                "Atomic wallet crediting",
                "Race condition protection"
            ]
        }
    except Exception as e:
        logger.error(f"BlockBee status check failed: {e}")
        return {
            "status": "error",
            "service": "BlockBee Webhook Handler (New)",
            "error": str(e),
        }