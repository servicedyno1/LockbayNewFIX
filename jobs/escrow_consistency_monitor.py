"""
Escrow Consistency Monitor
Comprehensive monitoring and repair system to detect escrows with confirmed payments but missing holdings
with automatic repair, detailed logging, and admin alerting
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List
from decimal import Decimal
from sqlalchemy.orm import Session
from sqlalchemy import text

from services.escrow_holding_verifier import EscrowHoldingVerifier
from services.admin_email_alerts import AdminEmailAlertService
from utils.atomic_transactions import atomic_transaction
from utils.financial_audit_logger import (
    financial_audit_logger,
    FinancialEventType,
    FinancialContext,
    EntityType
)

logger = logging.getLogger(__name__)


class EscrowConsistencyResult:
    """Result object for consistency monitoring operations"""
    
    def __init__(self):
        self.total_escrows_checked = 0
        self.inconsistencies_found = 0
        self.holdings_repaired = 0
        self.repair_failures = 0
        self.critical_issues = 0
        self.admin_alerts_sent = 0
        self.execution_time_ms = 0
        self.escrows_with_issues = []
        self.repairs_completed = []
        self.errors = []
        self.warnings = []
        
    def add_inconsistency(self, escrow_id: str, issue_type: str, details: Dict[str, Any]):
        """Record an inconsistency found"""
        self.inconsistencies_found += 1
        issue_record = {
            "escrow_id": escrow_id,
            "issue_type": issue_type,
            "details": details,
            "detected_at": datetime.utcnow().isoformat()
        }
        self.escrows_with_issues.append(issue_record)
        
        # Determine if this is critical
        if issue_type in ["missing_holding_with_payment", "amount_mismatch_critical"]:
            self.critical_issues += 1
    
    def add_repair(self, escrow_id: str, repair_type: str, details: Dict[str, Any]):
        """Record a successful repair"""
        self.holdings_repaired += 1
        repair_record = {
            "escrow_id": escrow_id,
            "repair_type": repair_type,
            "details": details,
            "repaired_at": datetime.utcnow().isoformat()
        }
        self.repairs_completed.append(repair_record)
    
    def add_error(self, error: str):
        """Add error to results"""
        self.errors.append(error)
        logger.error(f"CONSISTENCY_MONITOR_ERROR: {error}")
    
    def add_warning(self, warning: str):
        """Add warning to results"""
        self.warnings.append(warning)
        logger.warning(f"CONSISTENCY_MONITOR_WARNING: {warning}")
    
    def get_summary(self) -> Dict[str, Any]:
        """Get comprehensive summary of consistency check results"""
        return {
            "total_escrows_checked": self.total_escrows_checked,
            "inconsistencies_found": self.inconsistencies_found,
            "holdings_repaired": self.holdings_repaired,
            "repair_failures": self.repair_failures,
            "critical_issues": self.critical_issues,
            "admin_alerts_sent": self.admin_alerts_sent,
            "execution_time_ms": self.execution_time_ms,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "success_rate": round((self.holdings_repaired / max(self.inconsistencies_found, 1)) * 100, 2)
        }


class EscrowConsistencyMonitor:
    """Comprehensive escrow consistency monitoring and repair service"""
    
    @classmethod
    async def run_consistency_check(cls, max_escrows_to_check: int = 1000) -> EscrowConsistencyResult:
        """
        Main entry point - run comprehensive consistency check and repair
        
        Args:
            max_escrows_to_check: Maximum number of escrows to check in one run
            
        Returns:
            EscrowConsistencyResult with detailed results
        """
        start_time = datetime.utcnow()
        result = EscrowConsistencyResult()
        
        try:
            logger.info(f"🔍 CONSISTENCY_CHECK_START: Starting escrow consistency monitoring (max: {max_escrows_to_check})")
            
            with atomic_transaction() as session:
                # 1. Find escrows with confirmed payments but missing holdings
                inconsistent_escrows = await cls._detect_inconsistent_escrows(session, max_escrows_to_check)
                result.total_escrows_checked = len(inconsistent_escrows)
                
                if not inconsistent_escrows:
                    logger.info("✅ CONSISTENCY_CHECK_SUCCESS: No inconsistent escrows found")
                    return result
                
                logger.warning(f"⚠️ CONSISTENCY_ISSUES_DETECTED: Found {len(inconsistent_escrows)} escrows with potential issues")
                
                # 2. Analyze each inconsistent escrow
                for escrow_data in inconsistent_escrows:
                    await cls._analyze_and_repair_escrow(session, escrow_data, result)
                
                # 3. Send admin alerts for critical issues
                if result.critical_issues > 0:
                    await cls._send_admin_alerts(result)
                
                # 4. Log comprehensive results
                await cls._log_consistency_results(result, session)
                
                end_time = datetime.utcnow()
                result.execution_time_ms = int((end_time - start_time).total_seconds() * 1000)
                
                logger.info(
                    f"🔧 CONSISTENCY_CHECK_COMPLETE: Checked {result.total_escrows_checked} escrows, "
                    f"found {result.inconsistencies_found} issues, repaired {result.holdings_repaired}, "
                    f"execution time: {result.execution_time_ms}ms"
                )
                
                return result
                
        except Exception as e:
            result.add_error(f"Consistency check system error: {str(e)}")
            logger.error(f"CONSISTENCY_CHECK_SYSTEM_ERROR: {e}", exc_info=True)
            
            end_time = datetime.utcnow()
            result.execution_time_ms = int((end_time - start_time).total_seconds() * 1000)
            return result
    
    @classmethod
    async def _detect_inconsistent_escrows(cls, session: Session, max_escrows: int) -> List[Dict[str, Any]]:
        """
        Detect escrows with confirmed payments but missing or incorrect holdings
        
        Returns:
            List of escrow data dictionaries for inconsistent escrows
        """
        try:
            # Query for potentially inconsistent escrows
            # Look for escrows that should have holdings but don't
            query = text("""
                SELECT 
                    e.escrow_id,
                    e.amount,
                    e.currency,
                    e.status,
                    e.deposit_confirmed,
                    e.payment_confirmed_at,
                    e.buyer_id,
                    e.seller_id,
                    e.created_at,
                    eh.id as holding_id,
                    eh.amount_held,
                    eh.currency as holding_currency,
                    (SELECT COUNT(*) FROM transactions t WHERE t.escrow_id::text = e.escrow_id AND t.transaction_type = 'deposit') as deposit_tx_count
                FROM escrows e
                LEFT JOIN escrow_holdings eh ON e.escrow_id = eh.escrow_id
                WHERE 
                    (
                        -- Case 1: Deposit confirmed but no holding record
                        (e.deposit_confirmed = true AND eh.id IS NULL)
                        OR
                        -- Case 2: Payment confirmed but no holding record  
                        (e.payment_confirmed_at IS NOT NULL AND eh.id IS NULL)
                        OR
                        -- Case 3: Active status but no holding record
                        (e.status = 'active' AND eh.id IS NULL)
                        OR
                        -- Case 4: Has deposit transactions but no holding record
                        (eh.id IS NULL AND EXISTS (
                            SELECT 1 FROM transactions t 
                            WHERE t.escrow_id::text = e.escrow_id 
                            AND t.transaction_type = 'deposit' 
                            AND t.amount > 0
                        ))
                    )
                    AND e.status NOT IN ('cancelled', 'refunded', 'expired', 'completed')
                    AND e.created_at >= :cutoff_date
                ORDER BY e.created_at DESC
                LIMIT :max_limit
            """)
            
            # Look at escrows from the last 30 days to avoid very old records
            cutoff_date = datetime.utcnow() - timedelta(days=30)
            
            raw_results = session.execute(query, {
                "cutoff_date": cutoff_date,
                "max_limit": max_escrows
            }).fetchall()
            
            # Convert to dictionaries for easier handling
            inconsistent_escrows = []
            for row in raw_results:
                escrow_data = {
                    "escrow_id": row.escrow_id,
                    "amount": row.amount,
                    "currency": row.currency,
                    "status": row.status,
                    "deposit_confirmed": row.deposit_confirmed,
                    "payment_confirmed_at": row.payment_confirmed_at,
                    "buyer_id": row.buyer_id,
                    "seller_id": row.seller_id,
                    "created_at": row.created_at,
                    "holding_id": row.holding_id,
                    "amount_held": row.amount_held,
                    "holding_currency": row.holding_currency,
                    "deposit_tx_count": row.deposit_tx_count
                }
                inconsistent_escrows.append(escrow_data)
            
            logger.info(f"🔍 DETECTION_COMPLETE: Found {len(inconsistent_escrows)} potentially inconsistent escrows")
            return inconsistent_escrows
            
        except Exception as e:
            logger.error(f"DETECTION_ERROR: Failed to detect inconsistent escrows: {e}", exc_info=True)
            return []
    
    @classmethod
    async def _analyze_and_repair_escrow(cls, session: Session, escrow_data: Dict[str, Any], result: EscrowConsistencyResult):
        """
        Analyze a specific escrow for consistency issues and attempt repair
        
        Args:
            session: Database session
            escrow_data: Escrow data dictionary from detection query
            result: Result object to update
        """
        escrow_id = escrow_data["escrow_id"]
        
        try:
            # Determine the type of inconsistency
            issue_type = cls._classify_inconsistency(escrow_data)
            
            # Record the inconsistency
            result.add_inconsistency(escrow_id, issue_type, {
                "escrow_amount": str(escrow_data["amount"]),
                "escrow_status": escrow_data["status"],
                "deposit_confirmed": escrow_data["deposit_confirmed"],
                "payment_confirmed_at": escrow_data["payment_confirmed_at"].isoformat() if escrow_data["payment_confirmed_at"] else None,
                "deposit_tx_count": escrow_data["deposit_tx_count"],
                "has_holding": escrow_data["holding_id"] is not None
            })
            
            # Attempt automatic repair if appropriate
            if issue_type in ["missing_holding_with_payment", "missing_holding_confirmed_deposit"]:
                repair_success = await cls._repair_missing_holding(session, escrow_data, result)
                
                if repair_success:
                    result.add_repair(escrow_id, "created_missing_holding", {
                        "amount": str(escrow_data["amount"]),
                        "currency": escrow_data["currency"],
                        "repair_method": "auto_repair_verified"
                    })
                else:
                    result.repair_failures += 1
                    result.add_error(f"Failed to repair missing holding for escrow {escrow_id}")
            else:
                logger.info(f"📋 MANUAL_REVIEW_REQUIRED: Escrow {escrow_id} requires manual review (issue: {issue_type})")
                
        except Exception as e:
            result.add_error(f"Analysis failed for escrow {escrow_id}: {str(e)}")
            logger.error(f"ANALYSIS_ERROR: Failed to analyze escrow {escrow_id}: {e}", exc_info=True)
    
    @classmethod
    def _classify_inconsistency(cls, escrow_data: Dict[str, Any]) -> str:
        """
        Classify the type of inconsistency for proper handling
        
        Returns:
            String describing the inconsistency type
        """
        has_holding = escrow_data["holding_id"] is not None
        deposit_confirmed = escrow_data["deposit_confirmed"]
        payment_confirmed = escrow_data["payment_confirmed_at"] is not None
        has_deposit_tx = escrow_data["deposit_tx_count"] > 0
        status = escrow_data["status"]
        
        if not has_holding:
            if payment_confirmed:
                return "missing_holding_with_payment"
            elif deposit_confirmed:
                return "missing_holding_confirmed_deposit"
            elif status == "active":
                return "missing_holding_active_status"
            elif has_deposit_tx:
                return "missing_holding_with_transactions"
            else:
                return "missing_holding_unknown_cause"
        else:
            # Has holding but other issues
            amount_mismatch = abs(escrow_data["amount"] - escrow_data["amount_held"]) > Decimal("0.001")
            if amount_mismatch:
                return "amount_mismatch_critical"
            else:
                return "holding_exists_minor_issue"
    
    @classmethod
    async def _repair_missing_holding(cls, session: Session, escrow_data: Dict[str, Any], result: EscrowConsistencyResult) -> bool:
        """
        Attempt to repair missing holding using EscrowHoldingVerifier
        
        Returns:
            True if repair was successful, False otherwise
        """
        escrow_id = escrow_data["escrow_id"]
        expected_amount = Decimal(str(escrow_data["amount"]))
        
        try:
            logger.info(f"🔧 REPAIR_ATTEMPT: Attempting to repair missing holding for {escrow_id} (amount: ${expected_amount})")
            
            # Use existing EscrowHoldingVerifier for consistent repair logic
            verification_result = await EscrowHoldingVerifier.verify_holding_created(
                escrow_id=escrow_id,
                expected_amount=expected_amount,
                payment_source="consistency_monitor_repair",
                auto_recover=True,
                verification_context={
                    "repair_trigger": "consistency_monitor",
                    "repair_timestamp": datetime.utcnow().isoformat(),
                    "original_issue_type": cls._classify_inconsistency(escrow_data),
                    "escrow_status": escrow_data["status"],
                    "deposit_confirmed": escrow_data["deposit_confirmed"],
                    "payment_confirmed": escrow_data["payment_confirmed_at"] is not None
                }
            )
            
            if verification_result.success and verification_result.auto_recovered:
                logger.info(f"✅ REPAIR_SUCCESS: Successfully created holding for {escrow_id} (ID: {verification_result.holding_id})")
                
                # AUDIT: Log the repair
                financial_audit_logger.log_financial_event(
                    event_type=FinancialEventType.ESCROW_HOLDING_CONSISTENCY_REPAIR,
                    entity_type=EntityType.ESCROW_HOLDING,
                    entity_id=str(verification_result.holding_id),
                    user_id=escrow_data["buyer_id"],
                    financial_context=FinancialContext(
                        amount=expected_amount,
                        currency=escrow_data["currency"]
                    ),
                    related_entities={
                        "escrow_id": escrow_id,
                        "repair_source": "consistency_monitor"
                    },
                    additional_data={
                        "verification_result": verification_result.get_summary(),
                        "escrow_data": {
                            "status": escrow_data["status"],
                            "deposit_confirmed": escrow_data["deposit_confirmed"],
                            "created_at": escrow_data["created_at"].isoformat()
                        }
                    },
                    session=session
                )
                
                return True
            else:
                logger.error(f"❌ REPAIR_FAILED: Could not repair holding for {escrow_id}: {verification_result.errors}")
                return False
                
        except Exception as e:
            logger.error(f"REPAIR_ERROR: Exception during repair of {escrow_id}: {e}", exc_info=True)
            return False
    
    @classmethod
    async def _send_admin_alerts(cls, result: EscrowConsistencyResult):
        """Send admin alerts for critical consistency issues"""
        try:
            if result.critical_issues > 0:
                # Prepare alert details
                alert_data = {
                    "critical_issues": result.critical_issues,
                    "total_inconsistencies": result.inconsistencies_found,
                    "repairs_completed": result.holdings_repaired,
                    "repair_failures": result.repair_failures,
                    "execution_time": result.execution_time_ms,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
                # Send email alert to admins
                admin_alerts = AdminEmailAlertService()
                await admin_alerts.send_critical_system_alert(
                    subject=f"🚨 Escrow Consistency Issues Detected - {result.critical_issues} Critical",
                    alert_type="ESCROW_CONSISTENCY_CRITICAL",
                    details=alert_data,
                    escalation_level="high"
                )
                
                result.admin_alerts_sent += 1
                logger.warning(f"📧 ADMIN_ALERT_SENT: Notified admins of {result.critical_issues} critical consistency issues")
                
        except Exception as e:
            logger.error(f"ADMIN_ALERT_ERROR: Failed to send admin alerts: {e}", exc_info=True)
    
    @classmethod
    async def _log_consistency_results(cls, result: EscrowConsistencyResult, session: Session):
        """Log comprehensive consistency check results for auditing"""
        try:
            # Create comprehensive audit log entry
            financial_audit_logger.log_financial_event(
                event_type=FinancialEventType.ESCROW_CONSISTENCY_CHECK_COMPLETED,
                entity_type=EntityType.SYSTEM_MONITOR,
                entity_id=f"consistency_check_{int(datetime.utcnow().timestamp())}",
                user_id=None,  # System operation
                financial_context=FinancialContext(
                    amount=Decimal("0"),  # Not applicable for monitoring
                    currency="USD"
                ),
                related_entities={
                    "monitor_type": "escrow_consistency"
                },
                additional_data={
                    "summary": result.get_summary(),
                    "escrows_with_issues": result.escrows_with_issues[:10],  # Limit to first 10 for size
                    "repairs_completed": result.repairs_completed,
                    "performance_metrics": {
                        "execution_time_ms": result.execution_time_ms,
                        "escrows_per_second": round(result.total_escrows_checked / max(result.execution_time_ms / 1000, 0.001), 2)
                    }
                },
                session=session
            )
            
        except Exception as e:
            logger.error(f"AUDIT_LOG_ERROR: Failed to log consistency results: {e}", exc_info=True)


# Main entry point for scheduler
async def monitor_escrow_consistency() -> Dict[str, Any]:
    """
    Main entry point for escrow consistency monitoring job
    Called by the scheduler every 15 minutes
    
    Returns:
        Dictionary with monitoring results
    """
    try:
        result = await EscrowConsistencyMonitor.run_consistency_check(max_escrows_to_check=500)
        summary = result.get_summary()
        
        # Log summary for scheduler monitoring
        if summary["inconsistencies_found"] > 0:
            logger.warning(
                f"⚠️ CONSISTENCY_MONITOR_SUMMARY: Found {summary['inconsistencies_found']} issues, "
                f"repaired {summary['holdings_repaired']}, success rate: {summary['success_rate']}%"
            )
        else:
            logger.info("✅ CONSISTENCY_MONITOR_SUMMARY: No inconsistencies found - all escrows consistent")
        
        return summary
        
    except Exception as e:
        logger.error(f"CONSISTENCY_MONITOR_MAIN_ERROR: {e}", exc_info=True)
        return {
            "success": False,
            "error": str(e),
            "inconsistencies_found": 0,
            "holdings_repaired": 0
        }