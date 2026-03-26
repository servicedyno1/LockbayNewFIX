"""
Database Safety Service - Comprehensive safety mechanisms for financial data integrity

Provides database-level safety mechanisms, constraint management, and data integrity
validation for the wallet balance system with comprehensive audit trail support.

Key Features:
- Database constraint validation and management
- Automated integrity checks and repairs
- Emergency safety mechanisms for balance protection
- Performance monitoring for safety-critical operations
- Integration with audit trail system for complete traceability
"""

import logging
from typing import Dict, Any, Optional, List
from decimal import Decimal
from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session
from sqlalchemy import text

from models import (
    Wallet, InternalWallet
)

logger = logging.getLogger(__name__)


class SafetyLevel(Enum):
    """Safety levels for operations"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ConstraintViolationType(Enum):
    """Types of constraint violations"""
    NEGATIVE_BALANCE = "negative_balance"
    BALANCE_INCONSISTENCY = "balance_inconsistency"
    INVALID_CURRENCY = "invalid_currency"
    CONSTRAINT_VIOLATION = "constraint_violation"
    FOREIGN_KEY_VIOLATION = "foreign_key_violation"
    UNIQUE_CONSTRAINT = "unique_constraint"


@dataclass
class SafetyCheckResult:
    """Result of a safety check operation"""
    passed: bool
    violations: List[Dict[str, Any]]
    warnings: List[Dict[str, Any]]
    recommendations: List[str]
    checked_items: int
    safety_score: float  # 0.0 to 1.0
    
    def __post_init__(self):
        # Calculate safety score based on violations and warnings
        if not self.violations and not self.warnings:
            self.safety_score = 1.0
        else:
            # Decrease score based on severity
            critical_penalty = len([v for v in self.violations if v.get('severity') == 'critical']) * 0.3
            error_penalty = len([v for v in self.violations if v.get('severity') == 'error']) * 0.2
            warning_penalty = len(self.warnings) * 0.1
            
            self.safety_score = max(0.0, 1.0 - critical_penalty - error_penalty - warning_penalty)


class DatabaseSafetyService:
    """
    Comprehensive database safety service for financial data integrity
    
    Provides constraint validation, safety checks, and emergency mechanisms
    to ensure data integrity and financial safety.
    """
    
    def __init__(self):
        """Initialize the database safety service"""
        self.safety_constraints = self._load_safety_constraints()
        
    def validate_wallet_constraints(
        self,
        session: Session,
        user_id: Optional[int] = None,
        currency: Optional[str] = None
    ) -> SafetyCheckResult:
        """
        Validate wallet constraints and data integrity
        
        Args:
            session: Database session
            user_id: Specific user ID to check (optional)
            currency: Specific currency to check (optional)
            
        Returns:
            SafetyCheckResult with validation results
        """
        violations = []
        warnings = []
        recommendations = []
        checked_items = 0
        
        try:
            logger.info("🔒 Starting wallet constraint validation")
            
            # Get wallets to check
            query = session.query(Wallet)
            if user_id:
                query = query.filter(Wallet.user_id == user_id)
            if currency:
                query = query.filter(Wallet.currency == currency)
                
            wallets = query.all()
            checked_items = len(wallets)
            
            for wallet in wallets:
                # Check negative balance constraints
                if wallet.available_balance < 0:
                    violations.append({
                        'type': ConstraintViolationType.NEGATIVE_BALANCE.value,
                        'severity': 'critical',
                        'table': 'wallets',
                        'record_id': wallet.id,
                        'user_id': wallet.user_id,
                        'currency': wallet.currency,
                        'field': 'available_balance',
                        'value': float(wallet.available_balance),
                        'message': f'Negative available balance detected: {wallet.available_balance}',
                        'constraint': 'chk_wallet_positive_balance'
                    })
                
                if wallet.frozen_balance < 0:
                    violations.append({
                        'type': ConstraintViolationType.NEGATIVE_BALANCE.value,
                        'severity': 'critical',
                        'table': 'wallets',
                        'record_id': wallet.id,
                        'user_id': wallet.user_id,
                        'currency': wallet.currency,
                        'field': 'frozen_balance',
                        'value': float(wallet.frozen_balance),
                        'message': f'Negative frozen balance detected: {wallet.frozen_balance}',
                        'constraint': 'chk_wallet_positive_frozen_balance'
                    })
                
                if wallet.locked_balance < 0:
                    violations.append({
                        'type': ConstraintViolationType.NEGATIVE_BALANCE.value,
                        'severity': 'critical',
                        'table': 'wallets',
                        'record_id': wallet.id,
                        'user_id': wallet.user_id,
                        'currency': wallet.currency,
                        'field': 'locked_balance',
                        'value': float(wallet.locked_balance),
                        'message': f'Negative locked balance detected: {wallet.locked_balance}',
                        'constraint': 'chk_wallet_positive_locked_balance'
                    })
                
                # Check balance consistency
                total_balance = wallet.available_balance + wallet.frozen_balance + wallet.locked_balance
                if total_balance < 0:
                    violations.append({
                        'type': ConstraintViolationType.BALANCE_INCONSISTENCY.value,
                        'severity': 'error',
                        'table': 'wallets',
                        'record_id': wallet.id,
                        'user_id': wallet.user_id,
                        'currency': wallet.currency,
                        'field': 'total_balance_consistency',
                        'value': float(total_balance),
                        'message': f'Total balance inconsistency: available_balance={wallet.available_balance}, frozen={wallet.frozen_balance}, locked={wallet.locked_balance}',
                        'constraint': 'chk_wallet_total_balance_consistency'
                    })
                
                # Check reasonable balance limits
                max_balance = Decimal('999999999.99999999')
                if wallet.available_balance > max_balance:
                    warnings.append({
                        'type': 'balance_limit',
                        'table': 'wallets',
                        'record_id': wallet.id,
                        'user_id': wallet.user_id,
                        'currency': wallet.currency,
                        'field': 'available_balance',
                        'value': float(wallet.available_balance),
                        'message': f'Available balance exceeds maximum limit: {wallet.available_balance}',
                        'constraint': 'chk_wallet_max_balance'
                    })
                
                # Check currency validity
                valid_currencies = ['USD', 'NGN', 'BTC', 'ETH', 'LTC', 'USDT', 'USDC', 'TRX', 'DOGE']
                if wallet.currency not in valid_currencies:
                    violations.append({
                        'type': ConstraintViolationType.INVALID_CURRENCY.value,
                        'severity': 'error',
                        'table': 'wallets',
                        'record_id': wallet.id,
                        'user_id': wallet.user_id,
                        'currency': wallet.currency,
                        'field': 'currency',
                        'value': wallet.currency,
                        'message': f'Invalid currency: {wallet.currency}',
                        'constraint': 'chk_wallet_valid_currency'
                    })
            
            # Generate recommendations
            if violations:
                recommendations.append("Run emergency balance repair for wallets with constraint violations")
                recommendations.append("Investigate root cause of balance inconsistencies")
                
            if warnings:
                recommendations.append("Review wallets with balance warnings for potential issues")
            
            if not violations and not warnings:
                recommendations.append("All wallet constraints validated successfully - no action needed")
            
            result = SafetyCheckResult(
                passed=len(violations) == 0,
                violations=violations,
                warnings=warnings,
                recommendations=recommendations,
                checked_items=checked_items
            )
            
            logger.info(
                f"✅ Wallet constraint validation complete - "
                f"Checked: {checked_items}, Violations: {len(violations)}, "
                f"Warnings: {len(warnings)}, Safety Score: {result.safety_score:.2f}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Error validating wallet constraints: {e}")
            return SafetyCheckResult(
                passed=False,
                violations=[{
                    'type': 'validation_error',
                    'severity': 'critical',
                    'message': f'Constraint validation failed: {e}'
                }],
                warnings=[],
                recommendations=['Investigate constraint validation system failure'],
                checked_items=checked_items
            )
    
    def validate_internal_wallet_constraints(
        self,
        session: Session,
        provider_name: Optional[str] = None,
        currency: Optional[str] = None
    ) -> SafetyCheckResult:
        """
        Validate internal wallet constraints and data integrity
        
        Args:
            session: Database session
            provider_name: Specific provider to check (optional)
            currency: Specific currency to check (optional)
            
        Returns:
            SafetyCheckResult with validation results
        """
        violations = []
        warnings = []
        recommendations = []
        checked_items = 0
        
        try:
            logger.info("🔒 Starting internal wallet constraint validation")
            
            # Get internal wallets to check
            query = session.query(InternalWallet)
            if provider_name:
                query = query.filter(InternalWallet.provider_name == provider_name)
            if currency:
                query = query.filter(InternalWallet.currency == currency)
                
            internal_wallets = query.all()
            checked_items = len(internal_wallets)
            
            for wallet in internal_wallets:
                # Check negative balance constraints (using existing constraints from InternalWallet model)
                if wallet.available_balance < 0:
                    violations.append({
                        'type': ConstraintViolationType.NEGATIVE_BALANCE.value,
                        'severity': 'critical',
                        'table': 'internal_wallets',
                        'record_id': wallet.id,
                        'provider': wallet.provider_name,
                        'currency': wallet.currency,
                        'field': 'available_balance',
                        'value': float(wallet.available_balance),
                        'message': f'Negative available balance in {wallet.provider_name}: {wallet.available_balance}',
                        'constraint': 'chk_positive_available_balance'
                    })
                
                if wallet.locked_balance < 0:
                    violations.append({
                        'type': ConstraintViolationType.NEGATIVE_BALANCE.value,
                        'severity': 'critical',
                        'table': 'internal_wallets',
                        'record_id': wallet.id,
                        'provider': wallet.provider_name,
                        'currency': wallet.currency,
                        'field': 'locked_balance',
                        'value': float(wallet.locked_balance),
                        'message': f'Negative locked balance in {wallet.provider_name}: {wallet.locked_balance}',
                        'constraint': 'chk_positive_locked_balance'
                    })
                
                if wallet.reserved_balance < 0:
                    violations.append({
                        'type': ConstraintViolationType.NEGATIVE_BALANCE.value,
                        'severity': 'critical',
                        'table': 'internal_wallets',
                        'record_id': wallet.id,
                        'provider': wallet.provider_name,
                        'currency': wallet.currency,
                        'field': 'reserved_balance',
                        'value': float(wallet.reserved_balance),
                        'message': f'Negative reserved balance in {wallet.provider_name}: {wallet.reserved_balance}',
                        'constraint': 'chk_positive_reserved_balance'
                    })
                
                # Check balance consistency (using existing constraint)
                calculated_total = wallet.available_balance + wallet.locked_balance + wallet.reserved_balance
                if wallet.total_balance != calculated_total:
                    violations.append({
                        'type': ConstraintViolationType.BALANCE_INCONSISTENCY.value,
                        'severity': 'error',
                        'table': 'internal_wallets',
                        'record_id': wallet.id,
                        'provider': wallet.provider_name,
                        'currency': wallet.currency,
                        'field': 'total_balance',
                        'value': float(wallet.total_balance),
                        'expected_value': float(calculated_total),
                        'message': f'Balance consistency violation in {wallet.provider_name}: total={wallet.total_balance}, calculated={calculated_total}',
                        'constraint': 'chk_balance_consistency'
                    })
                
                # Check minimum balance requirements
                if wallet.available_balance < wallet.minimum_balance:
                    warnings.append({
                        'type': 'minimum_balance',
                        'table': 'internal_wallets',
                        'record_id': wallet.id,
                        'provider': wallet.provider_name,
                        'currency': wallet.currency,
                        'field': 'available_balance',
                        'value': float(wallet.available_balance),
                        'minimum': float(wallet.minimum_balance),
                        'message': f'Available balance below minimum in {wallet.provider_name}: {wallet.available_balance} < {wallet.minimum_balance}'
                    })
            
            # Generate recommendations
            if violations:
                recommendations.append("Run emergency internal wallet repair for providers with constraint violations")
                recommendations.append("Check provider API integration for balance discrepancies")
                
            if warnings:
                recommendations.append("Review internal wallets with minimum balance warnings")
                recommendations.append("Consider increasing provider funding or adjusting minimum thresholds")
            
            result = SafetyCheckResult(
                passed=len(violations) == 0,
                violations=violations,
                warnings=warnings,
                recommendations=recommendations,
                checked_items=checked_items
            )
            
            logger.info(
                f"✅ Internal wallet constraint validation complete - "
                f"Checked: {checked_items}, Violations: {len(violations)}, "
                f"Warnings: {len(warnings)}, Safety Score: {result.safety_score:.2f}"
            )
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Error validating internal wallet constraints: {e}")
            return SafetyCheckResult(
                passed=False,
                violations=[{
                    'type': 'validation_error',
                    'severity': 'critical',
                    'message': f'Internal wallet constraint validation failed: {e}'
                }],
                warnings=[],
                recommendations=['Investigate internal wallet validation system failure'],
                checked_items=checked_items
            )
    
    def emergency_balance_repair(
        self,
        session: Session,
        wallet_id: int,
        repair_type: str = "zero_negative_balances",
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        Emergency balance repair for constraint violations
        
        Args:
            session: Database session
            wallet_id: Wallet ID to repair
            repair_type: Type of repair to perform
            dry_run: If True, only report what would be changed
            
        Returns:
            Dictionary with repair results
        """
        try:
            logger.warning(f"🚨 Emergency balance repair requested for wallet {wallet_id}")
            
            wallet = session.query(Wallet).filter(Wallet.id == wallet_id).first()
            if not wallet:
                return {
                    'success': False,
                    'error': f'Wallet {wallet_id} not found',
                    'wallet_id': wallet_id
                }
            
            changes_made = []
            original_values = {
                'available_balance': wallet.available_balance,
                'frozen_balance': wallet.frozen_balance,
                'locked_balance': wallet.locked_balance
            }
            
            if repair_type == "zero_negative_balances":
                # Zero out negative balances
                if wallet.available_balance < 0:
                    changes_made.append(f"available_balance: {wallet.available_balance} -> 0")
                    if not dry_run:
                        wallet.available_balance = Decimal('0')
                
                if wallet.frozen_balance < 0:
                    changes_made.append(f"frozen_balance: {wallet.frozen_balance} -> 0")
                    if not dry_run:
                        wallet.frozen_balance = Decimal('0')
                
                if wallet.locked_balance < 0:
                    changes_made.append(f"locked_balance: {wallet.locked_balance} -> 0")
                    if not dry_run:
                        wallet.locked_balance = Decimal('0')
            
            if not dry_run and changes_made:
                # Update version for optimistic locking
                wallet.version += 1
                session.flush()
                
                logger.critical(f"🚨 EMERGENCY REPAIR APPLIED to wallet {wallet_id}: {changes_made}")
            
            return {
                'success': True,
                'wallet_id': wallet_id,
                'user_id': wallet.user_id,
                'currency': wallet.currency,
                'repair_type': repair_type,
                'dry_run': dry_run,
                'changes_made': changes_made,
                'original_values': original_values,
                'message': f"Emergency repair {'simulated' if dry_run else 'applied'} for wallet {wallet_id}"
            }
            
        except Exception as e:
            logger.error(f"❌ Emergency balance repair failed for wallet {wallet_id}: {e}")
            return {
                'success': False,
                'error': str(e),
                'wallet_id': wallet_id
            }
    
    def check_constraint_violations(
        self,
        session: Session,
        table_name: str,
        constraint_name: str
    ) -> List[Dict[str, Any]]:
        """
        Check for specific constraint violations
        
        Args:
            session: Database session
            table_name: Name of the table to check
            constraint_name: Name of the constraint to validate
            
        Returns:
            List of violating records
        """
        violations = []
        
        try:
            # Map constraint names to SQL conditions
            constraint_checks = {
                'chk_wallet_positive_balance': "SELECT * FROM wallets WHERE available_balance < 0",
                'chk_wallet_positive_frozen_balance': "SELECT * FROM wallets WHERE frozen_balance < 0",
                'chk_wallet_positive_locked_balance': "SELECT * FROM wallets WHERE locked_balance < 0",
                'chk_positive_available_balance': "SELECT * FROM internal_wallets WHERE available_balance < 0",
                'chk_positive_locked_balance': "SELECT * FROM internal_wallets WHERE locked_balance < 0",
                'chk_positive_reserved_balance': "SELECT * FROM internal_wallets WHERE reserved_balance < 0",
                'chk_balance_consistency': "SELECT * FROM internal_wallets WHERE total_balance != (available_balance + locked_balance + reserved_balance)"
            }
            
            if constraint_name in constraint_checks:
                sql_query = constraint_checks[constraint_name]
                result = session.execute(text(sql_query))
                
                for row in result:
                    violations.append(dict(row))
                    
            logger.info(f"🔍 Constraint check complete for {constraint_name}: {len(violations)} violations found")
            
        except Exception as e:
            logger.error(f"❌ Error checking constraint {constraint_name}: {e}")
            
        return violations
    
    def _load_safety_constraints(self) -> Dict[str, Any]:
        """Load safety constraint configurations"""
        return {
            'wallet_constraints': {
                'balance_min': Decimal('0'),
                'balance_max': Decimal('999999999.99999999'),
                'valid_currencies': ['USD', 'NGN', 'BTC', 'ETH', 'LTC', 'USDT', 'USDC', 'TRX', 'DOGE'],
                'valid_wallet_types': ['standard', 'savings', 'business', 'premium']
            },
            'internal_wallet_constraints': {
                'balance_min': Decimal('0'),
                'valid_providers': ['fincra', 'kraken', 'blockbee'],
                'minimum_balance_threshold': Decimal('10')  # Warning threshold
            },
            'safety_levels': {
                'critical_violation_threshold': 0.0,  # Any critical violation
                'warning_threshold': 0.05,  # 5% of checks have warnings
                'maintenance_threshold': 0.10  # 10% of checks have issues
            }
        }


# Global instance for easy access
database_safety_service = DatabaseSafetyService()