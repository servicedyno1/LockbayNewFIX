"""
Escrow Holding Verifier Service
Comprehensive verification system to ensure holding records are created properly after payment processing
with auto-recovery, detailed logging, and admin alerting
"""

import logging
from decimal import Decimal
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import func, select

from models import EscrowHolding, Escrow, Transaction, TransactionType, PlatformRevenue
from utils.atomic_transactions import atomic_transaction
from utils.financial_audit_logger import (
    financial_audit_logger,
    FinancialEventType,
    FinancialContext,
    EntityType
)

logger = logging.getLogger(__name__)


class HoldingVerificationResult:
    """Result object for holding verification operations"""
    
    def __init__(self):
        self.success = False
        self.holding_exists = False
        self.holding_created = False
        self.auto_recovered = False
        self.errors = []
        self.warnings = []
        self.holding_id = None
        self.verification_details = {}
        self.admin_alert_sent = False
    
    def add_error(self, error: str):
        """Add error to results"""
        self.errors.append(error)
        logger.error(f"HOLDING_VERIFICATION_ERROR: {error}")
    
    def add_warning(self, warning: str):
        """Add warning to results"""
        self.warnings.append(warning)
        logger.warning(f"HOLDING_VERIFICATION_WARNING: {warning}")
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of verification results"""
        return {
            "success": self.success,
            "holding_exists": self.holding_exists,
            "holding_created": self.holding_created,
            "auto_recovered": self.auto_recovered,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "holding_id": self.holding_id,
            "admin_alert_sent": self.admin_alert_sent
        }


class EscrowHoldingVerifier:
    """Comprehensive escrow holding verification service"""
    
    @classmethod
    async def verify_holding_created(
        cls,
        escrow_id: str,
        expected_amount: Decimal,
        payment_source: str = "unknown",
        tx_hash: Optional[str] = None,
        auto_recover: bool = True,
        verification_context: Optional[Dict[str, Any]] = None,
        session: Optional[Session] = None
    ) -> HoldingVerificationResult:
        """
        Comprehensive verification that holding record exists after payment processing
        
        Args:
            escrow_id: Escrow identifier to verify
            expected_amount: Expected holding amount to verify
            payment_source: Source of payment (dynopay, fincra, blockbee, manual, etc.)
            tx_hash: Transaction hash for reference
            auto_recover: Whether to auto-create missing holdings
            verification_context: Additional context for verification
        
        Returns:
            HoldingVerificationResult with detailed verification results
        """
        result = HoldingVerificationResult()
        result.verification_details = {
            "escrow_id": escrow_id,
            "expected_amount": str(expected_amount),
            "payment_source": payment_source,
            "tx_hash": tx_hash,
            "verification_timestamp": datetime.utcnow().isoformat(),
            "context": verification_context or {}
        }
        
        try:
            logger.info(
                f"🔍 HOLDING_VERIFICATION_START: Verifying holding for escrow {escrow_id} "
                f"(expected: ${expected_amount}, source: {payment_source})"
            )
            
            # RACE CONDITION FIX: Use provided session if available (async), otherwise create new one (sync)
            if session:
                # Use provided session from parent transaction (async-safe)
                escrow_result = await session.execute(
                    select(Escrow).filter(Escrow.escrow_id == escrow_id)
                )
                escrow = escrow_result.scalars().first()
                
                if not escrow:
                    result.add_error(f"Escrow {escrow_id} not found in database")
                    await cls._send_admin_alert_if_critical(result, "ESCROW_NOT_FOUND")
                    return result
                
                # Check if holding already exists (async query)
                holding_result = await session.execute(
                    select(EscrowHolding).filter(EscrowHolding.escrow_id == escrow_id)
                )
                existing_holding = holding_result.scalars().first()
                
                if existing_holding:
                    # Holding exists - verify it's correct
                    result.holding_exists = True
                    result.holding_id = existing_holding.id
                    
                    # Verify amount matches
                    amount_difference = abs(existing_holding.amount_held - expected_amount)
                    if amount_difference > Decimal("0.001"):  # Allow small floating point variance
                        result.add_warning(
                            f"Holding amount mismatch: Expected ${expected_amount}, "
                            f"Found ${existing_holding.amount_held}, Difference: ${amount_difference}"
                        )
                        
                        # AUDIT: Log holding amount mismatch (sync call - compatible with async session)
                        financial_audit_logger.log_financial_event(
                            event_type=FinancialEventType.HOLDING_AMOUNT_MISMATCH,
                            entity_type=EntityType.ESCROW_HOLDING,
                            entity_id=str(existing_holding.id),
                            user_id=escrow.buyer_id,
                            financial_context=FinancialContext(
                                amount=existing_holding.amount_held,
                                currency="USD",
                                expected_amount=expected_amount
                            ),
                            related_entities={
                                "escrow_id": escrow_id,
                                "payment_source": payment_source,
                                "tx_hash": tx_hash
                            },
                            additional_data={
                                "amount_difference": str(amount_difference),
                                "verification_context": verification_context
                            },
                            session=session
                        )
                    
                    result.success = True
                    logger.info(
                        f"✅ HOLDING_VERIFICATION_SUCCESS: Holding exists for {escrow_id} "
                        f"(ID: {existing_holding.id}, Amount: ${existing_holding.amount_held})"
                    )
                    
                else:
                    # Holding missing - this is critical!
                    result.add_warning(f"CRITICAL: No holding record found for escrow {escrow_id}")
                    
                    # AUDIT: Log missing holding detection (sync call - compatible with async session)
                    financial_audit_logger.log_financial_event(
                        event_type=FinancialEventType.ESCROW_HOLDING_MISSING,
                        entity_type=EntityType.ESCROW_HOLDING,
                        entity_id=f"missing_{escrow_id}",
                        user_id=escrow.buyer_id,
                        financial_context=FinancialContext(
                            amount=expected_amount,
                            currency="USD"
                        ),
                        related_entities={
                            "escrow_id": escrow_id,
                            "payment_source": payment_source,
                            "tx_hash": tx_hash
                        },
                        additional_data={
                            "auto_recover_enabled": auto_recover,
                            "verification_context": verification_context
                        },
                        session=session
                    )
                    
                    if auto_recover:
                        # Attempt auto-recovery (with provided async session)
                        recovery_result = await cls._create_missing_holding(
                            session, escrow, expected_amount, payment_source, tx_hash, verification_context
                        )
                        
                        if recovery_result:
                            result.holding_created = True
                            result.auto_recovered = True
                            result.holding_id = recovery_result["holding_id"]
                            result.success = True
                            
                            logger.warning(
                                f"🔧 HOLDING_AUTO_RECOVERY_SUCCESS: Created missing holding for {escrow_id} "
                                f"(ID: {recovery_result['holding_id']}, Amount: ${expected_amount})"
                            )
                            
                            # Send admin alert about auto-recovery
                            await cls._send_admin_alert_if_critical(result, "AUTO_RECOVERY_SUCCESS")
                        else:
                            result.add_error("Auto-recovery failed - could not create missing holding")
                            await cls._send_admin_alert_if_critical(result, "AUTO_RECOVERY_FAILED")
                    else:
                        result.add_error("Auto-recovery disabled - manual intervention required")
                        await cls._send_admin_alert_if_critical(result, "MANUAL_INTERVENTION_REQUIRED")
                
                # Additional verification checks (with provided async session)
                await cls._perform_additional_checks(session, escrow, result, verification_context)
                
                # DO NOT commit or rollback - let parent manage transaction
                return result
                
            else:
                # No session provided - create new one for standalone verification (sync pattern)
                with atomic_transaction() as new_session:
                    # Get escrow details first (sync query)
                    escrow = new_session.query(Escrow).filter(
                        Escrow.escrow_id == escrow_id
                    ).first()
                    
                    if not escrow:
                        result.add_error(f"Escrow {escrow_id} not found in database")
                        await cls._send_admin_alert_if_critical(result, "ESCROW_NOT_FOUND")
                        return result
                    
                    # Check if holding already exists (sync query)
                    existing_holding = new_session.query(EscrowHolding).filter(
                        EscrowHolding.escrow_id == escrow_id
                    ).first()
                    
                    if existing_holding:
                        # Holding exists - verify it's correct
                        result.holding_exists = True
                        result.holding_id = existing_holding.id
                        
                        # Verify amount matches
                        amount_difference = abs(existing_holding.amount_held - expected_amount)
                        if amount_difference > Decimal("0.001"):  # Allow small floating point variance
                            result.add_warning(
                                f"Holding amount mismatch: Expected ${expected_amount}, "
                                f"Found ${existing_holding.amount_held}, Difference: ${amount_difference}"
                            )
                            
                            # AUDIT: Log holding amount mismatch
                            financial_audit_logger.log_financial_event(
                                event_type=FinancialEventType.HOLDING_AMOUNT_MISMATCH,
                                entity_type=EntityType.ESCROW_HOLDING,
                                entity_id=str(existing_holding.id),
                                user_id=escrow.buyer_id,
                                financial_context=FinancialContext(
                                    amount=existing_holding.amount_held,
                                    currency="USD",
                                    expected_amount=expected_amount
                                ),
                                related_entities={
                                    "escrow_id": escrow_id,
                                    "payment_source": payment_source,
                                    "tx_hash": tx_hash
                                },
                                additional_data={
                                    "amount_difference": str(amount_difference),
                                    "verification_context": verification_context
                                },
                                session=new_session
                            )
                        
                        result.success = True
                        logger.info(
                            f"✅ HOLDING_VERIFICATION_SUCCESS: Holding exists for {escrow_id} "
                            f"(ID: {existing_holding.id}, Amount: ${existing_holding.amount_held})"
                        )
                        
                    else:
                        # Holding missing - this is critical!
                        result.add_warning(f"CRITICAL: No holding record found for escrow {escrow_id}")
                        
                        # AUDIT: Log missing holding detection
                        financial_audit_logger.log_financial_event(
                            event_type=FinancialEventType.ESCROW_HOLDING_MISSING,
                            entity_type=EntityType.ESCROW_HOLDING,
                            entity_id=f"missing_{escrow_id}",
                            user_id=escrow.buyer_id,
                            financial_context=FinancialContext(
                                amount=expected_amount,
                                currency="USD"
                            ),
                            related_entities={
                                "escrow_id": escrow_id,
                                "payment_source": payment_source,
                                "tx_hash": tx_hash
                            },
                            additional_data={
                                "auto_recover_enabled": auto_recover,
                                "verification_context": verification_context
                            },
                            session=new_session
                        )
                        
                        if auto_recover:
                            # Attempt auto-recovery
                            recovery_result = await cls._create_missing_holding(
                                new_session, escrow, expected_amount, payment_source, tx_hash, verification_context
                            )
                            
                            if recovery_result:
                                result.holding_created = True
                                result.auto_recovered = True
                                result.holding_id = recovery_result["holding_id"]
                                result.success = True
                                
                                logger.warning(
                                    f"🔧 HOLDING_AUTO_RECOVERY_SUCCESS: Created missing holding for {escrow_id} "
                                    f"(ID: {recovery_result['holding_id']}, Amount: ${expected_amount})"
                                )
                                
                                # Send admin alert about auto-recovery
                                await cls._send_admin_alert_if_critical(result, "AUTO_RECOVERY_SUCCESS")
                            else:
                                result.add_error("Auto-recovery failed - could not create missing holding")
                                await cls._send_admin_alert_if_critical(result, "AUTO_RECOVERY_FAILED")
                        else:
                            result.add_error("Auto-recovery disabled - manual intervention required")
                            await cls._send_admin_alert_if_critical(result, "MANUAL_INTERVENTION_REQUIRED")
                    
                    # Additional verification checks
                    await cls._perform_additional_checks(new_session, escrow, result, verification_context)
                    
                    return result
                
        except Exception as e:
            result.add_error(f"Verification system error: {str(e)}")
            logger.error(f"HOLDING_VERIFICATION_SYSTEM_ERROR: {e}", exc_info=True)
            await cls._send_admin_alert_if_critical(result, "SYSTEM_ERROR")
            return result
    
    @classmethod
    async def _create_missing_holding(
        cls,
        session: Session,
        escrow: Escrow,
        expected_amount: Decimal,
        payment_source: str,
        tx_hash: Optional[str],
        verification_context: Optional[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Create missing holding record with comprehensive audit trail
        
        Returns:
            Dict with holding creation details or None if failed
        """
        try:
            # Create the missing holding
            new_holding = EscrowHolding(
                escrow_id=escrow.escrow_id,
                amount_held=expected_amount,
                currency="USD",
                status="held",
                created_at=datetime.utcnow()
            )
            session.add(new_holding)
            session.flush()  # Get the ID
            
            # AUDIT: Log holding creation
            financial_audit_logger.log_escrow_event(
                event_type=FinancialEventType.ESCROW_HOLDING_AUTO_CREATED,
                escrow_id=escrow.escrow_id,
                user_id=escrow.buyer_id,
                amount=expected_amount,
                currency="USD",
                previous_state="missing",
                new_state="held",
                transaction_id=tx_hash,
                session=session,
                additional_data={
                    "payment_source": payment_source,
                    "auto_recovery": True,
                    "verification_context": verification_context,
                    "holding_id": new_holding.id
                }
            )
            
            logger.critical(
                f"🔧 HOLDING_AUTO_CREATED: Created missing holding ID {new_holding.id} "
                f"for escrow {escrow.escrow_id} (${expected_amount} USD)"
            )
            
            return {
                "holding_id": new_holding.id,
                "amount": str(expected_amount),
                "currency": "USD",
                "created_at": new_holding.created_at.isoformat()
            }
            
        except Exception as e:
            logger.error(f"Failed to create missing holding for {escrow.escrow_id}: {e}")
            return None
    
    @classmethod
    async def _perform_additional_checks(
        cls,
        session: Session,
        escrow: Escrow,
        result: HoldingVerificationResult,
        verification_context: Optional[Dict[str, Any]]
    ) -> None:
        """Perform additional verification checks beyond basic holding existence"""
        try:
            # Check for platform revenue record if applicable
            from services.fee_transparency import FeeTransparencyService
            
            fee_calculation = FeeTransparencyService.calculate_escrow_fees(
                escrow.amount, escrow.buyer_id, escrow.seller_id
            )
            
            if fee_calculation["total_amount"] > escrow.amount and fee_calculation["platform_fee"] > 0:
                # Should have a platform revenue record (SQLAlchemy 2.0 async pattern)
                platform_revenue_result = await session.execute(
                    select(PlatformRevenue).filter(
                        PlatformRevenue.escrow_id == escrow.escrow_id,
                        PlatformRevenue.fee_type == "escrow_fee"
                    )
                )
                platform_revenue = platform_revenue_result.scalars().first()
                
                if not platform_revenue:
                    result.add_warning(
                        f"Missing platform revenue record for escrow {escrow.escrow_id} "
                        f"(Expected fee: ${fee_calculation['platform_fee']})"
                    )
            
            # Check transaction records consistency (SQLAlchemy 2.0 async pattern)
            deposit_transactions_result = await session.execute(
                select(Transaction).filter(
                    Transaction.escrow_id == escrow.id,
                    Transaction.transaction_type == TransactionType.DEPOSIT.value
                )
            )
            deposit_transactions = deposit_transactions_result.scalars().all()
            
            if not deposit_transactions:
                result.add_warning(f"No deposit transaction records found for escrow {escrow.escrow_id}")
            
            # Log additional check results
            result.verification_details["additional_checks"] = {
                "platform_revenue_check": platform_revenue is not None if fee_calculation["platform_fee"] > 0 else "not_required",
                "deposit_transaction_count": len(deposit_transactions),
                "fee_calculation": {
                    "buyer_pays_total": str(fee_calculation["total_amount"]),
                    "platform_fee": str(fee_calculation["platform_fee"])
                }
            }
            
        except Exception as e:
            result.add_warning(f"Additional checks failed: {str(e)}")
            logger.error(f"Additional verification checks failed: {e}")
    
    @classmethod
    async def _send_admin_alert_if_critical(
        cls,
        result: HoldingVerificationResult,
        alert_type: str
    ) -> None:
        """Send admin alerts for critical verification issues"""
        try:
            critical_types = [
                "ESCROW_NOT_FOUND",
                "AUTO_RECOVERY_FAILED",
                "SYSTEM_ERROR",
                "MANUAL_INTERVENTION_REQUIRED"
            ]
            
            if alert_type in critical_types or len(result.errors) > 0:
                # TODO: Implement proper admin email alert service
                logger.critical(
                    f"🚨 HOLDING_VERIFICATION_ALERT: Type={alert_type}, "
                    f"Escrow={result.verification_details.get('escrow_id')}, "
                    f"Errors={len(result.errors)}, Summary={result.get_summary()}"
                )
                # Set to False since no actual notification is sent - only logged
                result.admin_alert_sent = False
                
        except Exception as e:
            logger.error(f"Failed to log admin alert for holding verification: {e}")
    
    @classmethod
    async def bulk_verify_holdings(
        cls,
        escrow_ids: List[str],
        auto_recover: bool = True
    ) -> Dict[str, HoldingVerificationResult]:
        """
        Bulk verification of multiple escrow holdings
        
        Args:
            escrow_ids: List of escrow IDs to verify
            auto_recover: Whether to auto-recover missing holdings
            
        Returns:
            Dict mapping escrow_id to verification results
        """
        results = {}
        
        logger.info(f"🔍 BULK_HOLDING_VERIFICATION_START: Verifying {len(escrow_ids)} escrows")
        
        for escrow_id in escrow_ids:
            try:
                with atomic_transaction() as session:
                    escrow = session.query(Escrow).filter(
                        Escrow.escrow_id == escrow_id
                    ).first()
                    
                    if escrow:
                        expected_amount = Decimal(str(escrow.amount))
                        result = await cls.verify_holding_created(
                            escrow_id=escrow_id,
                            expected_amount=expected_amount,
                            payment_source="bulk_verification",
                            auto_recover=auto_recover,
                            verification_context={"bulk_operation": True}
                        )
                        results[escrow_id] = result
                    else:
                        result = HoldingVerificationResult()
                        result.add_error(f"Escrow {escrow_id} not found")
                        results[escrow_id] = result
                        
            except Exception as e:
                result = HoldingVerificationResult()
                result.add_error(f"Bulk verification error for {escrow_id}: {str(e)}")
                results[escrow_id] = result
        
        # Generate summary report
        successful = sum(1 for r in results.values() if r.success)
        auto_recovered = sum(1 for r in results.values() if r.auto_recovered)
        errors = sum(1 for r in results.values() if r.errors)
        
        logger.info(
            f"🔍 BULK_HOLDING_VERIFICATION_COMPLETE: "
            f"Total: {len(escrow_ids)}, Successful: {successful}, "
            f"Auto-recovered: {auto_recovered}, Errors: {errors}"
        )
        
        return results
    
    @classmethod
    async def get_verification_statistics(
        cls,
        days_back: int = 7
    ) -> Dict[str, Any]:
        """Get verification statistics for monitoring and reporting"""
        try:
            with atomic_transaction() as session:
                cutoff_date = datetime.utcnow() - timedelta(days=days_back)
                
                # Count holdings created in period
                holdings_created = session.query(func.count(EscrowHolding.id)).filter(
                    EscrowHolding.created_at >= cutoff_date
                ).scalar()
                
                # Count escrows created in period
                escrows_created = session.query(func.count(Escrow.id)).filter(
                    Escrow.created_at >= cutoff_date
                ).scalar()
                
                # Calculate holding creation rate
                holding_rate = (holdings_created / max(escrows_created, 1)) * 100
                
                return {
                    "period_days": days_back,
                    "cutoff_date": cutoff_date.isoformat(),
                    "escrows_created": escrows_created,
                    "holdings_created": holdings_created,
                    "holding_creation_rate_percent": round(holding_rate, 2),
                    "potential_missing_holdings": max(0, escrows_created - holdings_created)
                }
                
        except Exception as e:
            logger.error(f"Failed to get verification statistics: {e}")
            return {"error": str(e)}