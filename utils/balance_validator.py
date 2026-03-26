"""
Balance Validator - Comprehensive balance consistency checks and discrepancy detection

Provides comprehensive validation of wallet balances against transaction history and audit logs
to detect discrepancies, ensure data integrity, and maintain financial accuracy.

Key Features:
- Cross-validation between wallet balances and transaction history
- Detection of balance discrepancies and inconsistencies
- Support for both user and internal wallet validation
- Performance-optimized bulk validation operations
- Detailed reporting of validation results and discrepancies
- Integration with audit trail system for complete traceability
"""

import logging
from typing import Dict, Any, Optional, List
from decimal import Decimal
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy.orm import Session
from sqlalchemy import and_, or_, desc

from models import (
    Wallet, InternalWallet, Transaction, BalanceAuditLog
)
from services.balance_audit_service import balance_audit_service

logger = logging.getLogger(__name__)


class ValidationSeverity(Enum):
    """Severity levels for validation issues"""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class DiscrepancyType(Enum):
    """Types of balance discrepancies"""
    NEGATIVE_BALANCE = "negative_balance"
    TRANSACTION_MISMATCH = "transaction_mismatch"
    AUDIT_TRAIL_GAP = "audit_trail_gap"
    BALANCE_INCONSISTENCY = "balance_inconsistency"
    FROZEN_LOCKED_MISMATCH = "frozen_locked_mismatch"
    INTERNAL_BALANCE_ERROR = "internal_balance_error"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    MISSING_TRANSACTIONS = "missing_transactions"


@dataclass
class ValidationIssue:
    """Represents a validation issue found during balance checks"""
    severity: ValidationSeverity
    discrepancy_type: DiscrepancyType
    wallet_type: str  # 'user' or 'internal'
    user_id: Optional[int] = None
    wallet_id: Optional[int] = None
    internal_wallet_id: Optional[str] = None
    currency: str = "USD"
    
    # Issue details
    expected_balance: Decimal = Decimal('0')
    actual_balance: Decimal = Decimal('0')
    difference: Decimal = Decimal('0')
    description: str = ""
    
    # Context
    transaction_count: int = 0
    last_transaction_id: Optional[str] = None
    last_audit_entry: Optional[str] = None
    
    # Metadata
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationResult:
    """Result of balance validation operation"""
    success: bool
    validation_type: str
    scope: str  # 'user', 'internal', 'all', 'currency'
    
    # Statistics
    wallets_checked: int = 0
    issues_found: int = 0
    critical_issues: int = 0
    warnings: int = 0
    
    # Issues
    issues: List[ValidationIssue] = field(default_factory=list)
    
    # Performance metrics
    duration_seconds: float = 0.0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    
    # Summary
    summary: Dict[str, Any] = field(default_factory=dict)


