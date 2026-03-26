"""
Scene Engine UTE Integration Layer

Connects the Scene Engine to the Unified Transaction Engine (UTE) for financial operations.
Provides a bridge between declarative scene flows and UTE transaction processing.
"""

import logging
from typing import Dict, Any
from decimal import Decimal

from services.unified_transaction_engine import (
    UnifiedTransactionEngine, TransactionRequest, UnifiedTransactionType,
    UnifiedTransactionPriority
)
from services.scene_engine import SceneState

logger = logging.getLogger(__name__)

class SceneUTEAdapter:
    """Adapter that connects Scene Engine flows to UTE operations"""
    
    def __init__(self):
        self.ute = UnifiedTransactionEngine()
    
    async def initialize(self) -> None:
        """Initialize the UTE adapter"""
        # UTE doesn't need explicit initialization
        logger.info("UTE adapter ready")
    
    async def process_scene_transaction(
        self, 
        scene_state: SceneState, 
        transaction_type: str
    ) -> Dict[str, Any]:
        """Process a transaction from scene data using UTE"""
        try:
            # Map scene data to UTE transaction request
            transaction_request = self._build_transaction_request(scene_state, transaction_type)
            
            # Process through UTE
            result = await self.ute.process_transaction(transaction_request)
            
            if result.success:
                return {
                    'success': True,
                    'transaction_id': result.transaction_id,
                    'status': result.status.value if result.status else None,
                    'message': result.message,
                    'processing_data': result.processing_data or {}
                }
            else:
                return {
                    'success': False,
                    'error': result.error,
                    'error_code': result.error_code,
                    'is_retryable': result.is_retryable
                }
        
        except Exception as e:
            logger.error(f"Scene UTE processing error: {e}")
            return {
                'success': False,
                'error': f"Transaction processing failed: {e}",
                'is_retryable': True
            }
    
    def _build_transaction_request(
        self, 
        scene_state: SceneState, 
        transaction_type: str
    ) -> TransactionRequest:
        """Build UTE transaction request from scene data"""
        data = scene_state.data
        
        # Map scene transaction types to UTE types
        ute_type_mapping = {
            'ngn_cashout': UnifiedTransactionType.NGN_CASHOUT,
            'crypto_cashout': UnifiedTransactionType.CRYPTO_CASHOUT,
            'wallet_funding': UnifiedTransactionType.WALLET_FUNDING,
            'escrow_creation': UnifiedTransactionType.ESCROW_CREATION,
            'escrow_payment': UnifiedTransactionType.ESCROW_PAYMENT,
            'escrow_release': UnifiedTransactionType.ESCROW_RELEASE
        }
        
        ute_type = ute_type_mapping.get(transaction_type, UnifiedTransactionType.GENERIC)
        
        # Extract amount and currency
        amount = Decimal(str(data.get('amount', 0)))
        currency = data.get('currency', 'USD')
        
        # Build metadata from scene data
        metadata = {
            'scene_id': scene_state.scene_id,
            'scene_step': scene_state.current_step,
            'confirmation_data': data.get('transaction_data', {}),
            'user_preferences': data.get('user_preferences', {}),
            'original_scene_data': data
        }
        
        # Add transaction-specific metadata
        if transaction_type == 'ngn_cashout':
            metadata.update({
                'bank_code': data.get('bank_code'),
                'bank_name': data.get('bank_name'),
                'account_number': data.get('account_number'),
                'account_name': data.get('account_name'),
                'otp_verified': data.get('otp_verified', False)
            })
        
        elif transaction_type == 'crypto_cashout':
            metadata.update({
                'crypto_currency': data.get('crypto'),
                'destination_address': data.get('address'),
                'network': data.get('network'),
                'address_source': data.get('source')  # 'saved' or 'manual'
            })
        
        elif transaction_type == 'wallet_funding':
            metadata.update({
                'funding_currency': data.get('selected_currency'),
                'funding_address': data.get('funding_address'),
                'payment_method': data.get('payment_method')
            })
        
        elif transaction_type == 'escrow_creation':
            metadata.update({
                'escrow_title': data.get('title'),
                'escrow_description': data.get('description'),
                'delivery_method': data.get('delivery_method'),
                'payment_timeout': data.get('payment_timeout'),
                'release_timeout': data.get('release_timeout'),
                'buyer_contact': data.get('buyer_contact')
            })
        
        # Determine priority based on transaction type and amount
        if amount > 1000:
            priority = UnifiedTransactionPriority.HIGH
        elif transaction_type in ['escrow_creation', 'escrow_payment']:
            priority = UnifiedTransactionPriority.HIGH
        else:
            priority = UnifiedTransactionPriority.NORMAL
        
        return TransactionRequest(
            transaction_type=ute_type,
            user_id=scene_state.user_id,
            amount=amount,
            currency=currency,
            priority=priority,
            metadata=metadata,
            preferred_provider=data.get('preferred_provider'),
            provider_metadata=data.get('provider_metadata', {})
        )
    
    async def get_transaction_status(self, transaction_id: str) -> Dict[str, Any]:
        """Get current transaction status from UTE"""
        try:
            result = await self.ute.get_transaction_status(transaction_id)
            return result
        except Exception as e:
            logger.error(f"Failed to get transaction status: {e}")
            return {
                'success': False,
                'error': f"Status check failed: {e}"
            }
    
    async def cancel_transaction(self, transaction_id: str) -> Dict[str, Any]:
        """Cancel a transaction through UTE"""
        try:
            result = await self.ute.cancel_transaction(transaction_id)
            return result
        except Exception as e:
            logger.error(f"Failed to cancel transaction: {e}")
            return {
                'success': False,
                'error': f"Cancellation failed: {e}"
            }
    
    async def retry_transaction(self, transaction_id: str) -> Dict[str, Any]:
        """Retry a failed transaction through UTE"""
        try:
            result = await self.ute.retry_transaction(transaction_id)
            return result
        except Exception as e:
            logger.error(f"Failed to retry transaction: {e}")
            return {
                'success': False,
                'error': f"Retry failed: {e}"
            }

# Global adapter instance
_scene_ute_adapter = None

async def get_scene_ute_adapter() -> SceneUTEAdapter:
    """Get the global scene UTE adapter instance"""
    global _scene_ute_adapter
    if _scene_ute_adapter is None:
        _scene_ute_adapter = SceneUTEAdapter()
        await _scene_ute_adapter.initialize()
    return _scene_ute_adapter