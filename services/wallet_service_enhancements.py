"""
WalletService Enhancements - Additional methods for audit trail integration

This file contains additional methods to be added to the WalletService class
to complete the integration with the comprehensive audit trail system.
"""

import logging
from typing import Optional, Dict, Any
from decimal import Decimal

logger = logging.getLogger(__name__)

# Lazy imports to avoid circular dependencies
def _get_balance_validator():
    try:
        from services.balance_validator import balance_validator
        return balance_validator
    except ImportError:
        return None

def _get_balance_audit_service():
    try:
        from services.balance_audit_service import balance_audit_service
        return balance_audit_service
    except ImportError:
        return None

def _get_transaction_safety_service():
    try:
        from services.transaction_safety_service import transaction_safety_service
        return transaction_safety_service
    except ImportError:
        return None

def _get_financial_audit_logger():
    try:
        from utils.financial_audit_logger import financial_audit_logger
        return financial_audit_logger
    except ImportError:
        return None

# Safe enums - fallback to string if not available
try:
    from models import TransactionType
except ImportError:
    TransactionType = None

try:
    from utils.financial_audit_logger import FinancialEventType, FinancialContext, EntityType
except ImportError:
    FinancialEventType = None
    FinancialContext = None
    EntityType = None

def validate_wallet_balance(self,
                          user_id: int,
                          currency: Optional[str] = None) -> Dict[str, Any]:
    """
    Validate wallet balance consistency using the new balance validator
    
    Args:
        user_id: User ID to validate
        currency: Specific currency to validate (optional)
        
    Returns:
        Dict with validation results
    """
    try:
        # Use the new balance validator for comprehensive validation
        validation_result = _get_balance_validator().validate_user_wallet(
            session=self.db,
            user_id=user_id,
            currency=currency,
            include_transaction_history=True,
            include_audit_validation=True
        )
        
        logger.info(f"💯 Wallet validation for user {user_id}: {validation_result.success}")
        
        return {
            'success': validation_result.success,
            'wallets_checked': validation_result.wallets_checked,
            'issues_found': validation_result.issues_found,
            'critical_issues': validation_result.critical_issues,
            'warnings': validation_result.warnings,
            'summary': validation_result.summary,
            'validation_id': f"validation_{user_id}_{int(validation_result.started_at.timestamp())}"
        }
        
    except Exception as e:
        logger.error(f"❌ Wallet validation exception: {e}")
        return {
            'success': False,
            'error': str(e),
            'user_id': user_id,
            'currency': currency
        }

def get_wallet_audit_history(self,
                           user_id: int,
                           currency: Optional[str] = None,
                           limit: int = 50,
                           offset: int = 0) -> Dict[str, Any]:
    """
    Get comprehensive audit history for user wallets
    
    Args:
        user_id: User ID
        currency: Currency filter (optional)
        limit: Maximum records to return
        offset: Records to skip
        
    Returns:
        Dict with audit history
    """
    try:
        # Use the new balance audit service to get history
        audit_history = _get_balance_audit_service().get_audit_history(
            session=self.db,
            wallet_type="user",
            user_id=user_id,
            currency=currency,
            limit=limit,
            offset=offset
        )
        
        logger.info(f"📋 Retrieved {len(audit_history)} audit records for user {user_id}")
        
        return {
            'success': True,
            'audit_history': audit_history,
            'total_records': len(audit_history),
            'user_id': user_id,
            'currency': currency,
            'pagination': {
                'limit': limit,
                'offset': offset,
                'has_more': len(audit_history) == limit
            }
        }
        
    except Exception as e:
        logger.error(f"❌ Error retrieving audit history: {e}")
        return {
            'success': False,
            'error': str(e),
            'user_id': user_id,
            'currency': currency
        }

