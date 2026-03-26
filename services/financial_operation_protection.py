#!/usr/bin/env python3
"""
Financial Operation Protection Middleware
Provides comprehensive protection for all financial operations by checking balances,
applying safety limits, and preventing operations when balances are insufficient.
"""

import logging
import json
from decimal import Decimal
from typing import Dict, Any, Optional, Callable
from functools import wraps
from dataclasses import dataclass
from enum import Enum

# Define classes locally to avoid circular imports
class AlertLevel(Enum):
    """Alert severity levels for balance monitoring"""
    WARNING = "warning"  # 75% of threshold
    CRITICAL = "critical"  # 50% of threshold  
    EMERGENCY = "emergency"  # 25% of threshold
    OPERATIONAL_DANGER = "operational_danger"  # Below minimum operational threshold

@dataclass
class ProtectionStatus:
    """Protection status for a financial operation"""
    operation_allowed: bool
    alert_level: Optional[AlertLevel]
    balance_check_passed: bool
    insufficient_services: list
    warning_message: Optional[str] = None
    blocking_reason: Optional[str] = None
from database import SessionLocal
from sqlalchemy import text

logger = logging.getLogger(__name__)


class FinancialOperationProtectionError(Exception):
    """Exception raised when a financial operation is blocked by protection system"""
    
    def __init__(self, message: str, protection_status: ProtectionStatus, operation_details: Dict[str, Any]):
        self.protection_status = protection_status
        self.operation_details = operation_details
        super().__init__(message)


class FinancialOperationProtection:
    """Middleware for protecting financial operations with comprehensive balance and safety checks"""
    
    def __init__(self):
        self.protection_service = None  # Will be initialized when needed
    
    async def check_operation_safety(
        self,
        operation_type: str,
        currency: str,
        amount: Decimal,
        user_id: Optional[int] = None,
        additional_context: Optional[Dict] = None
    ) -> ProtectionStatus:
        """
        Comprehensive safety check for financial operations
        
        Args:
            operation_type: Type of operation (cashout, withdrawal, transfer, exchange)
            currency: Currency code (NGN, BTC, ETH, etc.)
            amount: Amount being processed
            user_id: User ID if applicable
            additional_context: Additional context for the operation
            
        Returns:
            ProtectionStatus with safety assessment
        """
        logger.info(f"🛡️ FINANCIAL_PROTECTION_CHECK: {operation_type} {amount} {currency} user:{user_id}")
        
        # Lazy load protection service to avoid circular imports
        if self.protection_service is None:
            from services.balance_guard import check_operation_protection
            self.protection_service = check_operation_protection
        
        # Get protection status from BalanceGuard unified system
        protection_status = await self.protection_service(
            operation_type=operation_type,
            currency=currency,
            amount=amount
        )
        
        # Log the protection check for audit
        await self._log_protection_check(
            operation_type=operation_type,
            currency=currency,
            amount=amount,
            user_id=user_id,
            protection_status=protection_status,
            additional_context=additional_context or {}
        )
        
        return protection_status
    
    async def _log_protection_check(
        self,
        operation_type: str,
        currency: str,
        amount: Decimal,
        user_id: Optional[int],
        protection_status: ProtectionStatus,
        additional_context: Dict
    ) -> None:
        """Log protection check for audit trail"""
        try:
            # CRITICAL FIX: Extract balance info from cached protection_status instead of redundant API calls
            fincra_balance = None
            kraken_balances = {}
            
            # Extract balance data from protection snapshots (no redundant API calls)
            try:
                for snapshot in protection_status.balance_snapshots:
                    if snapshot.provider == "fincra" and snapshot.currency == "NGN":
                        fincra_balance = float(snapshot.balance)
                        logger.info(f"📊 CACHED_BALANCE_LOGGING: Using Fincra ₦{fincra_balance:,.2f} from protection check")
                    elif snapshot.provider == "kraken":
                        # For Kraken, we log the USD-equivalent total from the snapshot
                        kraken_balances["USD_EQUIVALENT"] = float(snapshot.balance)
                        logger.info(f"📊 CACHED_BALANCE_LOGGING: Using Kraken ${float(snapshot.balance):,.2f} USD equivalent from protection check")
                
                # If no cached balance data available, use fallback (should be rare)
                if fincra_balance is None or not kraken_balances:
                    logger.info("📊 FALLBACK_BALANCE_LOGGING: Using cached balance fallback - protection check may have skipped balance fetches")
                    
                    # Only fetch if we absolutely have no balance data (admin override scenarios)
                    if not protection_status.balance_snapshots:
                        logger.info("🔄 ADMIN_OVERRIDE_DETECTED: Skipping balance fetch for logging - admin override active")
                        # Use placeholder values to indicate admin override
                        fincra_balance = -1.0  # Sentinel value for admin override
                        kraken_balances = {"ADMIN_OVERRIDE": -1.0}
                            
            except Exception as e:
                logger.warning(f"Could not extract cached balances for protection log: {e}")
                # Use sentinel values to indicate data extraction failed
                fincra_balance = -2.0  # Sentinel for extraction error
                kraken_balances = {"EXTRACTION_ERROR": -2.0}
            
            # Insert protection log
            with SessionLocal() as session:
                session.execute(
                    text("""
                    INSERT INTO balance_protection_logs (
                        operation_type, currency, amount, user_id, operation_allowed,
                        alert_level, balance_check_passed, insufficient_services,
                        warning_message, blocking_reason, fincra_balance, kraken_balances
                    ) VALUES (
                        :operation_type, :currency, :amount, :user_id, :operation_allowed,
                        :alert_level, :balance_check_passed, :insufficient_services,
                        :warning_message, :blocking_reason, :fincra_balance, :kraken_balances
                    )
                    """),
                    {
                        'operation_type': operation_type,
                        'currency': currency,
                        'amount': float(amount),
                        'user_id': user_id,
                        'operation_allowed': protection_status.operation_allowed,
                        'alert_level': protection_status.alert_level.value if protection_status.alert_level else None,
                        'balance_check_passed': protection_status.balance_check_passed,
                        'insufficient_services': protection_status.insufficient_services if protection_status.insufficient_services else None,
                        'warning_message': protection_status.warning_message,
                        'blocking_reason': protection_status.blocking_reason,
                        'fincra_balance': fincra_balance,
                        'kraken_balances': json.dumps(kraken_balances) if kraken_balances else None
                    }
                )
                session.commit()
                
        except Exception as e:
            logger.error(f"Failed to log protection check: {e}")


