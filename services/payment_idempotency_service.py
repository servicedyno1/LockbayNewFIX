"""
Centralized Payment Idempotency Service
Provides standardized duplicate prevention for all payment webhook handlers
"""

import logging
from typing import Dict, Any, Tuple
from sqlalchemy.orm import Session
from models import Transaction, TransactionType
from utils.distributed_lock import distributed_lock_service
from utils.atomic_transactions import atomic_transaction

logger = logging.getLogger(__name__)


class PaymentIdempotencyService:
    """Centralized service for payment idempotency across all webhook handlers"""

    @staticmethod
    async def process_payment_with_idempotency(
        callback_source: str,
        order_id: str,
        external_tx_id: str,
        payment_data: Dict[str, Any],
        payment_processor: callable,
        timeout: int = 120
    ) -> Dict[str, Any]:
        """
        Process payment with comprehensive idempotency protection
        
        Args:
            callback_source: Source of callback (dynopay, blockbee, fincra, etc.)
            order_id: Order/escrow/exchange identifier
            external_tx_id: External transaction ID from payment provider
            payment_data: Payment data to process
            payment_processor: Async function to process the payment
            timeout: Lock timeout in seconds
            
        Returns:
            Dict containing processing result
        """
        try:
            # Validate required parameters
            if not external_tx_id:
                logger.error(f"{callback_source}: Missing external transaction ID")
                return {
                    "status": "error",
                    "reason": "missing_external_tx_id",
                    "message": "External transaction ID is required"
                }
            
            if not order_id:
                logger.error(f"{callback_source}: Missing order ID")
                return {
                    "status": "error", 
                    "reason": "missing_order_id",
                    "message": "Order ID is required"
                }
            
            logger.info(
                f"{callback_source}: Processing payment - Order: {order_id}, "
                f"External TX: {external_tx_id}"
            )
            
            # Acquire distributed lock for this payment
            additional_data = {
                "callback_source": callback_source,
                "payment_data": payment_data
            }
            
            with distributed_lock_service.acquire_payment_lock(
                order_id=str(order_id),
                txid=external_tx_id,
                timeout=timeout,
                additional_data=additional_data
            ) as lock:
                
                if not lock.acquired:
                    logger.warning(
                        f"{callback_source}: RACE_CONDITION_PREVENTED - Could not acquire lock "
                        f"for order {order_id}, tx {external_tx_id}. Reason: {lock.error}"
                    )
                    return {
                        "status": "already_processing", 
                        "message": "Payment is being processed by another instance"
                    }
                
                logger.critical(
                    f"{callback_source}: DISTRIBUTED_LOCK_ACQUIRED - Processing payment "
                    f"for order {order_id}, tx {external_tx_id} with exclusive lock"
                )
                
                # Process within lock context
                return await PaymentIdempotencyService._process_locked_payment(
                    callback_source, order_id, external_tx_id, payment_processor
                )
                
        except Exception as e:
            logger.error(f"{callback_source}: Error in idempotent payment processing: {e}", exc_info=True)
            return {
                "status": "error",
                "reason": "processing_error", 
                "message": "Internal processing error"
            }
    
    @staticmethod
    async def _process_locked_payment(
        callback_source: str,
        order_id: str,
        external_tx_id: str,
        payment_processor: callable
    ) -> Dict[str, Any]:
        """Process payment within distributed lock context"""
        try:
            # Check for existing transactions using multiple methods
            duplicate_check = await PaymentIdempotencyService.check_for_duplicates(
                callback_source, external_tx_id, order_id
            )
            
            if duplicate_check["is_duplicate"]:
                logger.warning(
                    f"{callback_source}: DUPLICATE_DETECTED - {duplicate_check['reason']} "
                    f"for tx {external_tx_id}"
                )
                return {
                    "status": "already_processed",
                    "transaction_id": duplicate_check.get("existing_transaction_id"),
                    "reason": duplicate_check["reason"],
                    "message": "Payment already processed"
                }
            
            # Process the payment
            logger.info(f"{callback_source}: Processing new payment for tx {external_tx_id}")
            result = await payment_processor()
            
            # Log successful processing
            if result.get("status") == "success":
                logger.info(
                    f"{callback_source}: ✅ Payment processed successfully - "
                    f"Order: {order_id}, TX: {external_tx_id}"
                )
            
            return result
            
        except Exception as e:
            logger.error(
                f"{callback_source}: Error in locked payment processing: {e}", 
                exc_info=True
            )
            raise
    
    @staticmethod
    async def check_for_duplicates(
        callback_source: str,
        external_tx_id: str,
        order_id: str = None
    ) -> Dict[str, Any]:
        """
        Comprehensive duplicate detection using multiple methods
        
        Returns:
            Dict with is_duplicate, reason, and existing_transaction_id
        """
        try:
            with atomic_transaction() as session:
                
                # METHOD 1: Check by external tx_hash globally
                existing_by_tx_hash = session.query(Transaction).filter_by(
                    tx_hash=external_tx_id
                ).first()
                
                if existing_by_tx_hash:
                    logger.info(f"Duplicate found by tx_hash: {external_tx_id}")
                    return {
                        "is_duplicate": True,
                        "reason": "duplicate_tx_hash_global",
                        "existing_transaction_id": existing_by_tx_hash.transaction_id,
                        "method": "tx_hash_lookup"
                    }
                
                # METHOD 2: Check by blockchain_address (for BlockBee txid_in)
                existing_by_blockchain = session.query(Transaction).filter_by(
                    blockchain_address=external_tx_id
                ).first()
                
                if existing_by_blockchain:
                    logger.info(f"Duplicate found by blockchain_address: {external_tx_id}")
                    return {
                        "is_duplicate": True,
                        "reason": "duplicate_blockchain_address",
                        "existing_transaction_id": existing_by_blockchain.transaction_id,
                        "method": "blockchain_address_lookup"
                    }
                
                # METHOD 3: Order-specific duplicate checks (if order_id provided)
                if order_id:
                    # Check for existing deposits to this order with same external reference
                    existing_by_order = session.query(Transaction).filter(
                        Transaction.escrow_id == order_id,
                        Transaction.transaction_type == TransactionType.DEPOSIT.value,
                        Transaction.tx_hash == external_tx_id
                    ).first()
                    
                    if existing_by_order:
                        logger.info(f"Duplicate found by order+tx_hash: order {order_id}, tx {external_tx_id}")
                        return {
                            "is_duplicate": True,
                            "reason": "duplicate_order_deposit",
                            "existing_transaction_id": existing_by_order.transaction_id,
                            "method": "order_tx_lookup"
                        }
                
                # No duplicates found
                return {
                    "is_duplicate": False,
                    "reason": "no_duplicates_found",
                    "method": "comprehensive_check"
                }
                
        except Exception as e:
            logger.error(f"Error checking for duplicates: {e}")
            # Return conservative result - assume no duplicate to avoid blocking legitimate payments
            return {
                "is_duplicate": False,
                "reason": "check_error",
                "error": str(e)
            }
    
    @staticmethod
    def log_duplicate_attempt(
        callback_source: str,
        external_tx_id: str,
        order_id: str,
        detection_method: str,
        existing_transaction_id: str = None
    ):
        """Log duplicate payment attempt for monitoring and security"""
        logger.warning(
            f"🔒 DUPLICATE_PAYMENT_BLOCKED: Source={callback_source}, "
            f"Order={order_id}, ExternalTX={external_tx_id}, "
            f"Method={detection_method}, ExistingTX={existing_transaction_id}"
        )
        
        # Additional security monitoring could be added here
        # e.g., increment counter, send admin alert if too many duplicates
    
    @staticmethod
    async def validate_payment_constraints(
        session: Session,
        external_tx_id: str,
        user_id: int = None,
        order_id: str = None,
        transaction_type: str = TransactionType.DEPOSIT.value
    ) -> Tuple[bool, str]:
        """
        Validate that payment meets database constraint requirements
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            # Check global tx_hash uniqueness
            if external_tx_id:
                existing_tx = session.query(Transaction).filter_by(
                    tx_hash=external_tx_id
                ).first()
                
                if existing_tx:
                    return False, f"Transaction hash {external_tx_id} already exists"
            
            # Check escrow-specific constraints if order provided
            if order_id and external_tx_id:
                existing_escrow_tx = session.query(Transaction).filter(
                    Transaction.escrow_id == order_id,
                    Transaction.tx_hash == external_tx_id,
                    Transaction.transaction_type == transaction_type
                ).first()
                
                if existing_escrow_tx:
                    return False, f"Order {order_id} already has transaction with hash {external_tx_id}"
            
            return True, "Validation passed"
            
        except Exception as e:
            logger.error(f"Error validating payment constraints: {e}")
            return False, f"Validation error: {str(e)}"


# Convenience wrapper functions for specific payment providers
class DynoPayIdempotency:
    """DynoPay-specific idempotency helpers"""
    
    @staticmethod
    async def process_escrow_deposit(webhook_data: Dict[str, Any], processor: callable) -> Dict[str, Any]:
        meta_data = webhook_data.get('meta_data', {})
        reference_id = meta_data.get('refId')
        transaction_id = webhook_data.get('id')
        
        return await PaymentIdempotencyService.process_payment_with_idempotency(
            callback_source="dynopay_escrow",
            order_id=reference_id,
            external_tx_id=transaction_id,
            payment_data=webhook_data,
            payment_processor=processor
        )
    
    @staticmethod
    async def process_exchange_deposit(webhook_data: Dict[str, Any], processor: callable) -> Dict[str, Any]:
        meta_data = webhook_data.get('meta_data', {})
        reference_id = meta_data.get('refId')
        transaction_id = webhook_data.get('id')
        
        return await PaymentIdempotencyService.process_payment_with_idempotency(
            callback_source="dynopay_exchange",
            order_id=reference_id,
            external_tx_id=transaction_id,
            payment_data=webhook_data,
            payment_processor=processor
        )


class BlockBeeIdempotency:
    """BlockBee-specific idempotency helpers"""
    
    @staticmethod
    async def process_callback(callback_data: Dict[str, Any], processor: callable) -> Dict[str, Any]:
        order_id = callback_data.get("params", {}).get("order_id")
        txid_in = callback_data.get("txid_in")
        
        return await PaymentIdempotencyService.process_payment_with_idempotency(
            callback_source="blockbee",
            order_id=order_id,
            external_tx_id=txid_in,
            payment_data=callback_data,
            payment_processor=processor
        )


class FincraIdempotency:
    """Fincra-specific idempotency helpers"""
    
    @staticmethod
    async def process_payment(payment_data: Dict[str, Any], processor: callable) -> Dict[str, Any]:
        reference = payment_data.get("reference")
        
        return await PaymentIdempotencyService.process_payment_with_idempotency(
            callback_source="fincra",
            order_id=reference,
            external_tx_id=f"fincra_{reference}",
            payment_data=payment_data,
            payment_processor=processor
        )