"""
Wallet Service - Provides unified wallet operations with comprehensive audit trails

Enhanced with TransactionSafetyService and BalanceAuditService for complete
audit trail compliance and atomic balance operations.
"""

import logging
from decimal import Decimal
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

from services.crypto import CryptoServiceAtomic
from models import TransactionType
from utils.financial_audit_logger import (
    financial_audit_logger,
    FinancialEventType,
    FinancialContext,
    EntityType
)

# Import new audit trail services
from services.transaction_safety_service import (
    transaction_safety_service
)

logger = logging.getLogger(__name__)


class WalletService:
    """Service for handling wallet operations with atomic guarantees"""
    
    def __init__(self, db: Session):
        self.db = db
    
    def credit_user_wallet(self,
                          user_id: int,
                          amount: Decimal,
                          transaction_type: Optional[TransactionType] = None,
                          description: Optional[str] = None,
                          escrow_id: Optional[int] = None,
                          currency: str = "USD",
                          use_audit_system: bool = True) -> Dict[str, Any]:
        """
        Credit user's wallet with comprehensive audit trail and atomic transaction guarantees
        
        Args:
            user_id: User ID to credit
            amount: Amount to credit
            transaction_type: Type of transaction (e.g., TransactionType.REFUND)
            description: Transaction description
            escrow_id: Related escrow ID if applicable
            currency: Currency (default USD)
            use_audit_system: Whether to use new audit system (default True)
            
        Returns:
            Dict with success status and details
        """
        try:
            # Use new audit trail system if enabled
            if use_audit_system:
                return self._credit_user_wallet_with_audit(
                    user_id=user_id,
                    amount=amount,
                    transaction_type=transaction_type,
                    description=description,
                    escrow_id=escrow_id,
                    currency=currency
                )
            
            # Fallback to legacy implementation for backward compatibility
            return self._credit_user_wallet_legacy(
                user_id=user_id,
                amount=amount,
                transaction_type=transaction_type,
                description=description,
                escrow_id=escrow_id,
                currency=currency
            )
                
        except Exception as e:
            logger.error(f"❌ Wallet credit exception: {e}")
            return {
                'success': False,
                'error': str(e),
                'amount': amount,
                'currency': currency
            }
    
    def _credit_user_wallet_with_audit(self,
                                     user_id: int,
                                     amount: Decimal,
                                     transaction_type: Optional[TransactionType] = None,
                                     description: Optional[str] = None,
                                     escrow_id: Optional[int] = None,
                                     currency: str = "USD") -> Dict[str, Any]:
        """Credit user wallet using new audit trail system"""
        try:
            # Use the new TransactionSafetyService for atomic operations with complete audit
            result = transaction_safety_service.safe_wallet_credit(
                session=self.db,
                user_id=user_id,
                amount=amount,
                currency=currency,
                transaction_type=transaction_type.value if transaction_type else "wallet_credit",
                description=description or f"Wallet credit {amount} {currency}",
                escrow_id=str(escrow_id) if escrow_id else None,
                initiated_by="wallet_service",
                initiated_by_id=f"wallet_service_{user_id}"
            )
            
            if result.success:
                # Log financial event for wallet service wrapper (maintain existing logging)
                financial_context = FinancialContext(
                    amount=amount,
                    currency=currency
                )
                
                related_entities = {}
                if escrow_id:
                    related_entities["escrow_id"] = str(escrow_id)
                
                # Add audit information
                related_entities["audit_ids"] = result.audit_ids
                related_entities["transaction_id"] = result.transaction_id
                
                financial_audit_logger.log_financial_event(
                    event_type=FinancialEventType.WALLET_CREDIT,
                    entity_type=EntityType.WALLET,
                    entity_id=f"wallet_service_{user_id}",
                    user_id=user_id,
                    financial_context=financial_context,
                    previous_state="credit_requested",
                    new_state="credit_processed",
                    related_entities=related_entities,
                    additional_data={
                        "service_layer": "wallet_service_enhanced",
                        "transaction_type": transaction_type.value if transaction_type else None,
                        "description": description,
                        "audit_system_used": True,
                        "operations_completed": result.operations_completed,
                        "transaction_result": result.result_type.value
                    },
                    session=self.db
                )
                
                logger.info(f"✅ Wallet credited with audit: User {user_id}, Amount {amount} {currency}, Audit IDs: {result.audit_ids}")
                return {
                    'success': True,
                    'message': f'Successfully credited {amount} {currency}',
                    'amount': amount,
                    'currency': currency,
                    'transaction_id': result.transaction_id,
                    'audit_ids': result.audit_ids,
                    'audit_system': True
                }
            else:
                logger.error(f"❌ Wallet credit with audit failed: User {user_id}, Amount {amount} {currency}, Error: {result.error_message}")
                return {
                    'success': False,
                    'error': result.error_message or 'Failed to credit wallet with audit system',
                    'amount': amount,
                    'currency': currency,
                    'transaction_id': result.transaction_id,
                    'result_type': result.result_type.value
                }
                
        except Exception as e:
            logger.error(f"❌ Wallet credit with audit exception: {e}")
            return {
                'success': False,
                'error': f'Audit system error: {e}',
                'amount': amount,
                'currency': currency
            }
    
    def _credit_user_wallet_legacy(self,
                                 user_id: int,
                                 amount: Decimal,
                                 transaction_type: Optional[TransactionType] = None,
                                 description: Optional[str] = None,
                                 escrow_id: Optional[int] = None,
                                 currency: str = "USD") -> Dict[str, Any]:
        """Legacy wallet credit implementation for backward compatibility"""
        try:
            # Convert transaction type to string if it's an enum
            tx_type_str = transaction_type.value if transaction_type else None
            
            success = CryptoServiceAtomic.credit_user_wallet_atomic(
                user_id=user_id,
                amount=amount,
                currency=currency,
                escrow_id=escrow_id,
                transaction_type=tx_type_str,
                description=description,
                session=self.db
            )
            
            if success:
                # Log financial event for wallet service wrapper
                financial_context = FinancialContext(
                    amount=amount,
                    currency=currency
                )
                
                related_entities = {}
                if escrow_id:
                    related_entities["escrow_id"] = str(escrow_id)
                
                financial_audit_logger.log_financial_event(
                    event_type=FinancialEventType.WALLET_CREDIT,
                    entity_type=EntityType.WALLET,
                    entity_id=f"wallet_service_{user_id}",
                    user_id=user_id,
                    financial_context=financial_context,
                    previous_state="credit_requested",
                    new_state="credit_processed",
                    related_entities=related_entities,
                    additional_data={
                        "service_layer": "wallet_service_legacy",
                        "transaction_type": transaction_type.value if transaction_type else None,
                        "description": description
                    },
                    session=self.db
                )
                
                logger.info(f"✅ Wallet credited (legacy): User {user_id}, Amount {amount} {currency}")
                return {
                    'success': True,
                    'message': f'Successfully credited {amount} {currency}',
                    'amount': amount,
                    'currency': currency
                }
            else:
                logger.error(f"❌ Wallet credit failed: User {user_id}, Amount {amount} {currency}")
                return {
                    'success': False,
                    'error': 'Failed to credit wallet',
                    'amount': amount,
                    'currency': currency
                }
                
        except Exception as e:
            logger.error(f"❌ Wallet credit legacy exception: {e}")
            return {
                'success': False,
                'error': str(e),
                'amount': amount,
                'currency': currency
            }
    
    def debit_user_wallet(self,
                         user_id: int,
                         amount: Decimal,
                         transaction_type: Optional[TransactionType] = None,
                         description: Optional[str] = None,
                         escrow_id: Optional[int] = None,
                         currency: str = "USD",
                         use_audit_system: bool = True) -> Dict[str, Any]:
        """
        Debit user's wallet with comprehensive audit trail and atomic transaction guarantees
        
        Args:
            user_id: User ID to debit
            amount: Amount to debit
            transaction_type: Type of transaction
            description: Transaction description
            escrow_id: Related escrow ID if applicable
            currency: Currency (default USD)
            use_audit_system: Whether to use new audit system (default True)
            
        Returns:
            Dict with success status and details
        """
        try:
            # Use new audit trail system if enabled
            if use_audit_system:
                return self._debit_user_wallet_with_audit(
                    user_id=user_id,
                    amount=amount,
                    transaction_type=transaction_type,
                    description=description,
                    escrow_id=escrow_id,
                    currency=currency
                )
            
            # Fallback to legacy implementation for backward compatibility
            return self._debit_user_wallet_legacy(
                user_id=user_id,
                amount=amount,
                transaction_type=transaction_type,
                description=description,
                escrow_id=escrow_id,
                currency=currency
            )
                
        except Exception as e:
            logger.error(f"❌ Wallet debit exception: {e}")
            return {
                'success': False,
                'error': str(e),
                'amount': amount,
                'currency': currency
            }
    
    def _debit_user_wallet_with_audit(self,
                                    user_id: int,
                                    amount: Decimal,
                                    transaction_type: Optional[TransactionType] = None,
                                    description: Optional[str] = None,
                                    escrow_id: Optional[int] = None,
                                    currency: str = "USD") -> Dict[str, Any]:
        """Debit user wallet using new audit trail system"""
        try:
            # Use the new TransactionSafetyService for atomic operations with complete audit
            result = transaction_safety_service.safe_wallet_debit(
                session=self.db,
                user_id=user_id,
                amount=amount,
                currency=currency,
                transaction_type=transaction_type.value if transaction_type else "wallet_debit",
                description=description or f"Wallet debit {amount} {currency}",
                check_balance=True,  # Always check balance for debits
                escrow_id=str(escrow_id) if escrow_id else None,
                initiated_by="wallet_service",
                initiated_by_id=f"wallet_service_{user_id}"
            )
            
            if result.success:
                # Log financial event for wallet service wrapper (maintain existing logging)
                financial_context = FinancialContext(
                    amount=amount,
                    currency=currency
                )
                
                related_entities = {}
                if escrow_id:
                    related_entities["escrow_id"] = str(escrow_id)
                
                # Add audit information
                related_entities["audit_ids"] = result.audit_ids
                related_entities["transaction_id"] = result.transaction_id
                
                financial_audit_logger.log_financial_event(
                    event_type=FinancialEventType.WALLET_DEBIT,
                    entity_type=EntityType.WALLET,
                    entity_id=f"wallet_service_{user_id}",
                    user_id=user_id,
                    financial_context=financial_context,
                    previous_state="debit_requested",
                    new_state="debit_processed",
                    related_entities=related_entities,
                    additional_data={
                        "service_layer": "wallet_service_enhanced",
                        "transaction_type": transaction_type.value if transaction_type else None,
                        "description": description,
                        "audit_system_used": True,
                        "operations_completed": result.operations_completed,
                        "transaction_result": result.result_type.value
                    },
                    session=self.db
                )
                
                logger.info(f"✅ Wallet debited with audit: User {user_id}, Amount {amount} {currency}, Audit IDs: {result.audit_ids}")
                return {
                    'success': True,
                    'message': f'Successfully debited {amount} {currency}',
                    'amount': amount,
                    'currency': currency,
                    'transaction_id': result.transaction_id,
                    'audit_ids': result.audit_ids,
                    'audit_system': True
                }
            else:
                logger.error(f"❌ Wallet debit with audit failed: User {user_id}, Amount {amount} {currency}, Error: {result.error_message}")
                return {
                    'success': False,
                    'error': result.error_message or 'Failed to debit wallet with audit system',
                    'amount': amount,
                    'currency': currency,
                    'transaction_id': result.transaction_id,
                    'result_type': result.result_type.value
                }
                
        except Exception as e:
            logger.error(f"❌ Wallet debit with audit exception: {e}")
            return {
                'success': False,
                'error': f'Audit system error: {e}',
                'amount': amount,
                'currency': currency
            }
    
    def _debit_user_wallet_legacy(self,
                                user_id: int,
                                amount: Decimal,
                                transaction_type: Optional[TransactionType] = None,
                                description: Optional[str] = None,
                                escrow_id: Optional[int] = None,
                                currency: str = "USD") -> Dict[str, Any]:
        """Legacy wallet debit implementation for backward compatibility"""
        try:
            # Convert transaction type to string if it's an enum
            tx_type_str = transaction_type.value if transaction_type else None
            
            success = CryptoServiceAtomic.debit_user_wallet_atomic(
                user_id=user_id,
                amount=amount,
                currency=currency,
                escrow_id=escrow_id,
                transaction_type=tx_type_str,
                description=description,
                session=self.db
            )
            
            if success:
                # Log financial event for wallet service wrapper debit
                financial_context = FinancialContext(
                    amount=amount,
                    currency=currency
                )
                
                related_entities = {}
                if escrow_id:
                    related_entities["escrow_id"] = str(escrow_id)
                
                financial_audit_logger.log_financial_event(
                    event_type=FinancialEventType.WALLET_DEBIT,
                    entity_type=EntityType.WALLET,
                    entity_id=f"wallet_service_{user_id}",
                    user_id=user_id,
                    financial_context=financial_context,
                    previous_state="debit_requested",
                    new_state="debit_processed",
                    related_entities=related_entities,
                    additional_data={
                        "service_layer": "wallet_service_legacy",
                        "transaction_type": transaction_type.value if transaction_type else None,
                        "description": description
                    },
                    session=self.db
                )
                
                logger.info(f"✅ Wallet debited (legacy): User {user_id}, Amount {amount} {currency}")
                return {
                    'success': True,
                    'message': f'Successfully debited {amount} {currency}',
                    'amount': amount,
                    'currency': currency
                }
            else:
                logger.error(f"❌ Wallet debit failed: User {user_id}, Amount {amount} {currency}")
                return {
                    'success': False,
                    'error': 'Failed to debit wallet',
                    'amount': amount,
                    'currency': currency
                }
                
        except Exception as e:
            logger.error(f"❌ Wallet debit legacy exception: {e}")
            return {
                'success': False,
                'error': str(e),
                'amount': amount,
                'currency': currency
            }