def require_balance_protection(
    operation_type: str,
    currency_param: str = "currency",
    amount_param: str = "amount",
    user_id_param: Optional[str] = None
):
    """
    Decorator to require balance protection for financial operations
    
    Args:
        operation_type: Type of operation (e.g., "ngn_cashout", "crypto_withdrawal")
        currency_param: Parameter name containing currency
        amount_param: Parameter name containing amount
        user_id_param: Parameter name containing user_id (optional)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract parameters for protection check
            currency = kwargs.get(currency_param)
            amount = kwargs.get(amount_param)
            user_id = kwargs.get(user_id_param) if user_id_param else None
            
            # Convert amount to Decimal if needed
            if amount is not None:
                amount = Decimal(str(amount))
            
            if currency is None or amount is None:
                logger.warning(f"Balance protection skipped for {func.__name__}: currency={currency}, amount={amount}")
                return await func(*args, **kwargs)
            
            # Create protection instance and check
            protection = FinancialOperationProtection()
            protection_status = await protection.check_operation_safety(
                operation_type=operation_type,
                currency=currency,
                amount=amount,
                user_id=user_id,
                additional_context={
                    'function': func.__name__,
                    'args_count': len(args),
                    'kwargs_keys': list(kwargs.keys())
                }
            )
            
            # CRITICAL FINANCIAL PROTECTION: Block operations when protection_status.operation_allowed = False
            if not protection_status.operation_allowed:
                # SECURITY: Block unsafe financial operations to prevent losses
                error_message = f"🚫 FINANCIAL_OPERATION_BLOCKED: {operation_type.upper()} blocked by balance protection"
                
                # Log the blocked operation for audit
                logger.error(
                    f"{error_message} | {currency} {amount} | User: {user_id} | "
                    f"Reason: {protection_status.blocking_reason}"
                )
                
                # Raise exception to actually block the operation
                raise FinancialOperationProtectionError(
                    message=f"Operation blocked: {protection_status.blocking_reason}",
                    protection_status=protection_status,
                    operation_details={
                        'operation_type': operation_type,
                        'currency': currency,
                        'amount': float(amount),
                        'user_id': user_id,
                        'function': func.__name__
                    }
                )
            
            # Log successful protection check for allowed operations
            if protection_status.warning_message:
                logger.warning(f"⚠️ BALANCE_WARNING: {protection_status.warning_message} | {operation_type} {amount} {currency} | Operation proceeding")
            
            logger.info(f"✅ FINANCIAL_PROTECTION_PASSED: {operation_type} {amount} {currency} | User: {user_id}")
            
            # Proceed with original operation only when protection allows
            return await func(*args, **kwargs)
            
        return wrapper
    return decorator


# Create singleton instance
financial_operation_protection = FinancialOperationProtection()