def transfer_between_users(self,
                         from_user_id: int,
                         to_user_id: int,
                         amount: Decimal,
                         currency: str = "USD",
                         description: Optional[str] = None,
                         transaction_type: Optional[TransactionType] = None) -> Dict[str, Any]:
    """
    Transfer funds between user wallets using the new audit system
    
    Args:
        from_user_id: Source user ID
        to_user_id: Destination user ID
        amount: Amount to transfer
        currency: Currency
        description: Transfer description
        transaction_type: Type of transaction
        
    Returns:
        Dict with transfer result
    """
    try:
        # Use the new TransactionSafetyService for atomic transfer with complete audit
        result = _get_transaction_safety_service().transfer_between_wallets(
            session=self.db,
            from_user_id=from_user_id,
            to_user_id=to_user_id,
            amount=amount,
            currency=currency,
            transaction_type=transaction_type.value if transaction_type else "user_transfer",
            description=description or f"Transfer {amount} {currency} from user {from_user_id} to user {to_user_id}",
            initiated_by="wallet_service",
            initiated_by_id="wallet_service_transfer"
        )
        
        if result.success:
            # Log financial event for transfer wrapper
            financial_context = FinancialContext(
                amount=amount,
                currency=currency
            )
            
            _get_financial_audit_logger().log_financial_event(
                event_type=FinancialEventType.WALLET_TRANSFER,
                entity_type=EntityType.WALLET,
                entity_id=f"wallet_transfer_{from_user_id}_to_{to_user_id}",
                user_id=from_user_id,
                financial_context=financial_context,
                previous_state="transfer_initiated",
                new_state="transfer_completed",
                related_entities={
                    "from_user_id": str(from_user_id),
                    "to_user_id": str(to_user_id),
                    "audit_ids": result.audit_ids,
                    "transaction_id": result.transaction_id
                },
                additional_data={
                    "service_layer": "wallet_service_transfer",
                    "transaction_type": transaction_type.value if transaction_type else None,
                    "description": description,
                    "operations_completed": result.operations_completed,
                    "audit_system_used": True
                },
                session=self.db
            )
            
            logger.info(f"✅ Transfer completed: {amount} {currency} from user {from_user_id} to {to_user_id}, Audit IDs: {result.audit_ids}")
            return {
                'success': True,
                'message': f'Successfully transferred {amount} {currency}',
                'amount': amount,
                'currency': currency,
                'from_user_id': from_user_id,
                'to_user_id': to_user_id,
                'transaction_id': result.transaction_id,
                'audit_ids': result.audit_ids,
                'operations_completed': result.operations_completed
            }
        else:
            logger.error(f"❌ Transfer failed: {amount} {currency} from user {from_user_id} to {to_user_id}, Error: {result.error_message}")
            return {
                'success': False,
                'error': result.error_message or 'Transfer failed',
                'amount': amount,
                'currency': currency,
                'from_user_id': from_user_id,
                'to_user_id': to_user_id,
                'transaction_id': result.transaction_id,
                'result_type': result.result_type.value
            }
            
    except Exception as e:
        logger.error(f"❌ Transfer exception: {e}")
        return {
            'success': False,
            'error': str(e),
            'amount': amount,
            'currency': currency,
            'from_user_id': from_user_id,
            'to_user_id': to_user_id
        }

def create_balance_snapshot(self,
                          user_id: int,
                          currency: Optional[str] = None,
                          snapshot_type: str = "manual") -> Dict[str, Any]:
    """
    Create balance snapshot for verification purposes
    
    Args:
        user_id: User ID
        currency: Currency filter (optional)  
        snapshot_type: Type of snapshot ('manual', 'scheduled', etc.)
        
    Returns:
        Dict with snapshot result
    """
    try:
        # Get user wallets
        from models import Wallet
        query = self.db.query(Wallet).filter(Wallet.user_id == user_id)
        if currency:
            query = query.filter(Wallet.currency == currency)
        
        wallets = query.all()
        snapshot_ids = []
        
        # Create snapshots for each wallet
        for wallet in wallets:
            snapshot_id = _get_balance_audit_service().create_balance_snapshot(
                session=self.db,
                wallet_type="user",
                user_id=user_id,
                wallet_id=wallet.id,
                snapshot_type=snapshot_type,
                trigger_event=f"Manual snapshot request for {wallet.currency} wallet"
            )
            
            if snapshot_id:
                snapshot_ids.append(snapshot_id)
        
        logger.info(f"📸 Created {len(snapshot_ids)} balance snapshots for user {user_id}")
        
        return {
            'success': True,
            'snapshot_ids': snapshot_ids,
            'wallets_snapshotted': len(snapshot_ids),
            'user_id': user_id,
            'currency': currency,
            'snapshot_type': snapshot_type
        }
        
    except Exception as e:
        logger.error(f"❌ Error creating balance snapshot: {e}")
        return {
            'success': False,
            'error': str(e),
            'user_id': user_id,
            'currency': currency
        }

def detect_balance_discrepancies(self,
                               user_id: Optional[int] = None,
                               currency: Optional[str] = None,
                               threshold: Decimal = Decimal('0.01')) -> Dict[str, Any]:
    """
    Detect balance discrepancies using the new validator
    
    Args:
        user_id: Specific user to check (optional)
        currency: Specific currency to check (optional)
        threshold: Minimum discrepancy amount to report
        
    Returns:
        Dict with discrepancy detection results
    """
    try:
        # Use the balance validator to detect discrepancies
        if user_id:
            # Validate specific user
            validation_result = _get_balance_validator().validate_user_wallet(
                session=self.db,
                user_id=user_id,
                currency=currency,
                include_transaction_history=True,
                include_audit_validation=True
            )
        else:
            # Detect discrepancies across all wallets
            validation_result = _get_balance_validator().detect_balance_discrepancies(
                session=self.db,
                threshold=threshold,
                max_age_days=7
            )
        
        discrepancies = [
            {
                'severity': issue.severity.value,
                'type': issue.discrepancy_type.value,
                'user_id': issue.user_id,
                'currency': issue.currency,
                'expected_balance': float(issue.expected_balance),
                'actual_balance': float(issue.actual_balance),
                'difference': float(issue.difference),
                'description': issue.description
            }
            for issue in validation_result.issues
        ]
        
        logger.info(f"🔍 Discrepancy detection complete: {len(discrepancies)} issues found")
        
        return {
            'success': validation_result.success,
            'discrepancies': discrepancies,
            'total_issues': len(discrepancies),
            'critical_issues': validation_result.critical_issues,
            'warnings': validation_result.warnings,
            'wallets_checked': validation_result.wallets_checked,
            'threshold': float(threshold),
            'summary': validation_result.summary
        }
        
    except Exception as e:
        logger.error(f"❌ Error detecting discrepancies: {e}")
        return {
            'success': False,
            'error': str(e),
            'user_id': user_id,
            'currency': currency
        }