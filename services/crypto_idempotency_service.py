"""
Crypto Idempotency Service
Provides comprehensive idempotency protection for crypto operations
Prevents double-processing of address generation, transactions, and wallet operations
"""

import logging
import hashlib
from typing import Optional, Dict, Any, Tuple
from datetime import datetime
from decimal import Decimal

from services.atomic_lock_manager import atomic_lock_manager, LockOperationType

logger = logging.getLogger(__name__)


class CryptoIdempotencyService:
    """
    Comprehensive idempotency protection for crypto operations
    
    Prevents:
    - Double generation of crypto addresses
    - Duplicate transaction processing
    - Multiple wallet debits/credits for same operation
    - Race conditions in crypto operations
    """
    
    def __init__(self):
        self.default_ttl = 24 * 3600  # 24 hours for crypto operations
        self.address_generation_ttl = 30 * 24 * 3600  # 30 days for address generation
    
    def _generate_crypto_address_key(
        self, 
        user_id: int, 
        currency: str, 
        network: str = None
    ) -> str:
        """Generate idempotency key for crypto address generation"""
        network_suffix = f"_{network}" if network else ""
        return f"crypto_address_{user_id}_{currency.lower()}{network_suffix}"
    
    def _generate_transaction_key(
        self, 
        user_id: int, 
        operation_type: str,
        amount: Decimal,
        currency: str,
        reference_id: str = None
    ) -> str:
        """Generate idempotency key for transaction operations"""
        # Include amount and currency in hash for precision
        amount_str = str(amount)
        base_string = f"{user_id}_{operation_type}_{amount_str}_{currency}"
        if reference_id:
            base_string += f"_{reference_id}"
        
        # Use hash for consistent key length
        hash_digest = hashlib.sha256(base_string.encode()).hexdigest()[:16]
        return f"crypto_tx_{operation_type}_{user_id}_{hash_digest}"
    
    def _generate_wallet_operation_key(
        self,
        user_id: int,
        operation: str,
        amount: Decimal,
        currency: str,
        external_id: str = None
    ) -> str:
        """Generate idempotency key for wallet operations"""
        base_string = f"{user_id}_{operation}_{str(amount)}_{currency}"
        if external_id:
            base_string += f"_{external_id}"
        
        hash_digest = hashlib.sha256(base_string.encode()).hexdigest()[:16]
        return f"wallet_op_{operation}_{user_id}_{hash_digest}"
    
    async def ensure_crypto_address_idempotency(
        self,
        user_id: int,
        currency: str,
        network: str = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Ensure crypto address generation is idempotent
        
        Args:
            user_id: User ID requesting address
            currency: Cryptocurrency (BTC, USDT, etc.)
            network: Network if applicable (TRC20, ERC20, etc.)
            
        Returns:
            (is_duplicate, existing_address):
            - (False, None) if this is a new request
            - (True, address) if address already exists
        """
        idempotency_key = self._generate_crypto_address_key(user_id, currency, network)
        
        is_duplicate, result = await atomic_lock_manager.ensure_idempotency(
            idempotency_key=idempotency_key,
            operation_type="crypto_address_generation",
            resource_id=str(user_id),
            ttl_seconds=self.address_generation_ttl
        )
        
        if is_duplicate and result:
            existing_address = result.get('address')
            logger.info(
                f"🔄 CRYPTO_ADDRESS_DUPLICATE: user={user_id} {currency}"
                f"{f'/{network}' if network else ''} address={existing_address}"
            )
            return True, existing_address
        
        logger.info(
            f"🆕 CRYPTO_ADDRESS_NEW: user={user_id} {currency}"
            f"{f'/{network}' if network else ''}"
        )
        return False, None
    
    async def complete_crypto_address_generation(
        self,
        user_id: int,
        currency: str,
        address: str,
        network: str = None,
        metadata: Dict[str, Any] = None
    ) -> bool:
        """
        Mark crypto address generation as completed
        
        Args:
            user_id: User ID
            currency: Cryptocurrency
            address: Generated address
            network: Network if applicable
            metadata: Additional metadata to store
            
        Returns:
            True if successfully recorded
        """
        idempotency_key = self._generate_crypto_address_key(user_id, currency, network)
        
        result_data = {
            'address': address,
            'currency': currency,
            'network': network,
            'user_id': user_id,
            'generated_at': datetime.utcnow().isoformat()
        }
        
        if metadata:
            result_data['metadata'] = metadata
        
        success = await atomic_lock_manager.complete_idempotent_operation(
            idempotency_key=idempotency_key,
            success=True,
            result_data=result_data
        )
        
        if success:
            logger.info(
                f"✅ CRYPTO_ADDRESS_COMPLETED: user={user_id} {currency}"
                f"{f'/{network}' if network else ''} address={address}"
            )
        else:
            logger.error(
                f"❌ CRYPTO_ADDRESS_COMPLETION_FAILED: user={user_id} {currency}"
                f"{f'/{network}' if network else ''}"
            )
        
        return success
    
    async def ensure_transaction_idempotency(
        self,
        user_id: int,
        operation_type: str,
        amount: Decimal,
        currency: str,
        reference_id: str = None
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Ensure transaction processing is idempotent
        
        Args:
            user_id: User ID
            operation_type: Type of operation (deposit, withdraw, etc.)
            amount: Transaction amount
            currency: Currency
            reference_id: External reference ID
            
        Returns:
            (is_duplicate, previous_result)
        """
        idempotency_key = self._generate_transaction_key(
            user_id, operation_type, amount, currency, reference_id
        )
        
        is_duplicate, result = await atomic_lock_manager.ensure_idempotency(
            idempotency_key=idempotency_key,
            operation_type=f"crypto_transaction_{operation_type}",
            resource_id=str(user_id),
            ttl_seconds=self.default_ttl
        )
        
        if is_duplicate:
            logger.info(
                f"🔄 CRYPTO_TRANSACTION_DUPLICATE: user={user_id} "
                f"{operation_type} {amount} {currency} ref={reference_id}"
            )
        else:
            logger.info(
                f"🆕 CRYPTO_TRANSACTION_NEW: user={user_id} "
                f"{operation_type} {amount} {currency} ref={reference_id}"
            )
        
        return is_duplicate, result
    
    async def complete_transaction(
        self,
        user_id: int,
        operation_type: str,
        amount: Decimal,
        currency: str,
        success: bool,
        transaction_id: str = None,
        error_message: str = None,
        reference_id: str = None,
        metadata: Dict[str, Any] = None
    ) -> bool:
        """
        Mark transaction as completed
        
        Args:
            user_id: User ID
            operation_type: Type of operation
            amount: Transaction amount
            currency: Currency
            success: Whether operation succeeded
            transaction_id: Internal transaction ID
            error_message: Error if failed
            reference_id: External reference ID
            metadata: Additional metadata
            
        Returns:
            True if successfully recorded
        """
        idempotency_key = self._generate_transaction_key(
            user_id, operation_type, amount, currency, reference_id
        )
        
        result_data = {
            'operation_type': operation_type,
            'amount': str(amount),  # Store as string to preserve precision
            'currency': currency,
            'user_id': user_id,
            'transaction_id': transaction_id,
            'reference_id': reference_id,
            'completed_at': datetime.utcnow().isoformat()
        }
        
        if metadata:
            result_data['metadata'] = metadata
        
        completed = await atomic_lock_manager.complete_idempotent_operation(
            idempotency_key=idempotency_key,
            success=success,
            result_data=result_data if success else None,
            error_message=error_message
        )
        
        status = "SUCCESS" if success else "FAILED"
        logger.info(
            f"✅ CRYPTO_TRANSACTION_{status}: user={user_id} "
            f"{operation_type} {amount} {currency} tx={transaction_id}"
        )
        
        return completed
    
    async def ensure_wallet_operation_idempotency(
        self,
        user_id: int,
        operation: str,
        amount: Decimal,
        currency: str,
        external_id: str = None
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Ensure wallet operations are idempotent
        
        Args:
            user_id: User ID
            operation: Operation type (debit, credit, freeze, etc.)
            amount: Amount
            currency: Currency
            external_id: External reference ID
            
        Returns:
            (is_duplicate, previous_result)
        """
        idempotency_key = self._generate_wallet_operation_key(
            user_id, operation, amount, currency, external_id
        )
        
        is_duplicate, result = await atomic_lock_manager.ensure_idempotency(
            idempotency_key=idempotency_key,
            operation_type=f"wallet_{operation}",
            resource_id=str(user_id),
            ttl_seconds=self.default_ttl
        )
        
        if is_duplicate:
            logger.info(
                f"🔄 WALLET_OPERATION_DUPLICATE: user={user_id} "
                f"{operation} {amount} {currency} ext_id={external_id}"
            )
        else:
            logger.info(
                f"🆕 WALLET_OPERATION_NEW: user={user_id} "
                f"{operation} {amount} {currency} ext_id={external_id}"
            )
        
        return is_duplicate, result
    
    async def complete_wallet_operation(
        self,
        user_id: int,
        operation: str,
        amount: Decimal,
        currency: str,
        success: bool,
        new_balance: Decimal = None,
        transaction_id: str = None,
        error_message: str = None,
        external_id: str = None
    ) -> bool:
        """
        Mark wallet operation as completed
        
        Args:
            user_id: User ID
            operation: Operation type
            amount: Amount
            currency: Currency
            success: Whether operation succeeded
            new_balance: New wallet balance after operation
            transaction_id: Transaction ID
            error_message: Error if failed
            external_id: External reference ID
            
        Returns:
            True if successfully recorded
        """
        idempotency_key = self._generate_wallet_operation_key(
            user_id, operation, amount, currency, external_id
        )
        
        result_data = {
            'operation': operation,
            'amount': str(amount),
            'currency': currency,
            'user_id': user_id,
            'new_balance': str(new_balance) if new_balance is not None else None,
            'transaction_id': transaction_id,
            'external_id': external_id,
            'completed_at': datetime.utcnow().isoformat()
        }
        
        completed = await atomic_lock_manager.complete_idempotent_operation(
            idempotency_key=idempotency_key,
            success=success,
            result_data=result_data if success else None,
            error_message=error_message
        )
        
        status = "SUCCESS" if success else "FAILED"
        logger.info(
            f"✅ WALLET_OPERATION_{status}: user={user_id} "
            f"{operation} {amount} {currency} balance={new_balance}"
        )
        
        return completed
    
    async def atomic_crypto_operation(
        self,
        lock_name: str,
        user_id: int,
        operation_type: str,
        timeout_seconds: int = 60
    ):
        """
        Context manager for atomic crypto operations with both locking and idempotency
        
        Args:
            lock_name: Unique lock name
            user_id: User ID (used as resource_id)
            operation_type: Type of crypto operation
            timeout_seconds: Lock timeout
        """
        return atomic_lock_manager.atomic_lock_context(
            lock_name=lock_name,
            operation_type=LockOperationType.CRYPTO_ADDRESS_GENERATION,
            resource_id=str(user_id),
            timeout_seconds=timeout_seconds,
            metadata={
                'operation_type': operation_type,
                'service': 'crypto_idempotency_service'
            }
        )


# Global crypto idempotency service
crypto_idempotency_service = CryptoIdempotencyService()