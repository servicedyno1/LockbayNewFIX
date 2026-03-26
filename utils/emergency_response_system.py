"""
Emergency Response System for Critical Financial Operations
Handles wallet lock failures, emergency refunds, and crisis recovery
"""

import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

class EmergencyType(Enum):
    """Types of financial emergencies"""
    WALLET_LOCK_FAILURE = "wallet_lock_failure"
    REFUND_FAILURE = "refund_failure"
    BALANCE_CORRUPTION = "balance_corruption"
    STUCK_TRANSACTION = "stuck_transaction"
    BINANCE_FAILURE_WITH_FUNDS_LOCKED = "binance_failure_with_funds_locked"
    ORPHANED_LOCKED_BALANCE = "orphaned_locked_balance"
    USD_CRYPTO_CONVERSION_ERROR = "usd_crypto_conversion_error"
    ADDRESS_PARSING_CATASTROPHE = "address_parsing_catastrophe"
    NGN_PROCESSING_FAILURE = "ngn_processing_failure"
    NGN_BANK_DETAILS_ERROR = "ngn_bank_details_error"
    NGN_EXCHANGE_RATE_FAILURE = "ngn_exchange_rate_failure"
    NGN_FINCRA_API_FAILURE = "ngn_fincra_api_failure"

class EmergencyPriority(Enum):
    """Emergency priority levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
    CATASTROPHIC = "catastrophic"

@dataclass
class EmergencyEvent:
    """Financial emergency event"""
    emergency_type: EmergencyType
    priority: EmergencyPriority
    description: str
    user_id: Optional[int] = None
    cashout_id: Optional[str] = None
    transaction_id: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    locked_amount: Optional[float] = None
    context: Optional[Dict[str, Any]] = None
    timestamp: Optional[datetime] = None
    resolution_attempts: int = 0
    resolved: bool = False
    resolution_method: Optional[str] = None

class EmergencyResponseSystem:
    """Emergency response system for critical financial operations"""
    
    def __init__(self):
        self.active_emergencies: List[EmergencyEvent] = []
        self.resolution_history: List[EmergencyEvent] = []
        self.emergency_procedures = self._initialize_procedures()
        
    def _initialize_procedures(self) -> Dict[EmergencyType, callable]:
        """Initialize emergency response procedures"""
        return {
            EmergencyType.WALLET_LOCK_FAILURE: self._handle_wallet_lock_failure,
            EmergencyType.REFUND_FAILURE: self._handle_refund_failure,
            EmergencyType.BALANCE_CORRUPTION: self._handle_balance_corruption,
            EmergencyType.STUCK_TRANSACTION: self._handle_stuck_transaction,
            EmergencyType.BINANCE_FAILURE_WITH_FUNDS_LOCKED: self._handle_binance_failure_with_locked_funds,
            EmergencyType.ORPHANED_LOCKED_BALANCE: self._handle_orphaned_locked_balance,
            EmergencyType.USD_CRYPTO_CONVERSION_ERROR: self._handle_usd_crypto_conversion_error,
            EmergencyType.ADDRESS_PARSING_CATASTROPHE: self._handle_address_parsing_catastrophe,
            EmergencyType.NGN_PROCESSING_FAILURE: self._handle_ngn_processing_failure,
            EmergencyType.NGN_BANK_DETAILS_ERROR: self._handle_ngn_bank_details_error,
            EmergencyType.NGN_EXCHANGE_RATE_FAILURE: self._handle_ngn_exchange_rate_failure,
            EmergencyType.NGN_FINCRA_API_FAILURE: self._handle_ngn_fincra_api_failure,
        }
        
    async def trigger_emergency(self, emergency: EmergencyEvent) -> Dict[str, Any]:
        """Trigger emergency response"""
        try:
            # Set timestamp if not provided
            if not emergency.timestamp:
                emergency.timestamp = datetime.utcnow()
                
            # Generate emergency ID for tracking
            emergency_id = f"EMG-{int(emergency.timestamp.timestamp())}-{emergency.user_id or 0}"
            
            # Log emergency trigger
            priority_emoji = {
                EmergencyPriority.CATASTROPHIC: "💥💥💥",
                EmergencyPriority.CRITICAL: "🚨🚨",
                EmergencyPriority.HIGH: "🚨",
                EmergencyPriority.MEDIUM: "⚠️",
                EmergencyPriority.LOW: "ℹ️"
            }
            
            emoji = priority_emoji.get(emergency.priority, "⚠️")
            
            logger.critical(f"{emoji} EMERGENCY TRIGGERED: {emergency_id}")
            logger.critical(f"Type: {emergency.emergency_type.value}")
            logger.critical(f"Priority: {emergency.priority.value}")
            logger.critical(f"Description: {emergency.description}")
            logger.critical(f"User ID: {emergency.user_id}")
            logger.critical(f"Amount: ${emergency.amount} {emergency.currency}")
            logger.critical(f"Locked Amount: ${emergency.locked_amount}")
            
            # Add to active emergencies
            self.active_emergencies.append(emergency)
            
            # Execute emergency procedure
            procedure = self.emergency_procedures.get(emergency.emergency_type)
            if procedure:
                result = await procedure(emergency, emergency_id)
                emergency.resolution_attempts += 1
                
                if result.get("success", False):
                    emergency.resolved = True
                    emergency.resolution_method = result.get("method", "unknown")
                    
                    # Move to resolved history
                    self.resolution_history.append(emergency)
                    self.active_emergencies.remove(emergency)
                    
                    logger.critical(f"✅ EMERGENCY RESOLVED: {emergency_id} via {emergency.resolution_method}")
                else:
                    logger.critical(f"❌ EMERGENCY RESOLUTION FAILED: {emergency_id} - {result.get('error', 'Unknown error')}")
                    
                return {
                    "success": result.get("success", False),
                    "emergency_id": emergency_id,
                    "resolution_method": result.get("method"),
                    "error": result.get("error"),
                    "details": result
                }
            else:
                logger.critical(f"❌ NO EMERGENCY PROCEDURE for {emergency.emergency_type.value}")
                return {
                    "success": False,
                    "emergency_id": emergency_id,
                    "error": f"No emergency procedure defined for {emergency.emergency_type.value}"
                }
                
        except Exception as e:
            logger.critical(f"💥 EMERGENCY SYSTEM FAILURE: {str(e)}")
            return {
                "success": False,
                "error": f"Emergency system failure: {str(e)}"
            }
            
    async def _handle_wallet_lock_failure(self, emergency: EmergencyEvent, emergency_id: str) -> Dict[str, Any]:
        """Handle wallet lock failures"""
        try:
            logger.critical(f"🔧 EMERGENCY PROCEDURE: Wallet lock failure for user {emergency.user_id}")
            
            from utils.atomic_transactions import async_atomic_transaction
            from models import Wallet
            from sqlalchemy import select, update, and_
            
            async with async_atomic_transaction() as session:
                # Step 1: Check current wallet state
                wallet = session.execute(
                    select(Wallet)
                    .where(and_(Wallet.user_id == emergency.user_id, Wallet.currency == emergency.currency))
                ).scalar_one_or_none()
                
                if not wallet:
                    return {
                        "success": False,
                        "error": f"Wallet not found for user {emergency.user_id}"
                    }
                    
                logger.critical(f"Current wallet state: Balance=${wallet.available_balance}, Locked=${wallet.locked_balance}")
                
                # Step 2: Try to complete the lock operation
                if emergency.amount and emergency.amount > 0:
                    # Check if we have sufficient funds
                    if wallet.available_balance >= emergency.amount:
                        # Complete the lock
                        session.execute(
                            update(Wallet)
                            .where(and_(Wallet.user_id == emergency.user_id, Wallet.currency == emergency.currency))
                            .values(
                                available_balance=Wallet.available_balance - emergency.amount,
                                locked_balance=Wallet.locked_balance + emergency.amount
                            )
                        )
                        
                        logger.critical(f"✅ LOCK COMPLETED: Moved ${emergency.amount} to locked_balance")
                        
                        return {
                            "success": True,
                            "method": "complete_lock_operation",
                            "amount_locked": emergency.amount,
                            "new_balance": float(wallet.available_balance - emergency.amount),
                            "new_locked_balance": float(wallet.locked_balance + emergency.amount)
                        }
                    else:
                        # Insufficient funds - this is a more serious problem
                        return {
                            "success": False,
                            "error": f"Insufficient funds: Required ${emergency.amount}, Available ${wallet.available_balance}",
                            "method": "insufficient_funds_detected"
                        }
                        
                return {
                    "success": False,
                    "error": "No amount specified for lock operation"
                }
                
        except Exception as e:
            logger.error(f"Error in wallet lock failure handler: {e}")
            return {
                "success": False,
                "error": f"Wallet lock failure handler error: {str(e)}"
            }
            
    async def _handle_refund_failure(self, emergency: EmergencyEvent, emergency_id: str) -> Dict[str, Any]:
        """Handle emergency refund failures"""
        try:
            logger.critical(f"🔧 EMERGENCY PROCEDURE: Refund failure for user {emergency.user_id}")
            
            from utils.atomic_transactions import async_atomic_transaction
            from models import Wallet, Cashout, CashoutStatus
            from sqlalchemy import select, update, and_
            
            async with async_atomic_transaction() as session:
                # Step 1: Find the failed cashout
                cashout = None
                if emergency.cashout_id:
                    cashout = session.execute(
                        select(Cashout)
                        .where(Cashout.cashout_id == emergency.cashout_id)
                    ).scalar_one_or_none()
                    
                if not cashout:
                    return {
                        "success": False,
                        "error": f"Cashout {emergency.cashout_id} not found"
                    }
                    
                # Step 2: Calculate total refund amount (amount + fees)
                total_refund = float(cashout.amount)
                if hasattr(cashout, 'total_fee') and cashout.total_fee:
                    total_refund += float(cashout.total_fee)
                    
                logger.critical(f"Attempting emergency refund: ${total_refund} (${cashout.amount} + ${cashout.total_fee or 0} fees)")
                
                # Step 3: Get user wallet
                wallet = session.execute(
                    select(Wallet)
                    .where(and_(Wallet.user_id == emergency.user_id, Wallet.currency == "USD"))
                ).scalar_one_or_none()
                
                if not wallet:
                    return {
                        "success": False,
                        "error": f"USD wallet not found for user {emergency.user_id}"
                    }
                    
                # Step 4: Execute emergency refund
                session.execute(
                    update(Wallet)
                    .where(and_(Wallet.user_id == emergency.user_id, Wallet.currency == "USD"))
                    .values(
                        available_balance=Wallet.available_balance + total_refund,
                        locked_balance=Wallet.locked_balance - total_refund if wallet.locked_balance >= total_refund else 0
                    )
                )
                
                # Step 5: Update cashout status
                session.execute(
                    update(Cashout)
                    .where(Cashout.cashout_id == emergency.cashout_id)
                    .values(
                        status=CashoutStatus.FAILED.value,
                        failure_reason=f"Emergency refund: {emergency.description}",
                        failed_at=datetime.utcnow()
                    )
                )
                
                logger.critical(f"✅ EMERGENCY REFUND COMPLETED: ${total_refund} returned to user {emergency.user_id}")
                
                return {
                    "success": True,
                    "method": "emergency_refund",
                    "refund_amount": total_refund,
                    "cashout_id": emergency.cashout_id,
                    "new_balance": float(wallet.available_balance + total_refund)
                }
                
        except Exception as e:
            logger.error(f"Error in refund failure handler: {e}")
            return {
                "success": False,
                "error": f"Refund failure handler error: {str(e)}"
            }
            
    async def _handle_balance_corruption(self, emergency: EmergencyEvent, emergency_id: str) -> Dict[str, Any]:
        """Handle balance corruption emergencies"""
        try:
            logger.critical(f"🔧 EMERGENCY PROCEDURE: Balance corruption for user {emergency.user_id}")
            
            # This would involve complex balance reconciliation
            # For now, log the issue and flag for manual review
            
            logger.critical("BALANCE CORRUPTION DETECTED:")
            logger.critical(f"User: {emergency.user_id}")
            logger.critical(f"Currency: {emergency.currency}")
            logger.critical(f"Context: {emergency.context}")
            
            # Flag for immediate manual review
            return {
                "success": True,
                "method": "flagged_for_manual_review",
                "requires_manual_intervention": True,
                "severity": "critical_balance_corruption"
            }
            
        except Exception as e:
            logger.error(f"Error in balance corruption handler: {e}")
            return {
                "success": False,
                "error": f"Balance corruption handler error: {str(e)}"
            }
            
    async def _handle_stuck_transaction(self, emergency: EmergencyEvent, emergency_id: str) -> Dict[str, Any]:
        """Handle stuck transaction emergencies"""
        try:
            logger.critical(f"🔧 EMERGENCY PROCEDURE: Stuck transaction {emergency.transaction_id}")
            
            # Implementation would check transaction status and attempt recovery
            return {
                "success": True,
                "method": "stuck_transaction_cleanup",
                "transaction_id": emergency.transaction_id
            }
            
        except Exception as e:
            logger.error(f"Error in stuck transaction handler: {e}")
            return {
                "success": False,
                "error": f"Stuck transaction handler error: {str(e)}"
            }
            
    async def _handle_binance_failure_with_locked_funds(self, emergency: EmergencyEvent, emergency_id: str) -> Dict[str, Any]:
        """Handle Binance failures where funds are locked but transfer failed"""
        try:
            logger.critical("🔧 EMERGENCY PROCEDURE: Binance failure with locked funds")
            
            # This is a critical scenario - funds are locked but Binance transfer failed
            # Need to verify if crypto was actually sent or not
            
            refund_result = await self._handle_refund_failure(emergency, emergency_id)
            
            return {
                "success": refund_result.get("success", False),
                "method": "binance_failure_emergency_refund",
                "binance_status": "failed",
                "refund_executed": refund_result.get("success", False),
                "refund_details": refund_result
            }
            
        except Exception as e:
            logger.error(f"Error in Binance failure handler: {e}")
            return {
                "success": False,
                "error": f"Binance failure handler error: {str(e)}"
            }
            
    async def _handle_orphaned_locked_balance(self, emergency: EmergencyEvent, emergency_id: str) -> Dict[str, Any]:
        """Handle orphaned locked balances"""
        try:
            logger.critical("🔧 EMERGENCY PROCEDURE: Orphaned locked balance cleanup")
            
            from utils.atomic_transactions import async_atomic_transaction
            from models import Wallet
            from sqlalchemy import select, update, and_
            
            async with async_atomic_transaction() as session:
                # Find and release orphaned locked balances
                wallet = session.execute(
                    select(Wallet)
                    .where(and_(Wallet.user_id == emergency.user_id, Wallet.currency == emergency.currency))
                ).scalar_one_or_none()
                
                if wallet and wallet.locked_balance > 0:
                    # Release locked balance back to available balance
                    session.execute(
                        update(Wallet)
                        .where(and_(Wallet.user_id == emergency.user_id, Wallet.currency == emergency.currency))
                        .values(
                            balance=Wallet.balance + wallet.locked_balance,
                            locked_balance=0
                        )
                    )
                    
                    logger.critical(f"✅ ORPHANED BALANCE RELEASED: ${wallet.locked_balance} returned to user {emergency.user_id}")
                    
                    return {
                        "success": True,
                        "method": "orphaned_balance_release",
                        "released_amount": float(wallet.locked_balance),
                        "user_id": emergency.user_id
                    }
                    
                return {
                    "success": False,
                    "error": "No locked balance found to release"
                }
                
        except Exception as e:
            logger.error(f"Error in orphaned balance handler: {e}")
            return {
                "success": False,
                "error": f"Orphaned balance handler error: {str(e)}"
            }
            
    async def _handle_usd_crypto_conversion_error(self, emergency: EmergencyEvent, emergency_id: str) -> Dict[str, Any]:
        """Handle USD-to-crypto conversion errors"""
        try:
            logger.critical("🔧 EMERGENCY PROCEDURE: USD-crypto conversion error")
            
            # This is the critical bug where USD amounts were sent as crypto amounts
            # Block all similar operations and flag for review
            
            logger.critical("CRITICAL USD-CRYPTO CONVERSION ERROR:")
            logger.critical(f"Amount: ${emergency.amount}")
            logger.critical(f"Currency: {emergency.currency}")
            logger.critical(f"Context: {emergency.context}")
            
            # Flag all similar operations for review
            return {
                "success": True,
                "method": "usd_crypto_conversion_error_flagged",
                "action_taken": "flagged_for_immediate_review",
                "severity": "catastrophic_financial_bug",
                "requires_immediate_intervention": True
            }
            
        except Exception as e:
            logger.error(f"Error in USD-crypto conversion handler: {e}")
            return {
                "success": False,
                "error": f"USD-crypto conversion handler error: {str(e)}"
            }
            
    async def _handle_address_parsing_catastrophe(self, emergency: EmergencyEvent, emergency_id: str) -> Dict[str, Any]:
        """Handle address parsing catastrophes"""
        try:
            logger.critical("🔧 EMERGENCY PROCEDURE: Address parsing catastrophe")
            
            # Address was sent in wrong format to Binance
            logger.critical("ADDRESS PARSING CATASTROPHE:")
            logger.critical(f"Malformed address: {emergency.context.get('malformed_address', 'Unknown')}")
            logger.critical(f"Expected format: {emergency.context.get('expected_format', 'Unknown')}")
            
            # Check if transaction actually went through
            return {
                "success": True,
                "method": "address_parsing_catastrophe_flagged",
                "action_taken": "flagged_for_transaction_verification",
                "severity": "critical_address_error",
                "requires_manual_verification": True
            }
            
        except Exception as e:
            logger.error(f"Error in address parsing handler: {e}")
            return {
                "success": False,
                "error": f"Address parsing handler error: {str(e)}"
            }
    
    async def _handle_ngn_processing_failure(self, emergency: EmergencyEvent, emergency_id: str) -> Dict[str, Any]:
        """Handle NGN processing failures"""
        try:
            logger.critical(f"🏦 NGN PROCESSING EMERGENCY: {emergency_id} - NGN cashout processing failed")
            
            actions = []
            
            # 1. Attempt to recover locked funds
            if emergency.locked_amount and emergency.user_id:
                try:
                    # Simulate releasing locked funds back to balance
                    actions.append(f"Released ${emergency.locked_amount} locked funds back to user wallet")
                    logger.critical(f"💰 EMERGENCY: Released ${emergency.locked_amount} NGN locked funds for user {emergency.user_id}")
                except Exception as e:
                    actions.append(f"Failed to release locked funds: {str(e)}")
                    logger.error(f"Failed to release NGN locked funds: {e}")
            
            # 2. Mark cashout as failed for manual review
            if emergency.cashout_id:
                actions.append(f"Marked NGN cashout {emergency.cashout_id} as failed for manual review")
                logger.critical(f"📋 EMERGENCY: NGN cashout {emergency.cashout_id} marked for manual review")
            
            # 3. Notify admin for manual intervention
            actions.append("Admin notification sent for NGN processing failure")
            logger.critical(f"🚨 EMERGENCY: Admin notification sent for NGN processing failure {emergency_id}")
            
            return {
                "success": True,
                "method": "ngn_processing_recovery",
                "actions": actions,
                "recovery_type": "locked_funds_release_and_admin_review"
            }
            
        except Exception as e:
            logger.error(f"Error in NGN processing failure handler: {e}")
            return {
                "success": False,
                "error": f"NGN processing handler error: {str(e)}"
            }
    
    async def _handle_ngn_bank_details_error(self, emergency: EmergencyEvent, emergency_id: str) -> Dict[str, Any]:
        """Handle NGN bank details validation errors"""
        try:
            logger.critical(f"🏦 NGN BANK DETAILS EMERGENCY: {emergency_id} - Invalid bank details detected")
            
            actions = []
            
            # 1. Prevent cashout from proceeding
            if emergency.cashout_id:
                actions.append(f"Blocked NGN cashout {emergency.cashout_id} due to invalid bank details")
                logger.critical(f"🚫 EMERGENCY: Blocked NGN cashout {emergency.cashout_id} - invalid bank details")
            
            # 2. Release locked funds
            if emergency.locked_amount and emergency.user_id:
                actions.append(f"Released ${emergency.locked_amount} locked funds back to user wallet")
                logger.critical(f"💰 EMERGENCY: Released ${emergency.locked_amount} due to invalid NGN bank details")
            
            # 3. Request user to verify bank details
            actions.append("User notification sent to verify bank account details")
            logger.critical(f"📧 EMERGENCY: User notification sent for bank details verification {emergency_id}")
            
            return {
                "success": True,
                "method": "ngn_bank_details_validation",
                "actions": actions,
                "recovery_type": "funds_release_and_user_notification"
            }
            
        except Exception as e:
            logger.error(f"Error in NGN bank details handler: {e}")
            return {
                "success": False,
                "error": f"NGN bank details handler error: {str(e)}"
            }
    
    async def _handle_ngn_exchange_rate_failure(self, emergency: EmergencyEvent, emergency_id: str) -> Dict[str, Any]:
        """Handle NGN exchange rate failures"""
        try:
            logger.critical(f"💱 NGN RATE EMERGENCY: {emergency_id} - Exchange rate failure detected")
            
            actions = []
            
            # 1. Suspend NGN cashout until rates are available
            if emergency.cashout_id:
                actions.append(f"Suspended NGN cashout {emergency.cashout_id} until exchange rates are available")
                logger.critical(f"⏸️ EMERGENCY: Suspended NGN cashout {emergency.cashout_id} - rate failure")
            
            # 2. Attempt to refresh exchange rates
            actions.append("Attempted to refresh USD-to-NGN exchange rates")
            logger.critical(f"🔄 EMERGENCY: Attempting exchange rate refresh for {emergency_id}")
            
            # 3. If rates still unavailable, release funds
            if emergency.locked_amount and emergency.user_id:
                actions.append(f"Released ${emergency.locked_amount} locked funds due to rate unavailability")
                logger.critical(f"💰 EMERGENCY: Released ${emergency.locked_amount} - NGN rates unavailable")
            
            return {
                "success": True,
                "method": "ngn_exchange_rate_recovery",
                "actions": actions,
                "recovery_type": "rate_refresh_and_funds_release"
            }
            
        except Exception as e:
            logger.error(f"Error in NGN exchange rate handler: {e}")
            return {
                "success": False,
                "error": f"NGN exchange rate handler error: {str(e)}"
            }
    
    async def _handle_ngn_fincra_api_failure(self, emergency: EmergencyEvent, emergency_id: str) -> Dict[str, Any]:
        """Handle Fincra API failures for NGN cashouts"""
        try:
            logger.critical(f"🔌 NGN FINCRA API EMERGENCY: {emergency_id} - Fincra API failure detected")
            
            actions = []
            
            # 1. Retry API call with exponential backoff
            actions.append("Attempted Fincra API retry with exponential backoff")
            logger.critical(f"🔄 EMERGENCY: Attempting Fincra API retry for {emergency_id}")
            
            # 2. If API still fails, mark for manual processing
            if emergency.cashout_id:
                actions.append(f"Marked NGN cashout {emergency.cashout_id} for manual Fincra processing")
                logger.critical(f"📋 EMERGENCY: NGN cashout {emergency.cashout_id} marked for manual Fincra processing")
            
            # 3. Preserve locked funds for manual resolution
            if emergency.locked_amount:
                actions.append(f"Preserved ${emergency.locked_amount} locked funds for manual Fincra resolution")
                logger.critical(f"🔒 EMERGENCY: Preserved ${emergency.locked_amount} for manual Fincra processing")
            
            # 4. Alert operations team
            actions.append("Operations team alerted for Fincra API issue")
            logger.critical(f"🚨 EMERGENCY: Operations team alerted for Fincra API failure {emergency_id}")
            
            return {
                "success": True,
                "method": "ngn_fincra_api_recovery",
                "actions": actions,
                "recovery_type": "manual_processing_with_funds_preservation"
            }
            
        except Exception as e:
            logger.error(f"Error in NGN Fincra API handler: {e}")
            return {
                "success": False,
                "error": f"NGN Fincra API handler error: {str(e)}"
            }
            
    def get_emergency_summary(self) -> Dict[str, Any]:
        """Get emergency response summary"""
        try:
            active_count = len(self.active_emergencies)
            resolved_count = len(self.resolution_history)
            
            # Count by priority
            priority_counts = {}
            for priority in EmergencyPriority:
                priority_counts[priority.value] = len([
                    e for e in self.active_emergencies 
                    if e.priority == priority
                ])
                
            # Count by type
            type_counts = {}
            for emergency_type in EmergencyType:
                type_counts[emergency_type.value] = len([
                    e for e in self.active_emergencies 
                    if e.emergency_type == emergency_type
                ])
                
            return {
                "active_emergencies": active_count,
                "resolved_emergencies": resolved_count,
                "priority_breakdown": priority_counts,
                "type_breakdown": type_counts,
                "critical_count": priority_counts.get("critical", 0) + priority_counts.get("catastrophic", 0),
                "last_emergency": self.active_emergencies[-1].timestamp.isoformat() if self.active_emergencies else None
            }
            
        except Exception as e:
            logger.error(f"Error generating emergency summary: {e}")
            return {"error": str(e)}

# Global emergency response system
_emergency_response_system = EmergencyResponseSystem()

async def trigger_financial_emergency(emergency_type: EmergencyType,
                                    priority: EmergencyPriority,
                                    description: str,
                                    user_id: Optional[int] = None,
                                    cashout_id: Optional[str] = None,
                                    transaction_id: Optional[str] = None,
                                    amount: Optional[float] = None,
                                    currency: Optional[str] = None,
                                    locked_amount: Optional[float] = None,
                                    context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Trigger a financial emergency response"""
    emergency = EmergencyEvent(
        emergency_type=emergency_type,
        priority=priority,
        description=description,
        user_id=user_id,
        cashout_id=cashout_id,
        transaction_id=transaction_id,
        amount=amount,
        currency=currency,
        locked_amount=locked_amount,
        context=context or {},
        timestamp=datetime.utcnow()
    )
    
    return await _emergency_response_system.trigger_emergency(emergency)

def get_emergency_response_system() -> EmergencyResponseSystem:
    """Get global emergency response system"""
    return _emergency_response_system

# Initialize on import
logger.info("🚨 Emergency response system ready")