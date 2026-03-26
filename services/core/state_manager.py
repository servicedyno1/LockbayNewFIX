"""
Unified State Manager - Central state transition management

Provides a single interface for all state transitions across the payment system.
Handles validation, logging, and database updates for the simplified 5-state system.

Key Features:
- Unified state transition management for all transaction types
- Automatic validation and error handling
- State change logging and audit trail
- Integration with database models
- Backward compatibility with legacy systems
"""

import logging
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.exc import SQLAlchemyError

from database import async_managed_session
from models import (
    UnifiedTransaction, UnifiedTransactionStatusHistory, Cashout, Escrow, ExchangeOrder, WalletHolds
)

from .payment_data_structures import (
    TransactionStatus, PaymentProvider, map_legacy_status, validate_state_transition,
    StateTransitionError
)

logger = logging.getLogger(__name__)


class StateTransitionContext:
    """Context information for state transitions"""
    def __init__(
        self,
        transaction_id: str,
        transaction_type: str,
        user_id: int,
        reason: str = "",
        metadata: Dict[str, Any] = None,
        provider: Optional[PaymentProvider] = None,
        external_reference: Optional[str] = None
    ):
        self.transaction_id = transaction_id
        self.transaction_type = transaction_type
        self.user_id = user_id
        self.reason = reason
        self.metadata = metadata or {}
        self.provider = provider
        self.external_reference = external_reference
        self.timestamp = datetime.utcnow()


