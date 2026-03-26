"""
Unified Escrow Orchestrator Service
Eliminates duplicate escrow creation attempts by providing a single, idempotent entry point
for all escrow creation operations across multiple handlers.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from dataclasses import dataclass
from enum import Enum
from decimal import Decimal, InvalidOperation as DecimalInvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.exc import IntegrityError
from database import async_managed_session
from models import User, Escrow, EscrowStatus, IdempotencyKey, PaymentAddress
from utils.universal_id_generator import UniversalIDGenerator

logger = logging.getLogger(__name__)


class EscrowCreationResult(Enum):
    """Result types for escrow creation attempts"""
    SUCCESS = "success"
    DUPLICATE_PREVENTED = "duplicate_prevented"
    VALIDATION_FAILED = "validation_failed" 
    USER_NOT_FOUND = "user_not_found"
    ERROR = "error"


@dataclass
class EscrowCreationRequest:
    """Request model for escrow creation"""
    user_id: int
    telegram_id: str
    seller_identifier: str
    seller_type: str  # 'username', 'email', 'phone'
    amount: Decimal
    currency: str
    description: Optional[str] = None
    expires_in_minutes: int = 15
    # Extended fields for complex escrow creation
    fee_amount: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None
    fee_split_option: str = 'buyer_pays'  # 'buyer_pays', 'seller_pays', 'split'
    payment_method: Optional[str] = None
    delivery_hours: Optional[int] = None  # Hours for delivery - stored in pricing_snapshot
    delivery_deadline: Optional[datetime] = None  # DEPRECATED: Set on payment confirmation
    auto_release_at: Optional[datetime] = None  # DEPRECATED: Set on payment confirmation
    seller_id: Optional[int] = None
    seller_contact_value: Optional[str] = None
    seller_contact_display: Optional[str] = None
    escrow_id: Optional[str] = None


@dataclass
class EscrowCreationResponse:
    """Response model for escrow creation"""
    result: EscrowCreationResult
    escrow_id: Optional[str] = None
    escrow_utid: Optional[str] = None
    existing_escrow_id: Optional[str] = None
    message: str = ""
    errors: Optional[List[str]] = None
    deposit_address: Optional[str] = None  # Payment address if crypto escrow
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class EscrowOrchestrator:
    """
    Unified service for escrow creation with database-backed idempotency protection.
    Prevents duplicate escrow creation attempts using true distributed locking.
    """
    
    def __init__(self):
        # No in-memory locks - use database for true distributed idempotency
        self.creation_timeout = 30  # seconds
        
    async def create_secure_trade(
        self, 
        request: EscrowCreationRequest,
        idempotency_key: Optional[str] = None,
        session: Optional[AsyncSession] = None
    ) -> EscrowCreationResponse:
        """
        Create escrow with comprehensive duplicate prevention and idempotency.
        
        Args:
            request: Escrow creation parameters
            idempotency_key: Optional key for duplicate prevention (defaults to user_id)
            session: Optional existing database session
            
        Returns:
            EscrowCreationResponse with result and escrow details
        """
        # Generate request-specific idempotency key if not provided
        if idempotency_key is None:
            # Hash full request payload to make it request-specific, not buyer-wide
            import hashlib
            import json
            request_data = {
                "user_id": request.user_id,
                "seller_identifier": request.seller_identifier,
                "seller_type": request.seller_type,
                "amount": str(request.amount),
                "currency": request.currency,
                "description": request.description,
                "payment_method": request.payment_method,
                "delivery_hours": request.delivery_hours,  # Include delivery_hours for idempotency
                "expires_in_minutes": request.expires_in_minutes,
                "seller_contact_value": request.seller_contact_value,
                "seller_contact_display": request.seller_contact_display,
                "fee_amount": str(request.fee_amount) if request.fee_amount else None,
                "total_amount": str(request.total_amount) if request.total_amount else None,
            }
            request_hash = hashlib.sha256(
                json.dumps(request_data, sort_keys=True).encode()
            ).hexdigest()[:16]
            idempotency_key = f"escrow_{request_hash}"
            
        logger.info(f"🔄 ESCROW_ORCHESTRATOR: Creating escrow for user {request.telegram_id} (key: {idempotency_key})")
        
        # Use database-backed idempotency for true distributed protection
        try:
            if session is None:
                async with async_managed_session() as new_session:
                    return await self._create_with_session(request, idempotency_key, new_session)
            else:
                return await self._create_with_session(request, idempotency_key, session)
                
        except Exception as e:
            logger.error(f"❌ ESCROW_ORCHESTRATOR_ERROR: {e}")
            return EscrowCreationResponse(
                result=EscrowCreationResult.ERROR,
                message=f"Escrow creation failed: {str(e)}",
                errors=[str(e)]
            )
    
    async def _check_idempotency(self, idempotency_key: str, request: EscrowCreationRequest, session: AsyncSession) -> Optional[str]:
        """Check if this operation was already processed. Returns existing escrow_id if found."""
        try:
            # Try to create idempotency record - database unique constraint provides distributed locking
            idempotency_record = IdempotencyKey(
                operation_key=idempotency_key,
                operation_type="escrow_create",
                user_id=request.user_id,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=24)
            )
            session.add(idempotency_record)
            await session.flush()  # This will fail if key already exists
            return None  # New operation, proceed
            
        except IntegrityError:
            # Idempotency key already exists - check if operation completed
            await session.rollback()
            existing_stmt = select(IdempotencyKey).where(IdempotencyKey.operation_key == idempotency_key)
            existing_result = await session.execute(existing_stmt)
            existing_record = existing_result.scalar_one_or_none()
            
            if existing_record and existing_record.entity_id is not None:
                logger.info(f"🔒 IDEMPOTENCY_HIT: Returning existing escrow {existing_record.entity_id} for key {idempotency_key}")
                return str(existing_record.entity_id)  # Return existing escrow ID
            else:
                # CRITICAL FIX: Record exists but operation not completed - block concurrent requests
                # Instead of returning None (proceed), return special blocking ID to prevent duplicates
                logger.warning(f"🚫 IDEMPOTENCY_BLOCK: Blocking concurrent request for key {idempotency_key}")
                return "CONCURRENT_OPERATION_IN_PROGRESS"
    
    async def _create_with_session(
        self, 
        request: EscrowCreationRequest, 
        idempotency_key: str,
        session: AsyncSession
    ) -> EscrowCreationResponse:
        """Internal escrow creation with guaranteed session"""
        
        # Step 1: Validate user exists
        # Fix: Convert telegram_id string to int for database query
        telegram_id_int = int(request.telegram_id)
        user_stmt = select(User).where(User.telegram_id == telegram_id_int)
        user_result = await session.execute(user_stmt)
        user = user_result.scalar_one_or_none()
        
        if not user:
            logger.warning(f"🚫 ESCROW_ORCHESTRATOR: User {request.telegram_id} not found")
            return EscrowCreationResponse(
                result=EscrowCreationResult.USER_NOT_FOUND,
                message="User not found in system"
            )
        
        # Step 2: Database-backed idempotency check (request-specific, not buyer-wide)
        existing_escrow_id = await self._check_idempotency(idempotency_key, request, session)
        if existing_escrow_id:
            if existing_escrow_id == "CONCURRENT_OPERATION_IN_PROGRESS":
                return EscrowCreationResponse(
                    result=EscrowCreationResult.DUPLICATE_PREVENTED,
                    message="Another request with identical parameters is currently being processed. Please try again in a moment."
                )
            else:
                return EscrowCreationResponse(
                    result=EscrowCreationResult.DUPLICATE_PREVENTED,
                    existing_escrow_id=existing_escrow_id,
                    message=f"Duplicate operation prevented - existing escrow: {existing_escrow_id}"
                )
        
        # Step 3: Validate request parameters (with rollback protection for idempotency)
        validation_errors = self._validate_request(request)
        if validation_errors:
            logger.warning(f"🚫 ESCROW_ORCHESTRATOR: Validation failed for user {request.telegram_id}: {validation_errors}")
            # CRITICAL FIX: Rollback idempotency record if validation fails
            try:
                await session.rollback()
            except Exception as rollback_error:
                logger.error(f"❌ ROLLBACK_ERROR: {rollback_error}")
            return EscrowCreationResponse(
                result=EscrowCreationResult.VALIDATION_FAILED,
                message="Request validation failed",
                errors=validation_errors
            )
        
        # Step 4: Use provided escrow_id or generate new one
        # CRITICAL FIX: Check if we're updating an existing escrow
        is_payment_update = request.escrow_id is not None
        unified_id = request.escrow_id or UniversalIDGenerator.generate_escrow_id()
        escrow_id = unified_id  # Same ID for both fields to maintain consistency
        escrow_utid = unified_id  # Ensures ck_escrow_id_consistency constraint passes
        logger.info(f"🔍 ESCROW_ID_RESULT: Using escrow_id={escrow_id} (provided={request.escrow_id is not None})")
        
        # If existing escrow_id provided, load existing escrow for update
        existing_escrow = None
        if is_payment_update:
            existing_stmt = select(Escrow).where(Escrow.escrow_id == escrow_id)
            existing_result = await session.execute(existing_stmt)
            existing_escrow = existing_result.scalar_one_or_none()
            if existing_escrow:
                logger.info(f"💳 PAYMENT_UPDATE_MODE: Found existing escrow {escrow_id}, will update instead of insert")
        
        # Step 5: Calculate fee split based on fee_split_option
        from utils.fee_calculator import FeeCalculator
        
        total_fee = request.fee_amount or Decimal('0')
        buyer_fee_amount, seller_fee_amount = FeeCalculator._calculate_fee_split(
            total_fee, request.fee_split_option
        )
        
        logger.info(f"💰 FEE_SPLIT: option={request.fee_split_option}, total=${total_fee}, buyer=${buyer_fee_amount}, seller=${seller_fee_amount}")
        
        # Step 6: Build pricing snapshot with delivery_hours
        pricing_snapshot = {}
        if request.delivery_hours is not None:
            pricing_snapshot['delivery_hours'] = request.delivery_hours
            logger.info(f"⏰ PRICING_SNAPSHOT: Storing delivery_hours={request.delivery_hours} for later use on payment confirmation")
        
        # Step 7: Create escrow record
        # Ensure expires_in_minutes is an integer before using in timedelta
        expires_minutes = int(request.expires_in_minutes) if isinstance(request.expires_in_minutes, str) else request.expires_in_minutes
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)
        
        new_escrow = Escrow(
            escrow_id=escrow_id,
            utid=escrow_utid,
            buyer_id=user.id,
            seller_id=request.seller_id,
            seller_contact_type=request.seller_type,
            seller_contact_value=request.seller_contact_value,
            seller_contact_display=request.seller_contact_display,
            amount=request.amount,
            currency=request.currency,
            fee_amount=request.fee_amount,
            total_amount=request.total_amount or (request.amount + (request.fee_amount or Decimal('0'))),
            fee_split_option=request.fee_split_option,
            buyer_fee_amount=buyer_fee_amount,
            seller_fee_amount=seller_fee_amount,
            description=request.description or "",
            status=EscrowStatus.PAYMENT_PENDING.value,
            payment_method=request.payment_method,
            pricing_snapshot=pricing_snapshot if pricing_snapshot else None,
            delivery_deadline=request.delivery_deadline,  # Should be None at creation
            auto_release_at=request.auto_release_at,  # Should be None at creation
            created_at=datetime.now(timezone.utc),
            expires_at=expires_at
        )
        
        # GREENLET FIX: Generate payment address OUTSIDE session before adding escrow
        # This prevents async/sync conflicts during commit
        deposit_address = None
        crypto_currency = None  # Initialize to prevent unbound issues in except block
        if request.payment_method and request.payment_method.startswith('crypto_'):
            try:
                from services.payment_processor_manager import payment_manager
                from config import Config
                from handlers.escrow import normalize_webhook_base_url
                
                # Extract crypto currency from payment method (e.g., "crypto_BTC" -> "BTC")
                crypto_currency = request.payment_method.replace('crypto_', '').upper()
                
                # Normalize webhook URL
                base_url = normalize_webhook_base_url(Config.WEBHOOK_URL)
                provider = payment_manager.primary_provider.value
                callback_url = f"{base_url}/dynopay/escrow" if provider == 'dynopay' else f"{base_url}/blockbee/callback/{escrow_id}"
                
                # When seller pays fee, buyer only needs to send the base amount
                if request.fee_split_option == 'seller_pays':
                    crypto_amount = float(request.amount)
                elif request.fee_split_option == 'split':
                    crypto_amount = float(request.amount + (buyer_fee_amount or Decimal('0')))
                else:
                    crypto_amount = float(request.total_amount or request.amount)
                
                # Generate payment address BEFORE DB transaction
                address_data, provider_used = await payment_manager.create_payment_address(
                    currency=crypto_currency,
                    amount=crypto_amount,
                    callback_url=callback_url,
                    reference_id=escrow_id,  # Use escrow_id as reference
                    metadata={'escrow_id': escrow_id, 'utid': escrow_utid, 'amount_usd': crypto_amount}
                )
                
                if address_data.get('address'):
                    deposit_address = address_data['address']
                    logger.info(f"✅ EXTERNAL_ADDRESS_GEN: Generated {crypto_currency} address {deposit_address} for escrow {escrow_id} using {provider_used.value}")
                else:
                    logger.error(f"❌ EXTERNAL_ADDRESS_GEN: No address returned from payment provider for escrow {escrow_id}")
                    raise ValueError(f"Payment provider failed to generate address for {crypto_currency}")
                    
            except Exception as addr_error:
                logger.error(f"❌ EXTERNAL_ADDRESS_GEN_ERROR: Failed to generate payment address for escrow {escrow_id}: {addr_error}")
                await session.rollback()
                return EscrowCreationResponse(
                    result=EscrowCreationResult.ERROR,
                    message=f"Payment address generation failed: {str(addr_error)}",
                    errors=[f"Could not generate {crypto_currency} payment address"]
                )
        
        # Set address on escrow object if generated
        if deposit_address:
            new_escrow.deposit_address = deposit_address
        
        try:
            # CRITICAL FIX: UPDATE existing escrow instead of INSERT for payment updates
            if existing_escrow:
                # Update existing escrow with new payment method
                existing_escrow.payment_method = request.payment_method
                existing_escrow.deposit_address = deposit_address
                existing_escrow.fee_split_option = request.fee_split_option
                existing_escrow.buyer_fee_amount = buyer_fee_amount
                existing_escrow.seller_fee_amount = seller_fee_amount
                existing_escrow.fee_amount = request.fee_amount
                existing_escrow.total_amount = request.total_amount or (request.amount + (request.fee_amount or Decimal('0')))
                
                # Update pricing snapshot if provided
                if pricing_snapshot:
                    existing_escrow.pricing_snapshot = pricing_snapshot
                
                logger.info(f"✅ PAYMENT_UPDATE: Updated payment method to {request.payment_method} for escrow {escrow_id}")
                await session.flush()
            else:
                # INSERT new escrow for new trades
                session.add(new_escrow)
                await session.flush()  # Get the ID without committing
            
            # CRITICAL FIX: Create PaymentAddress record for crypto payments
            if deposit_address and crypto_currency:
                # Use existing_escrow.id if updating, otherwise new_escrow.id
                db_escrow_id = existing_escrow.id if existing_escrow else new_escrow.id
                
                # Check if address already exists (DynoPay may reuse addresses)
                existing_addr = await session.execute(
                    select(PaymentAddress).where(PaymentAddress.address == deposit_address)
                )
                existing_addr_record = existing_addr.scalar_one_or_none()
                
                if existing_addr_record:
                    # Update existing record for the new escrow
                    existing_addr_record.utid = escrow_utid
                    existing_addr_record.escrow_id = db_escrow_id
                    existing_addr_record.user_id = request.user_id
                    existing_addr_record.currency = crypto_currency
                    existing_addr_record.provider = provider_used.value
                    existing_addr_record.is_used = False
                    existing_addr_record.provider_data = address_data
                    logger.info(f"✅ PAYMENT_ADDRESS_UPDATED: Reused address {deposit_address} for escrow {escrow_id}")
                else:
                    payment_address_record = PaymentAddress(
                        utid=escrow_utid,
                        address=deposit_address,
                        currency=crypto_currency,
                        provider=provider_used.value,
                        user_id=request.user_id,
                        escrow_id=db_escrow_id,
                        is_used=False,
                        provider_data=address_data
                    )
                    session.add(payment_address_record)
                    logger.info(f"✅ PAYMENT_ADDRESS_SAVED: Created payment_addresses record for escrow {escrow_id}, address {deposit_address}")
            
            # Update IdempotencyKey with entity_id to prevent duplicates
            from sqlalchemy import update
            idempotency_update_stmt = update(IdempotencyKey).where(
                IdempotencyKey.operation_key == idempotency_key
            ).values(
                entity_id=escrow_id,
                success=True,
                result_data={"escrow_id": escrow_id, "escrow_utid": escrow_utid}
            )
            await session.execute(idempotency_update_stmt)
            
            # Commit the transaction to persist escrow WITH deposit_address (if crypto)
            await session.commit()
            
            logger.info(f"✅ ESCROW_ORCHESTRATOR: Created escrow {escrow_id} for user {request.telegram_id}")
            
            return EscrowCreationResponse(
                result=EscrowCreationResult.SUCCESS,
                escrow_id=escrow_id,
                escrow_utid=escrow_utid,
                message=f"Escrow {escrow_id} created successfully",
                deposit_address=deposit_address  # Use local variable, not object after commit
            )
            
        except Exception as db_error:
            logger.error(f"❌ ESCROW_ORCHESTRATOR_DB_ERROR: {db_error}")
            await session.rollback()
            return EscrowCreationResponse(
                result=EscrowCreationResult.ERROR,
                message=f"Database error: {str(db_error)}",
                errors=[str(db_error)]
            )
    
    async def _check_existing_escrows(self, user_id: int, session: AsyncSession) -> Optional[Escrow]:
        """Check for existing active escrows for the user"""
        
        # Look for escrows created in the last 30 minutes that are still active
        cutoff_time = datetime.now(timezone.utc) - timedelta(minutes=30)
        
        stmt = select(Escrow).where(
            and_(
                Escrow.buyer_id == user_id,
                Escrow.created_at >= cutoff_time,
                Escrow.status.in_([
                    EscrowStatus.PAYMENT_PENDING.value,
                    EscrowStatus.PARTIAL_PAYMENT.value,
                    EscrowStatus.PAYMENT_CONFIRMED.value
                ])
            )
        ).order_by(Escrow.created_at.desc())
        
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
    
    def _validate_request(self, request: EscrowCreationRequest) -> List[str]:
        """Validate escrow creation request parameters"""
        errors = []
        
        # Validate amount - Ensure it's converted to Decimal for comparison
        try:
            amount = Decimal(str(request.amount)) if not isinstance(request.amount, Decimal) else request.amount
            if amount <= 0:
                errors.append("Amount must be greater than zero")
            
            if amount > Decimal('100000'):  # $100k limit
                errors.append("Amount exceeds maximum limit")
        except (ValueError, TypeError, DecimalInvalidOperation):
            errors.append("Invalid amount format")
        
        # Validate currency
        supported_currencies = ['USD', 'NGN', 'BTC', 'ETH', 'LTC', 'USDT']
        if request.currency not in supported_currencies:
            errors.append(f"Currency {request.currency} not supported")
        
        # Validate seller identifier
        if not request.seller_identifier or not request.seller_identifier.strip():
            errors.append("Seller identifier is required")
        
        # Validate seller type
        if request.seller_type not in ['username', 'email', 'phone']:
            errors.append("Invalid seller contact type")
        
        # Validate expiry time - FIX: Ensure proper int conversion
        try:
            if isinstance(request.expires_in_minutes, str):
                expires_minutes = int(request.expires_in_minutes)
            elif isinstance(request.expires_in_minutes, (int, float)):
                expires_minutes = int(request.expires_in_minutes)
            else:
                raise ValueError("Invalid expiry time type")
                
            if expires_minutes < 5 or expires_minutes > 1440:
                errors.append("Expiry time must be between 5 minutes and 24 hours")
        except (ValueError, TypeError):
            errors.append("Invalid expiry time format")
        
        return errors
    
    async def cleanup_expired_locks(self):
        """Cleanup expired creation locks to prevent memory leaks"""
        # This method is no longer needed as we use database-backed idempotency
        # instead of in-memory locks
        pass


# Global singleton instance
_orchestrator_instance = None


def get_escrow_orchestrator() -> EscrowOrchestrator:
    """Get singleton escrow orchestrator instance"""
    global _orchestrator_instance
    if _orchestrator_instance is None:
        _orchestrator_instance = EscrowOrchestrator()
    return _orchestrator_instance