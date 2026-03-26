"""
Dispute Resolution Service for Atomic Admin Operations
Centralized service to ensure all dispute resolutions are properly atomic
"""

import logging
from typing import Optional, NamedTuple
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from sqlalchemy.orm import Session

from models import Dispute, User, Escrow, EscrowStatus, DisputeStatus
from services.crypto import CryptoServiceAtomic
from utils.atomic_transactions import locked_escrow_operation_async
from utils.fee_calculator import FeeCalculator
from utils.helpers import get_user_display_name
from utils.escrow_state_validator import EscrowStateValidator

logger = logging.getLogger(__name__)


class ResolutionResult(NamedTuple):
    """Result of a dispute resolution operation"""

    success: bool
    escrow_id: str
    resolution_type: str
    amount: float
    error_message: Optional[str] = None
    dispute_winner_id: Optional[int] = None
    dispute_loser_id: Optional[int] = None
    buyer_id: Optional[int] = None
    seller_id: Optional[int] = None


class DisputeResolutionService:
    """Service for atomic dispute resolution operations"""

    @classmethod
    async def resolve_refund_to_buyer(
        cls, dispute_id: int, admin_user_id: int, session: Optional[Session] = None
    ) -> ResolutionResult:
        """
        Atomically resolve dispute with full refund to buyer
        All operations happen in single transaction with proper locking
        """
        from database import async_managed_session
        from sqlalchemy import select
        
        try:
            async with async_managed_session() as tx_session:
                # Load dispute and escrow using async patterns (eagerly load escrow and related users to avoid lazy loading)
                from sqlalchemy.orm import selectinload
                dispute_stmt = (
                    select(Dispute)
                    .where(Dispute.id == dispute_id)
                    .options(
                        selectinload(Dispute.escrow).selectinload(Escrow.buyer),
                        selectinload(Dispute.escrow).selectinload(Escrow.seller)
                    )
                )
                dispute_result = await tx_session.execute(dispute_stmt)
                dispute = dispute_result.scalar_one_or_none()
                if not dispute:
                    return ResolutionResult(
                        success=False,
                        escrow_id="",
                        resolution_type="refund",
                        amount=0.0,
                        error_message="Dispute not found",
                    )

                escrow = dispute.escrow

                # Validate status within transaction
                if str(escrow.status) not in [
                    EscrowStatus.DISPUTED.value,
                    EscrowStatus.ACTIVE.value,
                ]:
                    return ResolutionResult(
                        success=False,
                        escrow_id=escrow.escrow_id,
                        resolution_type="refund",
                        amount=0.0,
                        error_message=f"Cannot refund escrow in {str(escrow.status).upper()} status",
                    )

                if str(dispute.status) != "open":
                    return ResolutionResult(
                        success=False,
                        escrow_id=escrow.escrow_id,
                        resolution_type="refund",
                        amount=0.0,
                        error_message=f"Dispute already resolved: {str(dispute.status).upper()}",
                    )

                # Get admin user using async patterns
                admin_user_stmt = select(User).where(User.id == admin_user_id)
                admin_user_result = await tx_session.execute(admin_user_stmt)
                admin_user = admin_user_result.scalar_one_or_none()

                # REFUND POLICY: Check if seller has accepted the trade
                # If seller never accepted, include buyer_fee in refund (fair refund policy)
                # If seller has accepted, retain platform fees (service was provided)
                seller_has_accepted = escrow.seller_accepted_at is not None
                
                if seller_has_accepted:
                    # Seller accepted: Calculate refund amount minus platform fees (original behavior)
                    refund_amount_decimal = Decimal(
                        str(
                            FeeCalculator.calculate_refund_amount(
                                escrow_amount=float(escrow.amount),
                                buyer_fee_amount=(
                                    float(escrow.buyer_fee_amount)
                                    if escrow.buyer_fee_amount
                                    else None
                                ),
                                fee_split_option=escrow.fee_split_option,
                            )
                        )
                    )
                    refund_amount = refund_amount_decimal.quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    
                    # Record platform fee retention for admin audit trail and revenue tracking
                    platform_fee = Decimal(str(escrow.buyer_fee_amount or 0)) + Decimal(
                        str(escrow.seller_fee_amount or 0)
                    )
                    
                    # Record retained platform fees as revenue
                    if platform_fee > 0:
                        from models import PlatformRevenue
                        platform_revenue = PlatformRevenue(
                            escrow_id=escrow.escrow_id,
                            fee_amount=Decimal(str(platform_fee)),
                            fee_currency="USD",
                            fee_type="dispute_resolution_fee",
                            source_transaction_id=f"dispute_{dispute_id}_refund"
                        )
                        tx_session.add(platform_revenue)
                        logger.info(
                            f"Admin dispute resolution (seller accepted) - Refunding ${refund_amount:.2f} to buyer, "
                            f"recording ${platform_fee:.2f} platform fee as revenue"
                        )
                    else:
                        logger.info(
                            f"Admin dispute resolution (seller accepted) - Refunding ${refund_amount:.2f} to buyer, no platform fees"
                        )
                else:
                    # Seller never accepted: Include buyer_fee in refund (fair policy)
                    buyer_fee = Decimal(str(escrow.buyer_fee_amount or 0))
                    refund_amount = Decimal(str(escrow.amount)) + buyer_fee
                    
                    logger.info(
                        f"Admin dispute resolution (seller never accepted) - Refunding ${refund_amount:.2f} "
                        f"to buyer (${float(escrow.amount):.2f} escrow + ${buyer_fee:.2f} buyer_fee)"
                    )

                # Get seller name for description
                (
                    get_user_display_name(escrow.seller)
                    if escrow.seller
                    else "Unknown Seller"
                )

                # Atomic escrow operation with row-level locking (async)
                async with locked_escrow_operation_async(
                    escrow.escrow_id, tx_session
                ) as locked_escrow:
                    # SECURITY FIX: Validate state transition before refund to prevent invalid transitions
                    validator = EscrowStateValidator()
                    current_status = locked_escrow.status
                    if not validator.is_valid_transition(current_status, EscrowStatus.REFUNDED.value):
                        logger.error(
                            f"🚫 DISPUTE_REFUND_BLOCKED: Invalid transition {current_status}→REFUNDED for escrow {locked_escrow.escrow_id}"
                        )
                        return ResolutionResult(
                            success=False,
                            escrow_id=locked_escrow.escrow_id,
                            resolution_type="refund",
                            amount=0.0,
                            error_message=f"Invalid state transition: {current_status}→REFUNDED (escrow may already be completed or refunded)",
                        )
                    
                    # Credit buyer wallet within same transaction
                    credit_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                        user_id=locked_escrow.buyer_id,
                        amount=refund_amount,
                        currency="USD",
                        escrow_id=locked_escrow.id,
                        transaction_type="escrow_refund",
                        description=f"↩️ Refunded • Escrow #{locked_escrow.escrow_id}",
                        session=tx_session,
                    )

                    if not credit_success:
                        raise Exception(
                            f"Failed to credit wallet for refund: ${refund_amount}"
                        )

                    # Update escrow status atomically
                    locked_escrow.status = EscrowStatus.REFUNDED.value
                    locked_escrow.released_at = datetime.utcnow()

                    # SECURITY FIX: Validate dispute status transition before resolution
                    # Prevent modifying already resolved disputes
                    current_dispute_status = dispute.status
                    if current_dispute_status == DisputeStatus.RESOLVED.value:
                        logger.error(
                            f"🚫 DISPUTE_STATUS_BLOCKED: Cannot modify already resolved dispute {dispute.id} "
                            f"(current status: {current_dispute_status})"
                        )
                        return ResolutionResult(
                            success=False,
                            escrow_id=locked_escrow.escrow_id,
                            resolution_type="refund",
                            amount=0.0,
                            error_message=f"Dispute {dispute.id} is already resolved and cannot be modified",
                        )

                    # Update dispute atomically using setattr for SQLAlchemy compatibility
                    setattr(dispute, 'status', DisputeStatus.RESOLVED.value)
                    setattr(dispute, 'resolution', "refunded_to_buyer")
                    setattr(dispute, 'resolved_at', datetime.utcnow())
                    setattr(dispute, 'admin_assigned_id', admin_user.id if admin_user else None)

                    # Cache IDs and ACTUAL refund amount before session closes
                    final_escrow_id = locked_escrow.escrow_id
                    final_buyer_id = locked_escrow.buyer_id
                    final_seller_id = locked_escrow.seller_id
                    final_refund_amount = refund_amount
                    
                    logger.info(
                        f"Successfully processed atomic refund for escrow {locked_escrow.escrow_id}, amount: ${refund_amount}"
                    )

            # Send post-completion notifications (rating prompts) - SAME PATH AS NORMAL TRADE COMPLETION
            try:
                from services.post_completion_notification_service import notify_escrow_completion
                
                notification_results = await notify_escrow_completion(
                    escrow_id=final_escrow_id,
                    completion_type='dispute_resolved',
                    amount=final_refund_amount,
                    buyer_id=final_buyer_id,
                    seller_id=final_seller_id,
                    buyer_email=None,
                    seller_email=None,
                    resolution_type='refund'
                )
                
                logger.info(f"✅ Post-dispute rating prompts sent for {final_escrow_id}: {notification_results}")
            except Exception as e:
                logger.error(f"❌ Failed to send post-dispute rating prompts for {final_escrow_id}: {e}")

            return ResolutionResult(
                success=True,
                escrow_id=final_escrow_id,
                resolution_type="refund",
                amount=float(refund_amount),
                dispute_winner_id=final_buyer_id,
                dispute_loser_id=final_seller_id,
                buyer_id=final_buyer_id,
                seller_id=final_seller_id,
            )

        except Exception as e:
            logger.error(f"Error in atomic refund resolution: {e}")
            return ResolutionResult(
                success=False,
                escrow_id="",
                resolution_type="refund",
                amount=0.0,
                error_message=str(e),
            )

    @classmethod
    async def resolve_release_to_seller(
        cls, dispute_id: int, admin_user_id: int, session: Optional[Session] = None
    ) -> ResolutionResult:
        """
        Atomically resolve dispute with full release to seller
        All operations happen in single transaction with proper locking
        """
        from database import async_managed_session
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload
        
        try:
            async with async_managed_session() as tx_session:
                # Load dispute and escrow using async patterns (eagerly load escrow and related users to avoid lazy loading)
                dispute_stmt = (
                    select(Dispute)
                    .where(Dispute.id == dispute_id)
                    .options(
                        selectinload(Dispute.escrow).selectinload(Escrow.buyer),
                        selectinload(Dispute.escrow).selectinload(Escrow.seller)
                    )
                )
                dispute_result = await tx_session.execute(dispute_stmt)
                dispute = dispute_result.scalar_one_or_none()
                if not dispute:
                    return ResolutionResult(
                        success=False,
                        escrow_id="",
                        resolution_type="release",
                        amount=0.0,
                        error_message="Dispute not found",
                    )

                escrow = dispute.escrow

                # Validate status within transaction
                if str(escrow.status) not in [
                    EscrowStatus.DISPUTED.value,
                    EscrowStatus.ACTIVE.value,
                ]:
                    return ResolutionResult(
                        success=False,
                        escrow_id=escrow.escrow_id,
                        resolution_type="release",
                        amount=0.0,
                        error_message=f"Cannot release escrow in {str(escrow.status).upper()} status",
                    )

                if str(dispute.status) != "open":
                    return ResolutionResult(
                        success=False,
                        escrow_id=escrow.escrow_id,
                        resolution_type="release",
                        amount=0.0,
                        error_message=f"Dispute already resolved: {str(dispute.status).upper()}",
                    )

                # Get admin user using async patterns
                admin_user_stmt = select(User).where(User.id == admin_user_id)
                admin_user_result = await tx_session.execute(admin_user_stmt)
                admin_user = admin_user_result.scalar_one_or_none()

                # REFUND POLICY: For release to seller, seller must have accepted (otherwise no dispute would exist)
                # Platform fees are always retained when releasing to seller (service was provided)
                seller_has_accepted = escrow.seller_accepted_at is not None
                
                # Calculate release amount minus seller fees (platform always retains fees on release)
                release_amount_decimal = Decimal(
                    str(
                        FeeCalculator.calculate_release_amount(
                            escrow_amount=float(escrow.amount),
                            seller_fee_amount=(
                                float(escrow.seller_fee_amount)
                                if escrow.seller_fee_amount
                                else None
                            ),
                        )
                    )
                )
                release_amount = release_amount_decimal.quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )

                # Record platform fee retention for admin audit trail and revenue tracking
                platform_fee = Decimal(str(escrow.buyer_fee_amount or 0)) + Decimal(
                    str(escrow.seller_fee_amount or 0)
                )
                
                # Record retained platform fees as revenue
                if platform_fee > 0:
                    from models import PlatformRevenue
                    platform_revenue = PlatformRevenue(
                        escrow_id=escrow.escrow_id,
                        fee_amount=Decimal(str(platform_fee)),
                        fee_currency="USD",
                        fee_type="dispute_resolution_fee",
                        source_transaction_id=f"dispute_{dispute_id}_release"
                    )
                    tx_session.add(platform_revenue)
                    acceptance_status = "seller accepted" if seller_has_accepted else "seller never accepted (unusual)"
                    logger.info(
                        f"Admin dispute resolution ({acceptance_status}) - Releasing ${release_amount:.2f} to seller, "
                        f"recording ${platform_fee:.2f} platform fee as revenue"
                    )
                else:
                    acceptance_status = "seller accepted" if seller_has_accepted else "seller never accepted (unusual)"
                    logger.info(
                        f"Admin dispute resolution ({acceptance_status}) - Releasing ${release_amount:.2f} to seller, no platform fees"
                    )

                # Get buyer name for description
                (
                    get_user_display_name(escrow.buyer)
                    if escrow.buyer
                    else "Unknown Buyer"
                )

                # Atomic escrow operation with row-level locking (async)
                async with locked_escrow_operation_async(
                    escrow.escrow_id, tx_session
                ) as locked_escrow:
                    # SECURITY FIX: Validate state transition before release to prevent invalid transitions
                    validator = EscrowStateValidator()
                    current_status = locked_escrow.status
                    if not validator.is_valid_transition(current_status, EscrowStatus.COMPLETED.value):
                        logger.error(
                            f"🚫 DISPUTE_RELEASE_BLOCKED: Invalid transition {current_status}→COMPLETED for escrow {locked_escrow.escrow_id}"
                        )
                        return ResolutionResult(
                            success=False,
                            escrow_id=locked_escrow.escrow_id,
                            resolution_type="release",
                            amount=0.0,
                            error_message=f"Invalid state transition: {current_status}→COMPLETED (escrow may already be completed or refunded)",
                        )
                    
                    # Credit seller wallet within same transaction
                    credit_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                        user_id=locked_escrow.seller_id,
                        amount=release_amount,
                        currency="USD",
                        escrow_id=locked_escrow.id,
                        transaction_type="escrow_release",
                        description=f"🏆 Won Dispute • Escrow #{locked_escrow.escrow_id}",
                        session=tx_session,
                    )

                    if not credit_success:
                        raise Exception(
                            f"Failed to credit wallet for release: ${release_amount}"
                        )

                    # Update escrow status atomically
                    locked_escrow.status = EscrowStatus.COMPLETED.value
                    locked_escrow.released_at = datetime.utcnow()

                    # SECURITY FIX: Validate dispute status transition before resolution
                    # Prevent modifying already resolved disputes
                    current_dispute_status = dispute.status
                    if current_dispute_status == DisputeStatus.RESOLVED.value:
                        logger.error(
                            f"🚫 DISPUTE_STATUS_BLOCKED: Cannot modify already resolved dispute {dispute.id} "
                            f"(current status: {current_dispute_status})"
                        )
                        return ResolutionResult(
                            success=False,
                            escrow_id=locked_escrow.escrow_id,
                            resolution_type="release",
                            amount=0.0,
                            error_message=f"Dispute {dispute.id} is already resolved and cannot be modified",
                        )

                    # Update dispute atomically using setattr for SQLAlchemy compatibility
                    setattr(dispute, 'status', DisputeStatus.RESOLVED.value)
                    setattr(dispute, 'resolution', "released_to_seller")
                    setattr(dispute, 'resolved_at', datetime.utcnow())
                    setattr(dispute, 'admin_assigned_id', admin_user.id if admin_user else None)

                    # Cache IDs and ACTUAL release amount before session closes
                    final_escrow_id = locked_escrow.escrow_id
                    final_buyer_id = locked_escrow.buyer_id
                    final_seller_id = locked_escrow.seller_id
                    final_release_amount = release_amount
                    
                    logger.info(
                        f"Successfully processed atomic release for escrow {locked_escrow.escrow_id}, amount: ${release_amount}"
                    )

            # Send post-completion notifications (rating prompts) - SAME PATH AS NORMAL TRADE COMPLETION
            try:
                from services.post_completion_notification_service import notify_escrow_completion
                
                notification_results = await notify_escrow_completion(
                    escrow_id=final_escrow_id,
                    completion_type='dispute_resolved',
                    amount=final_release_amount,
                    buyer_id=final_buyer_id,
                    seller_id=final_seller_id,
                    buyer_email=None,
                    seller_email=None,
                    resolution_type='release'
                )
                
                logger.info(f"✅ Post-dispute rating prompts sent for {final_escrow_id}: {notification_results}")
            except Exception as e:
                logger.error(f"❌ Failed to send post-dispute rating prompts for {final_escrow_id}: {e}")

            return ResolutionResult(
                success=True,
                escrow_id=final_escrow_id,
                resolution_type="release",
                amount=float(release_amount),
                dispute_winner_id=final_seller_id,
                dispute_loser_id=final_buyer_id,
                buyer_id=final_buyer_id,
                seller_id=final_seller_id,
            )

        except Exception as e:
            logger.error(f"Error in atomic release resolution: {e}")
            return ResolutionResult(
                success=False,
                escrow_id="",
                resolution_type="release",
                amount=0.0,
                error_message=str(e),
            )

    @classmethod
    async def resolve_custom_split(
        cls,
        dispute_id: int,
        buyer_percent: int,
        seller_percent: int,
        admin_user_id: int,
        session: Optional[Session] = None,
    ) -> ResolutionResult:
        """
        Atomically resolve dispute with custom split between buyer and seller
        All operations happen in single transaction with proper locking and deterministic order
        """
        from database import async_managed_session
        from sqlalchemy import select
        
        try:
            # Validate percentages
            if buyer_percent + seller_percent != 100:
                return ResolutionResult(
                    success=False,
                    escrow_id="",
                    resolution_type="split",
                    amount=0.0,
                    error_message=f"Invalid split: {buyer_percent}% + {seller_percent}% ≠ 100%",
                )

            async with async_managed_session() as tx_session:
                # Load dispute and escrow using async patterns (eagerly load escrow and related users to avoid lazy loading)
                from sqlalchemy.orm import selectinload
                dispute_stmt = (
                    select(Dispute)
                    .where(Dispute.id == dispute_id)
                    .options(
                        selectinload(Dispute.escrow).selectinload(Escrow.buyer),
                        selectinload(Dispute.escrow).selectinload(Escrow.seller)
                    )
                )
                dispute_result = await tx_session.execute(dispute_stmt)
                dispute = dispute_result.scalar_one_or_none()
                if not dispute:
                    return ResolutionResult(
                        success=False,
                        escrow_id="",
                        resolution_type="split",
                        amount=0.0,
                        error_message="Dispute not found",
                    )

                escrow = dispute.escrow

                # Validate status within transaction
                if str(escrow.status) not in [
                    EscrowStatus.DISPUTED.value,
                    EscrowStatus.ACTIVE.value,
                ]:
                    return ResolutionResult(
                        success=False,
                        escrow_id=escrow.escrow_id,
                        resolution_type="split",
                        amount=0.0,
                        error_message=f"Cannot split escrow in {str(escrow.status).upper()} status",
                    )

                if str(dispute.status) != "open":
                    return ResolutionResult(
                        success=False,
                        escrow_id=escrow.escrow_id,
                        resolution_type="split",
                        amount=0.0,
                        error_message=f"Dispute already resolved: {str(dispute.status).upper()}",
                    )

                # Get admin user using async patterns
                admin_user_stmt = select(User).where(User.id == admin_user_id)
                admin_user_result = await tx_session.execute(admin_user_stmt)
                admin_user = admin_user_result.scalar_one_or_none()

                # REFUND POLICY: Check if seller has accepted the trade
                # If seller never accepted, include buyer_fee in the amount to split (fair policy)
                # If seller has accepted, retain platform fees (service was provided)
                seller_has_accepted = escrow.seller_accepted_at is not None
                
                buyer_fee = Decimal(str(escrow.buyer_fee_amount or 0))
                seller_fee = Decimal(str(escrow.seller_fee_amount or 0))
                platform_fee = buyer_fee + seller_fee
                
                if seller_has_accepted:
                    # Seller accepted: Split only the escrow amount, platform retains fees
                    amount_to_split = Decimal(str(escrow.amount))
                    
                    # Log platform fee retention for admin audit trail
                    logger.info(
                        f"Admin dispute split (seller accepted) - Platform fee ${float(platform_fee):.2f} retained, "
                        f"splitting ${float(amount_to_split):.2f} between parties ({buyer_percent}/{seller_percent})"
                    )
                    
                    # Record retained platform fees as revenue
                    if platform_fee > 0:
                        from models import PlatformRevenue
                        platform_revenue = PlatformRevenue(
                            escrow_id=escrow.escrow_id,
                            fee_amount=platform_fee,
                            fee_currency="USD",
                            fee_type="dispute_resolution_fee",
                            source_transaction_id=f"dispute_{dispute_id}_split"
                        )
                        tx_session.add(platform_revenue)
                else:
                    # Seller never accepted: Include buyer_fee in split amount (fair policy)
                    amount_to_split = Decimal(str(escrow.amount)) + buyer_fee
                    
                    logger.info(
                        f"Admin dispute split (seller never accepted) - Including ${float(buyer_fee):.2f} buyer_fee, "
                        f"splitting ${float(amount_to_split):.2f} total between parties ({buyer_percent}/{seller_percent})"
                    )
                
                # Calculate split amounts
                buyer_amount = (
                    amount_to_split * Decimal(str(buyer_percent)) / Decimal("100")
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                seller_amount = (
                    amount_to_split * Decimal(str(seller_percent)) / Decimal("100")
                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                # Ensure amounts sum exactly (handle rounding edge cases)
                if buyer_amount + seller_amount != amount_to_split:
                    difference = amount_to_split - (buyer_amount + seller_amount)
                    if buyer_percent >= seller_percent:
                        buyer_amount += difference
                    else:
                        seller_amount += difference

                # Get names for descriptions
                (
                    get_user_display_name(escrow.buyer)
                    if escrow.buyer
                    else "Unknown Buyer"
                )
                (
                    get_user_display_name(escrow.seller)
                    if escrow.seller
                    else "Unknown Seller"
                )

                # Atomic escrow operation with row-level locking (async)
                async with locked_escrow_operation_async(
                    escrow.escrow_id, tx_session
                ) as locked_escrow:
                    # Perform wallet credits in deterministic order (by user_id) to prevent deadlocks
                    if locked_escrow.buyer_id < locked_escrow.seller_id:
                        # Credit buyer first
                        if buyer_amount > 0:
                            buyer_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                                user_id=locked_escrow.buyer_id,
                                amount=buyer_amount,
                                currency="USD",
                                escrow_id=locked_escrow.id,
                                transaction_type="escrow_refund",
                                description=f"⚖️ Split {buyer_percent}% • Escrow #{locked_escrow.escrow_id}",
                                session=tx_session,
                            )
                            if not buyer_success:
                                raise Exception(
                                    f"Failed to credit buyer wallet: ${buyer_amount}"
                                )

                        # Credit seller second
                        if seller_amount > 0:
                            seller_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                                user_id=locked_escrow.seller_id,
                                amount=seller_amount,
                                currency="USD",
                                escrow_id=locked_escrow.id,
                                transaction_type="escrow_release",
                                description=f"⚖️ Split {seller_percent}% • Escrow #{locked_escrow.escrow_id}",
                                session=tx_session,
                            )
                            if not seller_success:
                                raise Exception(
                                    f"Failed to credit seller wallet: ${seller_amount}"
                                )
                    else:
                        # Credit seller first (deterministic order)
                        if seller_amount > 0:
                            seller_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                                user_id=locked_escrow.seller_id,
                                amount=seller_amount,
                                currency="USD",
                                escrow_id=locked_escrow.id,
                                transaction_type="escrow_release",
                                description=f"⚖️ Split {seller_percent}% • Escrow #{locked_escrow.escrow_id}",
                                session=tx_session,
                            )
                            if not seller_success:
                                raise Exception(
                                    f"Failed to credit seller wallet: ${seller_amount}"
                                )

                        # Credit buyer second
                        if buyer_amount > 0:
                            buyer_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                                user_id=locked_escrow.buyer_id,
                                amount=buyer_amount,
                                currency="USD",
                                escrow_id=locked_escrow.id,
                                transaction_type="escrow_refund",
                                description=f"⚖️ Split {buyer_percent}% • Escrow #{locked_escrow.escrow_id}",
                                session=tx_session,
                            )
                            if not buyer_success:
                                raise Exception(
                                    f"Failed to credit buyer wallet: ${buyer_amount}"
                                )

                    # Update escrow status atomically
                    locked_escrow.status = EscrowStatus.COMPLETED.value
                    locked_escrow.released_at = datetime.utcnow()

                    # SECURITY FIX: Validate dispute status transition before resolution
                    # Prevent modifying already resolved disputes
                    current_dispute_status = dispute.status
                    if current_dispute_status == DisputeStatus.RESOLVED.value:
                        logger.error(
                            f"🚫 DISPUTE_STATUS_BLOCKED: Cannot modify already resolved dispute {dispute.id} "
                            f"(current status: {current_dispute_status})"
                        )
                        return ResolutionResult(
                            success=False,
                            escrow_id=locked_escrow.escrow_id,
                            resolution_type="split",
                            amount=0.0,
                            error_message=f"Dispute {dispute.id} is already resolved and cannot be modified",
                        )

                    # Update dispute atomically using setattr for SQLAlchemy compatibility
                    setattr(dispute, 'status', DisputeStatus.RESOLVED.value)
                    setattr(dispute, 'resolution', f"split_{buyer_percent}_{seller_percent}")
                    setattr(dispute, 'resolved_at', datetime.utcnow())
                    setattr(dispute, 'admin_assigned_id', admin_user.id if admin_user else None)

                    # Cache IDs and ACTUAL split amounts before session closes
                    final_escrow_id = locked_escrow.escrow_id
                    final_buyer_id = locked_escrow.buyer_id
                    final_seller_id = locked_escrow.seller_id
                    final_total_amount = buyer_amount + seller_amount
                    
                    logger.info(
                        f"Successfully processed atomic split for escrow {locked_escrow.escrow_id}, "
                        f"buyer: ${buyer_amount} ({buyer_percent}%), seller: ${seller_amount} ({seller_percent}%)"
                    )

            # Send post-completion notifications (rating prompts) - SAME PATH AS NORMAL TRADE COMPLETION
            try:
                from services.post_completion_notification_service import notify_escrow_completion
                
                notification_results = await notify_escrow_completion(
                    escrow_id=final_escrow_id,
                    completion_type='dispute_resolved',
                    amount=final_total_amount,
                    buyer_id=final_buyer_id,
                    seller_id=final_seller_id,
                    buyer_email=None,
                    seller_email=None,
                    resolution_type=f'custom_split_{buyer_percent}_{seller_percent}'
                )
                
                logger.info(f"✅ Post-dispute rating prompts sent for {final_escrow_id}: {notification_results}")
            except Exception as e:
                logger.error(f"❌ Failed to send post-dispute rating prompts for {final_escrow_id}: {e}")

            return ResolutionResult(
                success=True,
                escrow_id=final_escrow_id,
                resolution_type="split",
                amount=float(final_total_amount),
            )

        except Exception as e:
            logger.error(f"Error in atomic split resolution: {e}")
            return ResolutionResult(
                success=False,
                escrow_id="",
                resolution_type="split",
                amount=0.0,
                error_message=str(e),
            )