class StateManager:
    """
    Unified State Manager for all payment transactions
    
    Provides centralized state management, validation, and transitions
    for all payment types using the simplified 5-state system.
    """
    
    def __init__(self):
        """Initialize the state manager"""
        self.logger = logging.getLogger(f"{__name__}.StateManager")
        
        # Mapping of legacy database models to their status fields
        self.model_status_fields = {
            UnifiedTransaction: 'status',
            Cashout: 'status',
            Escrow: 'status',
            ExchangeOrder: 'status',
            WalletHolds: 'status'
        }
        
        self.logger.info("🔄 StateManager initialized with 5-state system")
    
    async def transition_state(
        self,
        context: StateTransitionContext,
        target_status: TransactionStatus,
        skip_validation: bool = False
    ) -> bool:
        """
        Perform a state transition with full validation and logging
        
        Args:
            context: State transition context
            target_status: Target state
            skip_validation: Skip validation (for emergency/admin operations)
            
        Returns:
            bool: True if transition successful, False otherwise
        """
        try:
            self.logger.info(
                f"🔄 STATE_TRANSITION: {context.transaction_id} → {target_status.value} "
                f"({context.transaction_type}) - {context.reason}"
            )
            
            async with async_managed_session() as session:
                # Get current state
                current_status = await self._get_current_status(session, context)
                if current_status is None:
                    self.logger.error(f"❌ Transaction not found: {context.transaction_id}")
                    return False
                
                # Validate transition
                if not skip_validation:
                    try:
                        validate_state_transition(current_status, target_status)
                    except StateTransitionError as e:
                        self.logger.error(f"❌ Invalid transition: {e}")
                        return False
                
                # Perform transition
                success = await self._execute_transition(
                    session, context, current_status, target_status
                )
                
                if success:
                    # Log state change
                    await self._log_state_change(
                        session, context, current_status, target_status
                    )
                    
                    self.logger.info(
                        f"✅ STATE_TRANSITION_SUCCESS: {context.transaction_id} "
                        f"{current_status.value} → {target_status.value}"
                    )
                else:
                    self.logger.error(
                        f"❌ STATE_TRANSITION_FAILED: {context.transaction_id} "
                        f"{current_status.value} → {target_status.value}"
                    )
                
                return success
                
        except Exception as e:
            self.logger.error(
                f"❌ STATE_TRANSITION_ERROR: {context.transaction_id} "
                f"→ {target_status.value}: {e}"
            )
            return False
    
    async def get_transaction_status(
        self, 
        transaction_id: str,
        transaction_type: Optional[str] = None
    ) -> Optional[TransactionStatus]:
        """
        Get current status of a transaction
        
        Args:
            transaction_id: Transaction ID
            transaction_type: Optional transaction type for optimization
            
        Returns:
            TransactionStatus: Current status or None if not found
        """
        try:
            context = StateTransitionContext(
                transaction_id=transaction_id,
                transaction_type=transaction_type or "unknown",
                user_id=0,  # Not needed for read operations
                reason="status_check"
            )
            
            async with async_managed_session() as session:
                return await self._get_current_status(session, context)
                
        except Exception as e:
            self.logger.error(f"❌ Error getting status for {transaction_id}: {e}")
            return None
    
    async def batch_transition_states(
        self,
        transitions: List[Tuple[StateTransitionContext, TransactionStatus]]
    ) -> Dict[str, bool]:
        """
        Perform multiple state transitions in a single transaction
        
        Args:
            transitions: List of (context, target_status) tuples
            
        Returns:
            Dict[str, bool]: Results keyed by transaction_id
        """
        results = {}
        
        try:
            async with async_managed_session() as session:
                for context, target_status in transitions:
                    try:
                        # Get current status
                        current_status = await self._get_current_status(session, context)
                        if current_status is None:
                            results[context.transaction_id] = False
                            continue
                        
                        # Validate transition
                        try:
                            validate_state_transition(current_status, target_status)
                        except StateTransitionError:
                            results[context.transaction_id] = False
                            continue
                        
                        # Execute transition
                        success = await self._execute_transition(
                            session, context, current_status, target_status
                        )
                        results[context.transaction_id] = success
                        
                        if success:
                            # Log state change
                            await self._log_state_change(
                                session, context, current_status, target_status
                            )
                        
                    except Exception as e:
                        self.logger.error(
                            f"❌ Batch transition failed for {context.transaction_id}: {e}"
                        )
                        results[context.transaction_id] = False
                
                self.logger.info(
                    f"✅ BATCH_STATE_TRANSITIONS: {len(transitions)} attempted, "
                    f"{sum(results.values())} successful"
                )
                
        except Exception as e:
            self.logger.error(f"❌ Batch transition error: {e}")
            # Mark all as failed
            for context, _ in transitions:
                results[context.transaction_id] = False
        
        return results
    
    async def get_transactions_by_status(
        self,
        status: TransactionStatus,
        transaction_type: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get transactions in a specific status
        
        Args:
            status: Target status
            transaction_type: Optional filter by type
            limit: Maximum results
            
        Returns:
            List[Dict]: Transaction details
        """
        try:
            async with async_managed_session() as session:
                query = session.query(UnifiedTransaction).filter(
                    UnifiedTransaction.status == status.value
                )
                
                if transaction_type:
                    query = query.filter(
                        UnifiedTransaction.transaction_type == transaction_type
                    )
                
                transactions = query.limit(limit).all()
                
                results = []
                for tx in transactions:
                    results.append({
                        'transaction_id': tx.transaction_id,
                        'transaction_type': tx.transaction_type,
                        'user_id': tx.user_id,
                        'status': status.value,
                        'amount': float(tx.amount) if tx.amount else 0,
                        'currency': tx.currency,
                        'created_at': tx.created_at.isoformat() if tx.created_at else None,
                        'updated_at': tx.updated_at.isoformat() if tx.updated_at else None
                    })
                
                return results
                
        except Exception as e:
            self.logger.error(f"❌ Error getting transactions by status: {e}")
            return []
    
    async def sync_legacy_statuses(self, dry_run: bool = True) -> Dict[str, int]:
        """
        Sync legacy status fields to use the simplified 5-state system
        
        Args:
            dry_run: If True, only report what would be changed
            
        Returns:
            Dict[str, int]: Counts of changes per model type
        """
        results = {}
        
        try:
            async with async_managed_session() as session:
                # Sync UnifiedTransaction statuses
                unified_count = await self._sync_unified_transactions(session, dry_run)
                results['UnifiedTransaction'] = unified_count
                
                # Sync Cashout statuses
                cashout_count = await self._sync_cashouts(session, dry_run)
                results['Cashout'] = cashout_count
                
                # Sync Escrow statuses
                escrow_count = await self._sync_escrows(session, dry_run)
                results['Escrow'] = escrow_count
                
                # Sync Exchange statuses
                exchange_count = await self._sync_exchanges(session, dry_run)
                results['ExchangeOrder'] = exchange_count
                
                self.logger.info(
                    f"{'🔍 DRY_RUN' if dry_run else '✅'} LEGACY_SYNC: {results}"
                )
                
        except Exception as e:
            self.logger.error(f"❌ Legacy sync error: {e}")
        
        return results
    
    async def _get_current_status(
        self, 
        session: Session, 
        context: StateTransitionContext
    ) -> Optional[TransactionStatus]:
        """Get current status from database"""
        try:
            # Try UnifiedTransaction first
            unified_tx = session.query(UnifiedTransaction).filter(
                UnifiedTransaction.transaction_id == context.transaction_id
            ).first()
            
            if unified_tx and unified_tx.status:
                return map_legacy_status(unified_tx.status)
            
            # Try legacy models based on transaction type
            if "cashout" in context.transaction_type.lower():
                cashout = session.query(Cashout).filter(
                    Cashout.id == context.transaction_id.replace('CSH', '').replace('UTX', '')
                ).first()
                if cashout and cashout.status:
                    return map_legacy_status(cashout.status)
            
            # Add other legacy model lookups as needed
            return None
            
        except Exception as e:
            self.logger.error(f"❌ Error getting current status: {e}")
            return None
    
    async def _execute_transition(
        self,
        session: Session,
        context: StateTransitionContext,
        current_status: TransactionStatus,
        target_status: TransactionStatus
    ) -> bool:
        """Execute the actual state transition in database"""
        try:
            # Update UnifiedTransaction
            unified_tx = session.query(UnifiedTransaction).filter(
                UnifiedTransaction.transaction_id == context.transaction_id
            ).first()
            
            if unified_tx:
                unified_tx.status = target_status.value
                unified_tx.updated_at = context.timestamp
                
                # Add metadata if provided
                if context.metadata:
                    current_metadata = unified_tx.metadata or {}
                    current_metadata.update(context.metadata)
                    unified_tx.metadata = current_metadata
            
            # Update legacy models as needed (for backward compatibility)
            await self._update_legacy_models(session, context, target_status)
            
            session.commit()
            return True
            
        except SQLAlchemyError as e:
            self.logger.error(f"❌ Database error during transition: {e}")
            session.rollback()
            return False
        except Exception as e:
            self.logger.error(f"❌ Transition execution error: {e}")
            session.rollback()
            return False
    
    async def _update_legacy_models(
        self,
        session: Session,
        context: StateTransitionContext,
        target_status: TransactionStatus
    ) -> None:
        """Update legacy model status fields for backward compatibility"""
        try:
            # Convert back to legacy status format if needed
            legacy_status = self._convert_to_legacy_status(
                target_status, context.transaction_type
            )
            
            # Update based on transaction type
            if "cashout" in context.transaction_type.lower():
                session.query(Cashout).filter(
                    Cashout.id == context.transaction_id.replace('CSH', '').replace('UTX', '')
                ).update({
                    'status': legacy_status,
                    'updated_at': context.timestamp
                })
            
            # Add other legacy model updates as needed
            
        except Exception as e:
            self.logger.error(f"❌ Error updating legacy models: {e}")
    
    async def _log_state_change(
        self,
        session: Session,
        context: StateTransitionContext,
        from_status: TransactionStatus,
        to_status: TransactionStatus
    ) -> None:
        """Log state change for audit trail"""
        try:
            # Create status history entry
            history_entry = UnifiedTransactionStatusHistory(
                transaction_id=context.transaction_id,
                previous_status=from_status.value,
                new_status=to_status.value,
                changed_by="system",
                reason=context.reason,
                metadata=context.metadata,
                created_at=context.timestamp
            )
            session.add(history_entry)
            
        except Exception as e:
            self.logger.error(f"❌ Error logging state change: {e}")
    
    def _convert_to_legacy_status(
        self, 
        unified_status: TransactionStatus, 
        transaction_type: str
    ) -> str:
        """Convert unified status back to legacy format"""
        # Simple mapping for now - can be enhanced based on transaction type
        legacy_mappings = {
            TransactionStatus.PENDING: "pending",
            TransactionStatus.PROCESSING: "processing",
            TransactionStatus.AWAITING: "awaiting_response",
            TransactionStatus.SUCCESS: "success",
            TransactionStatus.FAILED: "failed"
        }
        
        return legacy_mappings.get(unified_status, unified_status.value)
    
    async def _sync_unified_transactions(self, session: Session, dry_run: bool) -> int:
        """Sync UnifiedTransaction statuses"""
        # Implementation for syncing existing records
        return 0  # Placeholder
    
    async def _sync_cashouts(self, session: Session, dry_run: bool) -> int:
        """Sync Cashout statuses"""
        # Implementation for syncing existing records
        return 0  # Placeholder
    
    async def _sync_escrows(self, session: Session, dry_run: bool) -> int:
        """Sync Escrow statuses"""
        # Implementation for syncing existing records
        return 0  # Placeholder
    
    async def _sync_exchanges(self, session: Session, dry_run: bool) -> int:
        """Sync ExchangeOrder statuses"""
        # Implementation for syncing existing records
        return 0  # Placeholder


# Global instance
state_manager = StateManager()


# Convenience functions for common operations
async def transition_to_processing(
    transaction_id: str,
    transaction_type: str,
    user_id: int,
    reason: str = "Processing started",
    **kwargs
) -> bool:
    """Transition transaction to PROCESSING state"""
    context = StateTransitionContext(
        transaction_id=transaction_id,
        transaction_type=transaction_type,
        user_id=user_id,
        reason=reason,
        **kwargs
    )
    return await state_manager.transition_state(context, TransactionStatus.PROCESSING)


async def transition_to_success(
    transaction_id: str,
    transaction_type: str,
    user_id: int,
    reason: str = "Processing completed successfully",
    **kwargs
) -> bool:
    """Transition transaction to SUCCESS state"""
    context = StateTransitionContext(
        transaction_id=transaction_id,
        transaction_type=transaction_type,
        user_id=user_id,
        reason=reason,
        **kwargs
    )
    return await state_manager.transition_state(context, TransactionStatus.SUCCESS)


async def transition_to_failed(
    transaction_id: str,
    transaction_type: str,
    user_id: int,
    reason: str = "Processing failed",
    **kwargs
) -> bool:
    """Transition transaction to FAILED state"""
    context = StateTransitionContext(
        transaction_id=transaction_id,
        transaction_type=transaction_type,
        user_id=user_id,
        reason=reason,
        **kwargs
    )
    return await state_manager.transition_state(context, TransactionStatus.FAILED)


async def transition_to_awaiting(
    transaction_id: str,
    transaction_type: str,
    user_id: int,
    reason: str = "Awaiting external action",
    **kwargs
) -> bool:
    """Transition transaction to AWAITING state"""
    context = StateTransitionContext(
        transaction_id=transaction_id,
        transaction_type=transaction_type,
        user_id=user_id,
        reason=reason,
        **kwargs
    )
    return await state_manager.transition_state(context, TransactionStatus.AWAITING)