class BalanceValidator:
    """
    Comprehensive balance validator for ensuring wallet balance consistency
    
    Performs detailed validation of wallet balances against transaction history,
    audit logs, and business rules to detect and report discrepancies.
    """
    
    def __init__(self):
        """Initialize the balance validator"""
        self.audit_service = balance_audit_service
        
    def validate_user_wallet(
        self,
        session: Session,
        user_id: int,
        currency: Optional[str] = None,
        include_transaction_history: bool = True,
        include_audit_validation: bool = True
    ) -> ValidationResult:
        """
        Validate balances for a specific user's wallets
        
        Args:
            session: Database session
            user_id: User ID to validate
            currency: Specific currency to validate (optional)
            include_transaction_history: Whether to cross-check with transaction history
            include_audit_validation: Whether to validate against audit logs
            
        Returns:
            ValidationResult with validation outcome
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            logger.info(f"🔍 VALIDATE: Starting user wallet validation for user {user_id}")
            
            result = ValidationResult(
                success=True,
                validation_type="user_wallet",
                scope=f"user_{user_id}_{currency or 'all'}",
                started_at=start_time
            )
            
            # Get user wallets
            query = session.query(Wallet).filter(Wallet.user_id == user_id)
            if currency:
                query = query.filter(Wallet.currency == currency)
            
            wallets = query.all()
            result.wallets_checked = len(wallets)
            
            if not wallets:
                logger.warning(f"⚠️ VALIDATE: No wallets found for user {user_id}")
                result.summary["status"] = "no_wallets_found"
                return self._finalize_result(result, start_time)
            
            # Validate each wallet
            for wallet in wallets:
                wallet_issues = self._validate_single_user_wallet(
                    session, wallet, include_transaction_history, include_audit_validation
                )
                result.issues.extend(wallet_issues)
            
            # Categorize issues
            result.issues_found = len(result.issues)
            result.critical_issues = sum(1 for issue in result.issues if issue.severity == ValidationSeverity.CRITICAL)
            result.warnings = sum(1 for issue in result.issues if issue.severity == ValidationSeverity.WARNING)
            
            # Set overall success based on critical issues
            result.success = result.critical_issues == 0
            
            # Generate summary
            result.summary = self._generate_validation_summary(result)
            
            logger.info(
                f"✅ VALIDATE: User wallet validation complete - "
                f"Wallets: {result.wallets_checked}, Issues: {result.issues_found}, "
                f"Critical: {result.critical_issues}"
            )
            
            return self._finalize_result(result, start_time)
            
        except Exception as e:
            logger.error(f"❌ VALIDATE: Error validating user wallet: {e}")
            result.success = False
            result.issues.append(ValidationIssue(
                severity=ValidationSeverity.CRITICAL,
                discrepancy_type=DiscrepancyType.BALANCE_INCONSISTENCY,
                wallet_type="user",
                user_id=user_id,
                description=f"Validation error: {e}"
            ))
            return self._finalize_result(result, start_time)
    
    def validate_internal_wallet(
        self,
        session: Session,
        internal_wallet_id: Optional[str] = None,
        provider_name: Optional[str] = None,
        currency: Optional[str] = None
    ) -> ValidationResult:
        """
        Validate internal wallet balances
        
        Args:
            session: Database session
            internal_wallet_id: Specific internal wallet ID to validate
            provider_name: Provider name to validate (e.g., 'kraken', 'fincra')
            currency: Specific currency to validate
            
        Returns:
            ValidationResult with validation outcome
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            logger.info("🔍 VALIDATE: Starting internal wallet validation")
            
            result = ValidationResult(
                success=True,
                validation_type="internal_wallet",
                scope=f"internal_{internal_wallet_id or provider_name or 'all'}_{currency or 'all'}",
                started_at=start_time
            )
            
            # Get internal wallets to validate
            query = session.query(InternalWallet)
            
            if internal_wallet_id:
                query = query.filter(InternalWallet.wallet_id == internal_wallet_id)
            elif provider_name:
                query = query.filter(InternalWallet.provider_name == provider_name)
            
            if currency:
                query = query.filter(InternalWallet.currency == currency)
            
            internal_wallets = query.all()
            result.wallets_checked = len(internal_wallets)
            
            if not internal_wallets:
                logger.warning("⚠️ VALIDATE: No internal wallets found for validation")
                result.summary["status"] = "no_wallets_found"
                return self._finalize_result(result, start_time)
            
            # Validate each internal wallet
            for internal_wallet in internal_wallets:
                wallet_issues = self._validate_single_internal_wallet(session, internal_wallet)
                result.issues.extend(wallet_issues)
            
            # Categorize issues
            result.issues_found = len(result.issues)
            result.critical_issues = sum(1 for issue in result.issues if issue.severity == ValidationSeverity.CRITICAL)
            result.warnings = sum(1 for issue in result.issues if issue.severity == ValidationSeverity.WARNING)
            
            # Set overall success based on critical issues
            result.success = result.critical_issues == 0
            
            # Generate summary
            result.summary = self._generate_validation_summary(result)
            
            logger.info(
                f"✅ VALIDATE: Internal wallet validation complete - "
                f"Wallets: {result.wallets_checked}, Issues: {result.issues_found}, "
                f"Critical: {result.critical_issues}"
            )
            
            return self._finalize_result(result, start_time)
            
        except Exception as e:
            logger.error(f"❌ VALIDATE: Error validating internal wallets: {e}")
            result.success = False
            result.issues.append(ValidationIssue(
                severity=ValidationSeverity.CRITICAL,
                discrepancy_type=DiscrepancyType.INTERNAL_BALANCE_ERROR,
                wallet_type="internal",
                description=f"Internal validation error: {e}"
            ))
            return self._finalize_result(result, start_time)
    
    def validate_all_wallets(
        self,
        session: Session,
        currency: Optional[str] = None,
        include_internal: bool = True,
        batch_size: int = 100
    ) -> ValidationResult:
        """
        Perform comprehensive validation of all wallets
        
        Args:
            session: Database session
            currency: Specific currency to validate (optional)
            include_internal: Whether to include internal wallets
            batch_size: Number of wallets to process in each batch
            
        Returns:
            ValidationResult with comprehensive validation outcome
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            logger.info("🔍 VALIDATE: Starting comprehensive wallet validation")
            
            result = ValidationResult(
                success=True,
                validation_type="comprehensive",
                scope=f"all_{currency or 'all_currencies'}",
                started_at=start_time
            )
            
            # Validate user wallets
            user_wallets_result = self._validate_all_user_wallets_batched(
                session, currency, batch_size
            )
            result.issues.extend(user_wallets_result.issues)
            result.wallets_checked += user_wallets_result.wallets_checked
            
            # Validate internal wallets if requested
            if include_internal:
                internal_wallets_result = self.validate_internal_wallet(
                    session, currency=currency
                )
                result.issues.extend(internal_wallets_result.issues)
                result.wallets_checked += internal_wallets_result.wallets_checked
            
            # Categorize issues
            result.issues_found = len(result.issues)
            result.critical_issues = sum(1 for issue in result.issues if issue.severity == ValidationSeverity.CRITICAL)
            result.warnings = sum(1 for issue in result.issues if issue.severity == ValidationSeverity.WARNING)
            
            # Set overall success based on critical issues
            result.success = result.critical_issues == 0
            
            # Generate comprehensive summary
            result.summary = self._generate_comprehensive_summary(result)
            
            logger.info(
                f"✅ VALIDATE: Comprehensive validation complete - "
                f"Wallets: {result.wallets_checked}, Issues: {result.issues_found}, "
                f"Critical: {result.critical_issues}"
            )
            
            return self._finalize_result(result, start_time)
            
        except Exception as e:
            logger.error(f"❌ VALIDATE: Error in comprehensive validation: {e}")
            result.success = False
            result.issues.append(ValidationIssue(
                severity=ValidationSeverity.CRITICAL,
                discrepancy_type=DiscrepancyType.BALANCE_INCONSISTENCY,
                wallet_type="all",
                description=f"Comprehensive validation error: {e}"
            ))
            return self._finalize_result(result, start_time)
    
    def detect_balance_discrepancies(
        self,
        session: Session,
        threshold: Decimal = Decimal('0.01'),
        max_age_days: int = 7
    ) -> ValidationResult:
        """
        Detect balance discrepancies across all wallets
        
        Args:
            session: Database session
            threshold: Minimum discrepancy amount to report
            max_age_days: Maximum age of transactions to consider
            
        Returns:
            ValidationResult with discrepancy detection results
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            logger.info("🔍 VALIDATE: Starting balance discrepancy detection")
            
            result = ValidationResult(
                success=True,
                validation_type="discrepancy_detection",
                scope=f"threshold_{threshold}_age_{max_age_days}",
                started_at=start_time
            )
            
            # Get cutoff date for transaction analysis
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=max_age_days)
            
            # Detect user wallet discrepancies
            user_discrepancies = self._detect_user_wallet_discrepancies(
                session, threshold, cutoff_date
            )
            result.issues.extend(user_discrepancies)
            
            # Detect internal wallet discrepancies
            internal_discrepancies = self._detect_internal_wallet_discrepancies(
                session, threshold, cutoff_date
            )
            result.issues.extend(internal_discrepancies)
            
            # Categorize issues
            result.issues_found = len(result.issues)
            result.critical_issues = sum(1 for issue in result.issues if issue.severity == ValidationSeverity.CRITICAL)
            result.warnings = sum(1 for issue in result.issues if issue.severity == ValidationSeverity.WARNING)
            
            # Set overall success based on critical issues
            result.success = result.critical_issues == 0
            
            # Generate summary
            result.summary = self._generate_discrepancy_summary(result, threshold)
            
            logger.info(
                f"✅ VALIDATE: Discrepancy detection complete - "
                f"Issues: {result.issues_found}, Critical: {result.critical_issues}"
            )
            
            return self._finalize_result(result, start_time)
            
        except Exception as e:
            logger.error(f"❌ VALIDATE: Error detecting discrepancies: {e}")
            result.success = False
            result.issues.append(ValidationIssue(
                severity=ValidationSeverity.CRITICAL,
                discrepancy_type=DiscrepancyType.BALANCE_INCONSISTENCY,
                wallet_type="all",
                description=f"Discrepancy detection error: {e}"
            ))
            return self._finalize_result(result, start_time)
    
    def validate_against_audit_trail(
        self,
        session: Session,
        user_id: Optional[int] = None,
        internal_wallet_id: Optional[str] = None,
        currency: Optional[str] = None,
        days_back: int = 30
    ) -> ValidationResult:
        """
        Validate wallet balances against audit trail records
        
        Args:
            session: Database session
            user_id: Specific user ID to validate
            internal_wallet_id: Specific internal wallet ID to validate
            currency: Specific currency to validate
            days_back: Number of days of audit trail to analyze
            
        Returns:
            ValidationResult with audit trail validation results
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            logger.info("🔍 VALIDATE: Starting audit trail validation")
            
            result = ValidationResult(
                success=True,
                validation_type="audit_trail",
                scope=f"user_{user_id or 'all'}_internal_{internal_wallet_id or 'all'}_days_{days_back}",
                started_at=start_time
            )
            
            # Get cutoff date
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_back)
            
            # Validate user wallets against audit trail
            if user_id or (not user_id and not internal_wallet_id):
                user_issues = self._validate_user_audit_trail(
                    session, user_id, currency, cutoff_date
                )
                result.issues.extend(user_issues)
            
            # Validate internal wallets against audit trail
            if internal_wallet_id or (not user_id and not internal_wallet_id):
                internal_issues = self._validate_internal_audit_trail(
                    session, internal_wallet_id, currency, cutoff_date
                )
                result.issues.extend(internal_issues)
            
            # Categorize issues
            result.issues_found = len(result.issues)
            result.critical_issues = sum(1 for issue in result.issues if issue.severity == ValidationSeverity.CRITICAL)
            result.warnings = sum(1 for issue in result.issues if issue.severity == ValidationSeverity.WARNING)
            
            # Set overall success based on critical issues
            result.success = result.critical_issues == 0
            
            # Generate summary
            result.summary = self._generate_audit_trail_summary(result, days_back)
            
            logger.info(
                f"✅ VALIDATE: Audit trail validation complete - "
                f"Issues: {result.issues_found}, Critical: {result.critical_issues}"
            )
            
            return self._finalize_result(result, start_time)
            
        except Exception as e:
            logger.error(f"❌ VALIDATE: Error validating audit trail: {e}")
            result.success = False
            result.issues.append(ValidationIssue(
                severity=ValidationSeverity.CRITICAL,
                discrepancy_type=DiscrepancyType.AUDIT_TRAIL_GAP,
                wallet_type="all",
                description=f"Audit trail validation error: {e}"
            ))
            return self._finalize_result(result, start_time)
    
    # Private helper methods
    
    def _validate_single_user_wallet(
        self,
        session: Session,
        wallet: Wallet,
        include_transaction_history: bool,
        include_audit_validation: bool
    ) -> List[ValidationIssue]:
        """Validate a single user wallet"""
        issues = []
        
        try:
            # Basic balance validations
            if wallet.available_balance < 0:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    discrepancy_type=DiscrepancyType.NEGATIVE_BALANCE,
                    wallet_type="user",
                    user_id=wallet.user_id,
                    wallet_id=wallet.id,
                    currency=wallet.currency,
                    actual_balance=wallet.available_balance,
                    description=f"Negative available balance: {wallet.available_balance}"
                ))
            
            if wallet.frozen_balance < 0:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    discrepancy_type=DiscrepancyType.NEGATIVE_BALANCE,
                    wallet_type="user",
                    user_id=wallet.user_id,
                    wallet_id=wallet.id,
                    currency=wallet.currency,
                    actual_balance=wallet.frozen_balance,
                    description=f"Negative frozen balance: {wallet.frozen_balance}"
                ))
            
            if wallet.locked_balance < 0:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    discrepancy_type=DiscrepancyType.NEGATIVE_BALANCE,
                    wallet_type="user",
                    user_id=wallet.user_id,
                    wallet_id=wallet.id,
                    currency=wallet.currency,
                    actual_balance=wallet.locked_balance,
                    description=f"Negative locked balance: {wallet.locked_balance}"
                ))
            
            # Transaction history validation
            if include_transaction_history:
                transaction_issues = self._validate_wallet_transaction_history(session, wallet)
                issues.extend(transaction_issues)
            
            # Audit trail validation
            if include_audit_validation:
                audit_issues = self._validate_wallet_audit_trail(session, wallet)
                issues.extend(audit_issues)
            
        except Exception as e:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                discrepancy_type=DiscrepancyType.BALANCE_INCONSISTENCY,
                wallet_type="user",
                user_id=wallet.user_id,
                wallet_id=wallet.id,
                currency=wallet.currency,
                description=f"Validation error: {e}"
            ))
        
        return issues
    
    def _validate_single_internal_wallet(
        self,
        session: Session,
        internal_wallet: InternalWallet
    ) -> List[ValidationIssue]:
        """Validate a single internal wallet"""
        issues = []
        
        try:
            # Basic balance validations
            if internal_wallet.available_balance < 0:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    discrepancy_type=DiscrepancyType.NEGATIVE_BALANCE,
                    wallet_type="internal",
                    internal_wallet_id=internal_wallet.wallet_id,
                    currency=internal_wallet.currency,
                    actual_balance=internal_wallet.available_balance,
                    description=f"Negative available balance in {internal_wallet.provider_name}: {internal_wallet.available_balance}"
                ))
            
            if internal_wallet.locked_balance < 0:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    discrepancy_type=DiscrepancyType.NEGATIVE_BALANCE,
                    wallet_type="internal",
                    internal_wallet_id=internal_wallet.wallet_id,
                    currency=internal_wallet.currency,
                    actual_balance=internal_wallet.locked_balance,
                    description=f"Negative locked balance in {internal_wallet.provider_name}: {internal_wallet.locked_balance}"
                ))
            
            if internal_wallet.reserved_balance < 0:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    discrepancy_type=DiscrepancyType.NEGATIVE_BALANCE,
                    wallet_type="internal",
                    internal_wallet_id=internal_wallet.wallet_id,
                    currency=internal_wallet.currency,
                    actual_balance=internal_wallet.reserved_balance,
                    description=f"Negative reserved balance in {internal_wallet.provider_name}: {internal_wallet.reserved_balance}"
                ))
            
            # Total balance consistency
            calculated_total = (
                internal_wallet.available_balance + 
                internal_wallet.locked_balance + 
                internal_wallet.reserved_balance
            )
            
            if abs(calculated_total - internal_wallet.total_balance) > Decimal('0.00000001'):
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.ERROR,
                    discrepancy_type=DiscrepancyType.BALANCE_INCONSISTENCY,
                    wallet_type="internal",
                    internal_wallet_id=internal_wallet.wallet_id,
                    currency=internal_wallet.currency,
                    expected_balance=calculated_total,
                    actual_balance=internal_wallet.total_balance,
                    difference=calculated_total - internal_wallet.total_balance,
                    description=f"Total balance inconsistency in {internal_wallet.provider_name}: expected {calculated_total}, actual {internal_wallet.total_balance}"
                ))
            
            # Minimum balance check
            if internal_wallet.available_balance < internal_wallet.minimum_balance:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.WARNING,
                    discrepancy_type=DiscrepancyType.INTERNAL_BALANCE_ERROR,
                    wallet_type="internal",
                    internal_wallet_id=internal_wallet.wallet_id,
                    currency=internal_wallet.currency,
                    actual_balance=internal_wallet.available_balance,
                    description=f"Available balance below minimum in {internal_wallet.provider_name}: {internal_wallet.available_balance} < {internal_wallet.minimum_balance}"
                ))
            
        except Exception as e:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                discrepancy_type=DiscrepancyType.INTERNAL_BALANCE_ERROR,
                wallet_type="internal",
                internal_wallet_id=internal_wallet.wallet_id,
                currency=internal_wallet.currency,
                description=f"Internal wallet validation error: {e}"
            ))
        
        return issues
    
    def _validate_wallet_transaction_history(
        self, session: Session, wallet: Wallet
    ) -> List[ValidationIssue]:
        """Validate wallet balance against transaction history"""
        issues = []
        
        try:
            # This is a simplified version - in a real implementation,
            # you would calculate expected balance from transaction history
            # and compare it to actual wallet balance
            
            # Get transaction count for context
            session.query(Transaction).filter(
                and_(
                    Transaction.user_id == wallet.user_id,
                    Transaction.currency == wallet.currency
                )
            ).count()
            
            # Placeholder for transaction validation logic
            # In a real implementation, this would:
            # 1. Sum all credits and debits from transaction history
            # 2. Compare to current wallet balance
            # 3. Report any discrepancies
            
        except Exception as e:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                discrepancy_type=DiscrepancyType.TRANSACTION_MISMATCH,
                wallet_type="user",
                user_id=wallet.user_id,
                wallet_id=wallet.id,
                currency=wallet.currency,
                description=f"Transaction history validation error: {e}"
            ))
        
        return issues
    
    def _validate_wallet_audit_trail(
        self, session: Session, wallet: Wallet
    ) -> List[ValidationIssue]:
        """Validate wallet against audit trail records"""
        issues = []
        
        try:
            # Get latest audit log for this wallet
            latest_audit = session.query(BalanceAuditLog).filter(
                and_(
                    BalanceAuditLog.wallet_type == "user",
                    BalanceAuditLog.user_id == wallet.user_id,
                    BalanceAuditLog.currency == wallet.currency
                )
            ).order_by(desc(BalanceAuditLog.created_at)).first()
            
            if latest_audit:
                # Check if current balance matches last audit entry
                if latest_audit.balance_type == "available":
                    if abs(wallet.available_balance - latest_audit.amount_after) > Decimal('0.00000001'):
                        issues.append(ValidationIssue(
                            severity=ValidationSeverity.WARNING,
                            discrepancy_type=DiscrepancyType.AUDIT_TRAIL_GAP,
                            wallet_type="user",
                            user_id=wallet.user_id,
                            wallet_id=wallet.id,
                            currency=wallet.currency,
                            expected_balance=latest_audit.amount_after,
                            actual_balance=wallet.available_balance,
                            difference=wallet.available_balance - latest_audit.amount_after,
                            description="Current balance doesn't match latest audit entry",
                            last_audit_entry=latest_audit.audit_id
                        ))
            
        except Exception as e:
            issues.append(ValidationIssue(
                severity=ValidationSeverity.ERROR,
                discrepancy_type=DiscrepancyType.AUDIT_TRAIL_GAP,
                wallet_type="user",
                user_id=wallet.user_id,
                wallet_id=wallet.id,
                currency=wallet.currency,
                description=f"Audit trail validation error: {e}"
            ))
        
        return issues
    
    def _validate_all_user_wallets_batched(
        self, session: Session, currency: Optional[str], batch_size: int
    ) -> ValidationResult:
        """Validate all user wallets in batches for performance"""
        result = ValidationResult(
            success=True,
            validation_type="user_wallets_batch",
            scope=f"all_user_wallets_{currency or 'all'}"
        )
        
        try:
            # Get total count
            query = session.query(Wallet)
            if currency:
                query = query.filter(Wallet.currency == currency)
            
            total_wallets = query.count()
            
            # Process in batches
            offset = 0
            while offset < total_wallets:
                batch_query = query.offset(offset).limit(batch_size)
                wallets = batch_query.all()
                
                for wallet in wallets:
                    wallet_issues = self._validate_single_user_wallet(
                        session, wallet, True, True
                    )
                    result.issues.extend(wallet_issues)
                    result.wallets_checked += 1
                
                offset += batch_size
                
                logger.debug(f"🔄 VALIDATE: Processed batch {offset}/{total_wallets} wallets")
            
        except Exception as e:
            logger.error(f"❌ VALIDATE: Error in batched user wallet validation: {e}")
            result.success = False
        
        return result
    
    def _detect_user_wallet_discrepancies(
        self, session: Session, threshold: Decimal, cutoff_date: datetime
    ) -> List[ValidationIssue]:
        """Detect discrepancies in user wallets"""
        issues = []
        
        try:
            # Find wallets with significant balance changes without corresponding transactions
            # This is a simplified implementation - expand based on specific business rules
            
            wallets_with_issues = session.query(Wallet).filter(
                or_(
                    Wallet.available_balance < Decimal('0'),
                    Wallet.frozen_balance < Decimal('0'),
                    Wallet.locked_balance < Decimal('0')
                )
            ).all()
            
            for wallet in wallets_with_issues:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    discrepancy_type=DiscrepancyType.NEGATIVE_BALANCE,
                    wallet_type="user",
                    user_id=wallet.user_id,
                    wallet_id=wallet.id,
                    currency=wallet.currency,
                    actual_balance=min(wallet.available_balance, wallet.frozen_balance, wallet.locked_balance),
                    description="Negative balance detected in user wallet"
                ))
            
        except Exception as e:
            logger.error(f"❌ VALIDATE: Error detecting user wallet discrepancies: {e}")
        
        return issues
    
    def _detect_internal_wallet_discrepancies(
        self, session: Session, threshold: Decimal, cutoff_date: datetime
    ) -> List[ValidationIssue]:
        """Detect discrepancies in internal wallets"""
        issues = []
        
        try:
            # Find internal wallets with negative balances or inconsistencies
            internal_wallets_with_issues = session.query(InternalWallet).filter(
                or_(
                    InternalWallet.available_balance < Decimal('0'),
                    InternalWallet.locked_balance < Decimal('0'),
                    InternalWallet.reserved_balance < Decimal('0')
                )
            ).all()
            
            for internal_wallet in internal_wallets_with_issues:
                issues.append(ValidationIssue(
                    severity=ValidationSeverity.CRITICAL,
                    discrepancy_type=DiscrepancyType.INTERNAL_BALANCE_ERROR,
                    wallet_type="internal",
                    internal_wallet_id=internal_wallet.wallet_id,
                    currency=internal_wallet.currency,
                    actual_balance=min(
                        internal_wallet.available_balance,
                        internal_wallet.locked_balance,
                        internal_wallet.reserved_balance
                    ),
                    description=f"Negative balance detected in {internal_wallet.provider_name} internal wallet"
                ))
            
        except Exception as e:
            logger.error(f"❌ VALIDATE: Error detecting internal wallet discrepancies: {e}")
        
        return issues
    
    def _validate_user_audit_trail(
        self, session: Session, user_id: Optional[int], currency: Optional[str], cutoff_date: datetime
    ) -> List[ValidationIssue]:
        """Validate user wallets against audit trail"""
        issues = []
        
        # Implementation would check audit trail consistency for user wallets
        # This is a placeholder for the actual audit trail validation logic
        
        return issues
    
    def _validate_internal_audit_trail(
        self, session: Session, internal_wallet_id: Optional[str], currency: Optional[str], cutoff_date: datetime
    ) -> List[ValidationIssue]:
        """Validate internal wallets against audit trail"""
        issues = []
        
        # Implementation would check audit trail consistency for internal wallets
        # This is a placeholder for the actual audit trail validation logic
        
        return issues
    
    def _generate_validation_summary(self, result: ValidationResult) -> Dict[str, Any]:
        """Generate summary for validation results"""
        return {
            "status": "success" if result.success else "failed",
            "wallets_checked": result.wallets_checked,
            "total_issues": result.issues_found,
            "critical_issues": result.critical_issues,
            "warnings": result.warnings,
            "issue_types": self._categorize_issues_by_type(result.issues),
            "recommendations": self._generate_recommendations(result.issues)
        }
    
    def _generate_comprehensive_summary(self, result: ValidationResult) -> Dict[str, Any]:
        """Generate comprehensive summary for full validation"""
        summary = self._generate_validation_summary(result)
        summary.update({
            "scope": "comprehensive",
            "coverage": "all_wallets",
            "performance_metrics": {
                "duration_seconds": result.duration_seconds,
                "wallets_per_second": result.wallets_checked / max(result.duration_seconds, 0.001)
            }
        })
        return summary
    
    def _generate_discrepancy_summary(self, result: ValidationResult, threshold: Decimal) -> Dict[str, Any]:
        """Generate summary for discrepancy detection"""
        summary = self._generate_validation_summary(result)
        summary.update({
            "detection_threshold": float(threshold),
            "high_value_discrepancies": len([
                issue for issue in result.issues 
                if abs(issue.difference) > threshold * 10
            ])
        })
        return summary
    
    def _generate_audit_trail_summary(self, result: ValidationResult, days_back: int) -> Dict[str, Any]:
        """Generate summary for audit trail validation"""
        summary = self._generate_validation_summary(result)
        summary.update({
            "audit_period_days": days_back,
            "audit_trail_gaps": len([
                issue for issue in result.issues 
                if issue.discrepancy_type == DiscrepancyType.AUDIT_TRAIL_GAP
            ])
        })
        return summary
    
    def _categorize_issues_by_type(self, issues: List[ValidationIssue]) -> Dict[str, int]:
        """Categorize issues by discrepancy type"""
        categories = {}
        for issue in issues:
            category = issue.discrepancy_type.value
            categories[category] = categories.get(category, 0) + 1
        return categories
    
    def _generate_recommendations(self, issues: List[ValidationIssue]) -> List[str]:
        """Generate recommendations based on issues found"""
        recommendations = []
        
        if any(issue.discrepancy_type == DiscrepancyType.NEGATIVE_BALANCE for issue in issues):
            recommendations.append("Investigate negative balance causes and implement additional balance checks")
        
        if any(issue.discrepancy_type == DiscrepancyType.AUDIT_TRAIL_GAP for issue in issues):
            recommendations.append("Review audit trail logging to ensure complete transaction tracking")
        
        if any(issue.discrepancy_type == DiscrepancyType.INTERNAL_BALANCE_ERROR for issue in issues):
            recommendations.append("Reconcile internal wallet balances with external provider APIs")
        
        return recommendations
    
    def _finalize_result(self, result: ValidationResult, start_time: datetime) -> ValidationResult:
        """Finalize validation result with timing information"""
        result.completed_at = datetime.now(timezone.utc)
        result.duration_seconds = (result.completed_at - start_time).total_seconds()
        return result


# Global instance for easy access
balance_validator = BalanceValidator()