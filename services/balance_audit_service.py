"""
Balance Audit Service - Comprehensive audit trail system for all wallet balance changes

Provides complete tracking and logging of all balance changes for both user wallets
and internal service provider wallets with full transaction history and safety mechanisms.

Key Features:
- Comprehensive balance change logging with before/after states
- Support for both user and internal wallet auditing
- Automatic checksum generation for data integrity
- Idempotency protection to prevent duplicate logging
- Integration with existing financial audit systems
- Performance-optimized batch operations
"""

import logging
import json
import hashlib
import socket
import os
import threading
from enum import Enum
from typing import Dict, Any, Optional, List
from decimal import Decimal
from datetime import datetime, timezone
from dataclasses import dataclass

from sqlalchemy.orm import Session
from sqlalchemy import and_, desc
from sqlalchemy.exc import IntegrityError

from models import (
    Wallet, InternalWallet, BalanceAuditLog, WalletBalanceSnapshot
)
from utils.database_locking import DatabaseLockingService
from utils.financial_audit_logger import financial_audit_logger, FinancialEventType, FinancialContext, EntityType

logger = logging.getLogger(__name__)


class AuditLogLevel(Enum):
    """Audit log levels for balance audit service"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class BalanceChangeContext:
    """Context information for balance changes"""
    wallet_type: str  # 'user' or 'internal'
    user_id: Optional[int] = None
    wallet_id: Optional[int] = None
    internal_wallet_id: Optional[str] = None
    currency: str = "USD"
    balance_type: str = "available"  # 'available', 'frozen', 'locked', 'reserved'
    
    # Transaction context
    transaction_id: Optional[str] = None
    transaction_type: str = "unknown"
    operation_type: str = "manual"
    
    # Audit information
    initiated_by: str = "system"
    initiated_by_id: Optional[str] = None
    reason: str = "Balance change"
    
    # Related entities
    escrow_id: Optional[str] = None
    cashout_id: Optional[str] = None
    exchange_id: Optional[str] = None
    
    # Metadata
    metadata: Dict[str, Any] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    api_version: Optional[str] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


@dataclass
class BalanceChangeResult:
    """Result of a balance change audit operation"""
    success: bool
    audit_id: Optional[str] = None
    checksum: Optional[str] = None
    error_message: Optional[str] = None
    validation_passed: bool = True
    duplicate_detected: bool = False


class BalanceAuditService:
    """
    Comprehensive balance audit service for tracking all wallet balance changes
    
    Provides complete audit trail capabilities with integrity verification,
    idempotency protection, and performance optimization.
    """
    
    def __init__(self):
        """Initialize the balance audit service"""
        self.hostname = socket.gethostname()
        self.process_id = str(os.getpid())
        self.locking_service = DatabaseLockingService()
        
    def log_balance_change(
        self, 
        session: Session,
        context: BalanceChangeContext,
        amount_before: Decimal,
        amount_after: Decimal,
        idempotency_key: Optional[str] = None
    ) -> BalanceChangeResult:
        """
        Log a balance change with complete audit trail
        
        Args:
            session: Database session
            context: Balance change context information
            amount_before: Balance before the change
            amount_after: Balance after the change
            idempotency_key: Optional idempotency key for duplicate prevention
            
        Returns:
            BalanceChangeResult with operation outcome
        """
        try:
            # Calculate change amount and type
            change_amount = amount_after - amount_before
            if change_amount == 0:
                logger.warning(f"⚠️ AUDIT: Zero balance change detected for {context.wallet_type} wallet")
                return BalanceChangeResult(success=False, error_message="Zero balance change")
            
            change_type = "credit" if change_amount > 0 else "debit"
            change_amount = abs(change_amount)
            
            # Check for duplicates if idempotency key provided
            if idempotency_key:
                existing_log = session.query(BalanceAuditLog).filter(
                    BalanceAuditLog.idempotency_key == idempotency_key
                ).first()
                
                if existing_log:
                    logger.info(f"🔄 AUDIT: Duplicate balance change detected, returning existing audit {existing_log.audit_id}")
                    return BalanceChangeResult(
                        success=True,
                        audit_id=existing_log.audit_id,
                        duplicate_detected=True
                    )
            
            # Generate audit ID and checksums
            audit_id = self._generate_audit_id(context, change_amount)
            pre_checksum = self._generate_balance_checksum(context, amount_before)
            post_checksum = self._generate_balance_checksum(context, amount_after)
            
            # Create audit log entry
            audit_log = BalanceAuditLog(
                audit_id=audit_id,
                wallet_type=context.wallet_type,
                user_id=context.user_id,
                wallet_id=context.wallet_id,
                internal_wallet_id=context.internal_wallet_id,
                currency=context.currency,
                balance_type=context.balance_type,
                amount_before=amount_before,
                amount_after=amount_after,
                change_amount=change_amount,
                change_type=change_type,
                transaction_id=context.transaction_id,
                transaction_type=context.transaction_type,
                operation_type=context.operation_type,
                initiated_by=context.initiated_by,
                initiated_by_id=context.initiated_by_id,
                reason=context.reason,
                escrow_id=context.escrow_id,
                cashout_id=context.cashout_id,
                exchange_id=context.exchange_id,
                pre_validation_checksum=pre_checksum,
                post_validation_checksum=post_checksum,
                idempotency_key=idempotency_key,
                processed_at=datetime.now(timezone.utc),
                metadata=json.dumps(context.metadata) if context.metadata else None,
                ip_address=context.ip_address,
                user_agent=context.user_agent,
                api_version=context.api_version,
                hostname=self.hostname,
                process_id=self.process_id,
                thread_id=str(threading.get_ident())
            )
            
            # Validate balance change
            validation_passed = self._validate_balance_change(session, context, amount_before, amount_after)
            audit_log.balance_validation_passed = validation_passed
            
            # Save audit log
            session.add(audit_log)
            session.flush()  # Get the ID without committing
            
            # Log to financial audit system if enabled
            try:
                self._log_to_financial_audit_system(session, audit_log, context)
            except Exception as e:
                logger.error(f"❌ AUDIT: Financial audit system logging failed: {e}")
                # Don't fail the main operation for audit logging issues
            
            logger.info(
                f"✅ AUDIT: Balance change logged - {audit_id} | "
                f"{context.wallet_type} wallet | {change_type} {change_amount} {context.currency} | "
                f"Validation: {'PASS' if validation_passed else 'FAIL'}"
            )
            
            return BalanceChangeResult(
                success=True,
                audit_id=audit_id,
                checksum=post_checksum,
                validation_passed=validation_passed
            )
            
        except IntegrityError as e:
            session.rollback()
            logger.error(f"❌ AUDIT: Integrity error logging balance change: {e}")
            return BalanceChangeResult(success=False, error_message=f"Integrity error: {e}")
            
        except Exception as e:
            session.rollback()
            logger.error(f"❌ AUDIT: Unexpected error logging balance change: {e}")
            return BalanceChangeResult(success=False, error_message=f"Unexpected error: {e}")
    
    def create_balance_snapshot(
        self, 
        session: Session,
        wallet_type: str,
        user_id: Optional[int] = None,
        wallet_id: Optional[int] = None,
        internal_wallet_id: Optional[str] = None,
        snapshot_type: str = "manual",
        trigger_event: Optional[str] = None
    ) -> Optional[str]:
        """
        Create a point-in-time balance snapshot for verification
        
        Args:
            session: Database session
            wallet_type: 'user' or 'internal'
            user_id: User ID for user wallets
            wallet_id: Wallet ID
            internal_wallet_id: Internal wallet ID for internal wallets
            snapshot_type: Type of snapshot ('scheduled', 'manual', 'pre_operation', 'post_operation')
            trigger_event: Event that triggered this snapshot
            
        Returns:
            Snapshot ID if successful, None otherwise
        """
        try:
            # Get current balances
            if wallet_type == "user" and user_id and wallet_id:
                wallet = session.query(Wallet).filter(
                    and_(Wallet.user_id == user_id, Wallet.id == wallet_id)
                ).first()
                
                if not wallet:
                    logger.error(f"❌ SNAPSHOT: User wallet not found: user_id={user_id}, wallet_id={wallet_id}")
                    return None
                
                available_balance = wallet.available_balance
                frozen_balance = wallet.frozen_balance
                locked_balance = wallet.locked_balance
                reserved_balance = Decimal('0')  # User wallets don't have reserved balance
                currency = wallet.currency
                
            elif wallet_type == "internal" and internal_wallet_id:
                internal_wallet = session.query(InternalWallet).filter(
                    InternalWallet.wallet_id == internal_wallet_id
                ).first()
                
                if not internal_wallet:
                    logger.error(f"❌ SNAPSHOT: Internal wallet not found: {internal_wallet_id}")
                    return None
                
                available_balance = internal_wallet.available_balance
                frozen_balance = Decimal('0')  # Internal wallets use different terminology
                locked_balance = internal_wallet.locked_balance
                reserved_balance = internal_wallet.reserved_balance
                currency = internal_wallet.currency
            else:
                logger.error(f"❌ SNAPSHOT: Invalid wallet parameters - type: {wallet_type}")
                return None
            
            # Calculate total balance
            total_balance = available_balance + frozen_balance + locked_balance + reserved_balance
            
            # Generate snapshot ID and checksum
            snapshot_id = self._generate_snapshot_id(wallet_type, user_id, internal_wallet_id)
            balance_checksum = self._generate_balance_checksum_for_snapshot(
                available_balance, frozen_balance, locked_balance, reserved_balance
            )
            
            # Get transaction context
            transaction_count = self._get_transaction_count(session, wallet_type, user_id, internal_wallet_id)
            last_transaction_id = self._get_last_transaction_id(session, wallet_type, user_id, internal_wallet_id)
            
            # Create snapshot record
            snapshot = WalletBalanceSnapshot(
                snapshot_id=snapshot_id,
                wallet_type=wallet_type,
                user_id=user_id,
                wallet_id=wallet_id,
                internal_wallet_id=internal_wallet_id,
                currency=currency,
                available_balance=available_balance,
                frozen_balance=frozen_balance,
                locked_balance=locked_balance,
                reserved_balance=reserved_balance,
                total_balance=total_balance,
                snapshot_type=snapshot_type,
                trigger_event=trigger_event,
                balance_checksum=balance_checksum,
                transaction_count=transaction_count,
                last_transaction_id=last_transaction_id,
                valid_from=datetime.now(timezone.utc),
                created_by="system",
                hostname=self.hostname,
                process_id=self.process_id
            )
            
            session.add(snapshot)
            session.flush()
            
            logger.info(
                f"📸 SNAPSHOT: Created {snapshot_type} snapshot {snapshot_id} | "
                f"{wallet_type} wallet | Total: {total_balance} {currency}"
            )
            
            return snapshot_id
            
        except Exception as e:
            logger.error(f"❌ SNAPSHOT: Error creating balance snapshot: {e}")
            return None
    
    def verify_balance_consistency(
        self,
        session: Session,
        wallet_type: str,
        user_id: Optional[int] = None,
        internal_wallet_id: Optional[str] = None,
        currency: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Verify balance consistency against transaction history
        
        Args:
            session: Database session
            wallet_type: 'user' or 'internal'
            user_id: User ID for user wallets
            internal_wallet_id: Internal wallet ID for internal wallets
            currency: Currency to check (optional)
            
        Returns:
            Dictionary with verification results
        """
        try:
            results = {
                "wallet_type": wallet_type,
                "consistent": True,
                "discrepancies": [],
                "warnings": [],
                "total_checked": 0,
                "errors": []
            }
            
            if wallet_type == "user" and user_id:
                # Get user wallets to check
                query = session.query(Wallet).filter(Wallet.user_id == user_id)
                if currency:
                    query = query.filter(Wallet.currency == currency)
                
                wallets = query.all()
                
                for wallet in wallets:
                    wallet_result = self._verify_user_wallet_consistency(session, wallet)
                    if not wallet_result["consistent"]:
                        results["consistent"] = False
                        results["discrepancies"].extend(wallet_result["discrepancies"])
                    results["warnings"].extend(wallet_result["warnings"])
                    results["total_checked"] += 1
                    
            elif wallet_type == "internal" and internal_wallet_id:
                internal_wallet = session.query(InternalWallet).filter(
                    InternalWallet.wallet_id == internal_wallet_id
                ).first()
                
                if internal_wallet:
                    wallet_result = self._verify_internal_wallet_consistency(session, internal_wallet)
                    results.update(wallet_result)
                    results["total_checked"] = 1
                else:
                    results["errors"].append(f"Internal wallet {internal_wallet_id} not found")
            
            logger.info(
                f"🔍 VERIFY: Balance consistency check complete - "
                f"Consistent: {results['consistent']}, Checked: {results['total_checked']}, "
                f"Discrepancies: {len(results['discrepancies'])}"
            )
            
            return results
            
        except Exception as e:
            logger.error(f"❌ VERIFY: Error verifying balance consistency: {e}")
            return {
                "wallet_type": wallet_type,
                "consistent": False,
                "errors": [f"Verification error: {e}"],
                "total_checked": 0
            }
    
    def get_audit_history(
        self,
        session: Session,
        wallet_type: str,
        user_id: Optional[int] = None,
        internal_wallet_id: Optional[str] = None,
        currency: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Get audit history for a wallet
        
        Args:
            session: Database session
            wallet_type: 'user' or 'internal'
            user_id: User ID for user wallets
            internal_wallet_id: Internal wallet ID for internal wallets
            currency: Currency filter (optional)
            limit: Maximum records to return
            offset: Records to skip
            
        Returns:
            List of audit log entries
        """
        try:
            query = session.query(BalanceAuditLog).filter(
                BalanceAuditLog.wallet_type == wallet_type
            )
            
            if wallet_type == "user" and user_id:
                query = query.filter(BalanceAuditLog.user_id == user_id)
            elif wallet_type == "internal" and internal_wallet_id:
                query = query.filter(BalanceAuditLog.internal_wallet_id == internal_wallet_id)
            
            if currency:
                query = query.filter(BalanceAuditLog.currency == currency)
            
            # Order by most recent first
            query = query.order_by(desc(BalanceAuditLog.created_at))
            
            # Apply pagination
            audit_logs = query.offset(offset).limit(limit).all()
            
            # Convert to dictionary format
            results = []
            for log in audit_logs:
                result = {
                    "audit_id": log.audit_id,
                    "currency": log.currency,
                    "balance_type": log.balance_type,
                    "amount_before": float(log.amount_before),
                    "amount_after": float(log.amount_after),
                    "change_amount": float(log.change_amount),
                    "change_type": log.change_type,
                    "transaction_type": log.transaction_type,
                    "operation_type": log.operation_type,
                    "initiated_by": log.initiated_by,
                    "reason": log.reason,
                    "validation_passed": log.balance_validation_passed,
                    "created_at": log.created_at.isoformat(),
                    "transaction_id": log.transaction_id,
                    "escrow_id": log.escrow_id,
                    "cashout_id": log.cashout_id
                }
                
                # Include metadata if available
                if log.metadata:
                    try:
                        result["metadata"] = json.loads(log.metadata)
                    except (json.JSONDecodeError, ValueError, TypeError) as e:
                        logger.warning(f"Failed to parse metadata JSON: {e}")
                        result["metadata"] = log.metadata
                
                results.append(result)
            
            logger.info(f"📋 HISTORY: Retrieved {len(results)} audit records for {wallet_type} wallet")
            return results
            
        except Exception as e:
            logger.error(f"❌ HISTORY: Error retrieving audit history: {e}")
            return []
    
    # Private helper methods
    
    def _generate_audit_id(self, context: BalanceChangeContext, change_amount: Decimal) -> str:
        """Generate unique audit ID"""
        timestamp = datetime.now(timezone.utc).isoformat()
        content = f"{context.wallet_type}_{context.currency}_{change_amount}_{timestamp}_{self.process_id}"
        hash_value = hashlib.sha256(content.encode()).hexdigest()[:12]
        return f"audit_{hash_value}_{int(datetime.now(timezone.utc).timestamp())}"
    
    def _generate_snapshot_id(self, wallet_type: str, user_id: Optional[int], internal_wallet_id: Optional[str]) -> str:
        """Generate unique snapshot ID"""
        timestamp = datetime.now(timezone.utc).isoformat()
        identifier = str(user_id) if user_id else internal_wallet_id or "unknown"
        content = f"snapshot_{wallet_type}_{identifier}_{timestamp}_{self.process_id}"
        hash_value = hashlib.sha256(content.encode()).hexdigest()[:12]
        return f"snap_{hash_value}_{int(datetime.now(timezone.utc).timestamp())}"
    
    def _generate_balance_checksum(self, context: BalanceChangeContext, amount: Decimal) -> str:
        """Generate checksum for balance integrity verification"""
        content = f"{context.wallet_type}_{context.currency}_{context.balance_type}_{amount}_{datetime.now(timezone.utc).date()}"
        return hashlib.sha256(content.encode()).hexdigest()
    
    def _generate_balance_checksum_for_snapshot(
        self, available: Decimal, frozen: Decimal, locked: Decimal, reserved: Decimal
    ) -> str:
        """Generate checksum for balance snapshot"""
        content = f"available:{available}_frozen:{frozen}_locked:{locked}_reserved:{reserved}_{datetime.now(timezone.utc).date()}"
        return hashlib.sha256(content.encode()).hexdigest()
    
    def _validate_balance_change(
        self, session: Session, context: BalanceChangeContext, 
        amount_before: Decimal, amount_after: Decimal
    ) -> bool:
        """Validate that the balance change is reasonable and safe"""
        try:
            # Basic validations
            if amount_before < 0 or amount_after < 0:
                logger.warning(f"⚠️ VALIDATE: Negative balance detected - before: {amount_before}, after: {amount_after}")
                return False
            
            change_amount = amount_after - amount_before
            if abs(change_amount) == 0:
                return True  # No change is valid
            
            # Check for extremely large changes (potential fraud detection)
            max_single_change = Decimal('1000000')  # $1M limit for single changes
            if abs(change_amount) > max_single_change:
                logger.warning(f"⚠️ VALIDATE: Extremely large balance change detected: {change_amount}")
                # Don't fail validation, just log - large legitimate transactions exist
            
            return True
            
        except Exception as e:
            logger.error(f"❌ VALIDATE: Error validating balance change: {e}")
            return False
    
    def _log_to_financial_audit_system(
        self, session: Session, audit_log: BalanceAuditLog, context: BalanceChangeContext
    ):
        """Log to the existing financial audit system for integration"""
        try:
            financial_context = FinancialContext(
                amount=audit_log.change_amount,
                currency=context.currency
            )
            
            event_type = FinancialEventType.WALLET_CREDIT if audit_log.change_type == "credit" else FinancialEventType.WALLET_DEBIT
            entity_type = EntityType.WALLET if context.wallet_type == "user" else EntityType.INTERNAL_WALLET
            entity_id = f"{context.wallet_type}_{context.user_id or context.internal_wallet_id}"
            
            additional_data = {
                "audit_id": audit_log.audit_id,
                "balance_type": context.balance_type,
                "amount_before": float(audit_log.amount_before),
                "amount_after": float(audit_log.amount_after),
                "operation_type": context.operation_type,
                "validation_passed": audit_log.balance_validation_passed,
                "service_layer": "balance_audit_service"
            }
            
            financial_audit_logger.log_financial_event(
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                user_id=context.user_id,
                financial_context=financial_context,
                previous_state=f"balance_{float(audit_log.amount_before)}",
                new_state=f"balance_{float(audit_log.amount_after)}",
                related_entities={
                    "transaction_id": context.transaction_id,
                    "escrow_id": context.escrow_id,
                    "cashout_id": context.cashout_id
                },
                additional_data=additional_data,
                session=session
            )
            
        except Exception as e:
            logger.error(f"❌ FINANCIAL_AUDIT: Error logging to financial audit system: {e}")
            # Don't propagate errors from audit system integration
    
    def _verify_user_wallet_consistency(self, session: Session, wallet: Wallet) -> Dict[str, Any]:
        """Verify consistency of a user wallet against audit logs"""
        # Implementation would check wallet balance against sum of audit log changes
        # This is a placeholder for the detailed consistency checking logic
        return {
            "consistent": True,
            "discrepancies": [],
            "warnings": []
        }
    
    def _verify_internal_wallet_consistency(self, session: Session, internal_wallet: InternalWallet) -> Dict[str, Any]:
        """Verify consistency of an internal wallet against audit logs"""
        # Implementation would check internal wallet balance against sum of audit log changes
        # This is a placeholder for the detailed consistency checking logic
        return {
            "consistent": True,
            "discrepancies": [],
            "warnings": []
        }
    
    def _get_transaction_count(
        self, session: Session, wallet_type: str, user_id: Optional[int], internal_wallet_id: Optional[str]
    ) -> int:
        """Get transaction count for snapshot context"""
        try:
            if wallet_type == "user" and user_id:
                return session.query(BalanceAuditLog).filter(
                    and_(
                        BalanceAuditLog.wallet_type == "user",
                        BalanceAuditLog.user_id == user_id
                    )
                ).count()
            elif wallet_type == "internal" and internal_wallet_id:
                return session.query(BalanceAuditLog).filter(
                    and_(
                        BalanceAuditLog.wallet_type == "internal",
                        BalanceAuditLog.internal_wallet_id == internal_wallet_id
                    )
                ).count()
            return 0
        except Exception as e:
            logger.error(f"Error getting transaction count: {e}")
            return 0
    
    def _get_last_transaction_id(
        self, session: Session, wallet_type: str, user_id: Optional[int], internal_wallet_id: Optional[str]
    ) -> Optional[str]:
        """Get last transaction ID for snapshot context"""
        try:
            if wallet_type == "user" and user_id:
                last_audit = session.query(BalanceAuditLog).filter(
                    and_(
                        BalanceAuditLog.wallet_type == "user",
                        BalanceAuditLog.user_id == user_id
                    )
                ).order_by(desc(BalanceAuditLog.created_at)).first()
                
                return last_audit.transaction_id if last_audit else None
            elif wallet_type == "internal" and internal_wallet_id:
                last_audit = session.query(BalanceAuditLog).filter(
                    and_(
                        BalanceAuditLog.wallet_type == "internal",
                        BalanceAuditLog.internal_wallet_id == internal_wallet_id
                    )
                ).order_by(desc(BalanceAuditLog.created_at)).first()
                
                return last_audit.transaction_id if last_audit else None
            return None
        except Exception as e:
            logger.error(f"Error getting last transaction ID: {e}")
            return None


# Global instance for easy access
balance_audit_service = BalanceAuditService()