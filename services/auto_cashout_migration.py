#!/usr/bin/env python3
"""
Auto-Cashout Migration Bridge

Provides backward compatibility for the auto_cashout.py migration by gradually
replacing complex service calls with unified PaymentProcessor calls while
maintaining all existing functionality.

This bridge allows for safe, incremental migration without breaking existing flows.
"""

import logging
from typing import Dict, Any, Optional
from decimal import Decimal
from datetime import datetime
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

# Import existing models and utilities
from models import (
    Escrow, Cashout, CashoutType
)

# Import the new unified services
from services.auto_cashout_unified import unified_auto_cashout_service
from services.migration_adapters import (
    payment_adapter
)

# Import original auto_cashout for fallback
from services.auto_cashout import AutoCashoutService as LegacyAutoCashoutService

logger = logging.getLogger(__name__)


class AutoCashoutMigrationBridge:
    """
    Migration bridge that provides the original AutoCashoutService interface
    but uses the new unified PaymentProcessor architecture under the hood.
    
    This allows existing code to work unchanged while gradually migrating
    to the simplified architecture.
    """
    
    def __init__(self):
        """Initialize the migration bridge"""
        self.unified_service = unified_auto_cashout_service
        self.legacy_service = LegacyAutoCashoutService
        self.use_unified = True  # Flag to control migration progress
        
        logger.info("🔄 AutoCashoutMigrationBridge initialized")
    
    async def process_escrow_completion(
        self, 
        escrow: Escrow, 
        session: AsyncSession
    ) -> Dict[str, Any]:
        """
        Process escrow completion with migration bridge
        
        Uses unified service when possible, falls back to legacy service
        for complex edge cases during migration period.
        """
        try:
            if self.use_unified:
                # Try unified service first
                logger.info(
                    f"🔄 MIGRATION_BRIDGE: Processing escrow {escrow.escrow_id} "
                    f"via unified service"
                )
                
                result = await self.unified_service.process_escrow_completion(
                    escrow_id=escrow.escrow_id,
                    user_id=escrow.seller_id,
                    amount=Decimal(str(escrow.amount)),
                    currency="USD"  # Default currency for escrows
                )
                
                # If unified service succeeds, return result
                if result.get("success"):
                    logger.info(f"✅ Unified escrow completion successful for {escrow.escrow_id}")
                    return result
                
                # If unified service fails, log and fall back
                logger.warning(
                    f"⚠️ Unified service failed for escrow {escrow.escrow_id}, "
                    f"falling back to legacy: {result.get('error')}"
                )
            
            # Fallback to legacy service
            logger.info(f"🔄 Using legacy service for escrow {escrow.escrow_id}")
            return await self.legacy_service.process_escrow_completion(escrow, session)
            
        except Exception as e:
            logger.error(f"❌ Migration bridge error for escrow {escrow.escrow_id}: {e}")
            # Final fallback to legacy service
            try:
                return await self.legacy_service.process_escrow_completion(escrow, session)
            except Exception as fallback_error:
                logger.error(f"❌ Legacy fallback also failed: {fallback_error}")
                return {
                    "success": False,
                    "error": f"Both unified and legacy services failed: {str(e)}"
                }
    
    async def create_cashout_request(
        self,
        user_id: int,
        amount: float,
        currency: str,
        cashout_type: str,
        destination_info: Dict[str, Any],
        session: Session,
        requires_otp: bool = True,
        defer_processing: bool = False
    ) -> Dict[str, Any]:
        """
        Create cashout request with migration bridge
        
        Maintains the original interface while using unified services
        when possible.
        """
        try:
            if self.use_unified and not defer_processing:
                # Try unified service for immediate processing
                logger.info(
                    f"🔄 MIGRATION_BRIDGE: Creating {cashout_type} cashout "
                    f"for user {user_id} via unified service"
                )
                
                result = await self.unified_service.process_auto_cashout(
                    user_id=user_id,
                    amount=Decimal(str(amount)),
                    currency=currency,
                    cashout_type=cashout_type,
                    destination_info=destination_info,
                    requires_otp=requires_otp,
                    priority="normal"
                )
                
                if result.get("success"):
                    logger.info(f"✅ Unified cashout creation successful for user {user_id}")
                    return result
                
                logger.warning(
                    f"⚠️ Unified cashout failed for user {user_id}, "
                    f"falling back to legacy: {result.get('error')}"
                )
            
            # Use legacy service for deferred processing or fallback
            logger.info(f"🔄 Using legacy service for user {user_id} cashout")
            return await self.legacy_service.create_cashout_request(
                user_id=user_id,
                amount=amount,
                currency=currency,
                cashout_type=cashout_type,
                destination=str(destination_info),  # Legacy expects string
                session=session,
                defer_processing=defer_processing
            )
            
        except Exception as e:
            logger.error(f"❌ Migration bridge error for user {user_id} cashout: {e}")
            # Fallback to legacy service
            try:
                return await self.legacy_service.create_cashout_request(
                    user_id=user_id,
                    amount=amount,
                    currency=currency,
                    cashout_type=cashout_type,
                    destination=str(destination_info),
                    session=session,
                    defer_processing=defer_processing
                )
            except Exception as fallback_error:
                logger.error(f"❌ Legacy cashout fallback failed: {fallback_error}")
                return {
                    "success": False,
                    "error": f"Both unified and legacy cashout creation failed: {str(e)}"
                }
    
    async def process_automatic_cashout(
        self,
        cashout: Cashout,
        session: AsyncSession
    ) -> Dict[str, Any]:
        """
        Process automatic cashout with migration bridge
        
        Routes to appropriate service based on cashout type and complexity.
        """
        try:
            if self.use_unified:
                # Convert legacy cashout to unified format
                cashout_type = cashout.cashout_type or CashoutType.CRYPTO.value
                currency = cashout.currency or "USD"
                
                # Create destination info from cashout record
                destination_info = self._extract_destination_info(cashout)
                
                if destination_info:
                    logger.info(
                        f"🔄 MIGRATION_BRIDGE: Processing automatic cashout {cashout.cashout_id} "
                        f"via unified service"
                    )
                    
                    result = await self.unified_service.process_auto_cashout(
                        user_id=cashout.user_id,
                        amount=Decimal(str(cashout.amount)),
                        currency=currency,
                        cashout_type=cashout_type,
                        destination_info=destination_info,
                        requires_otp=False,  # Automatic cashouts don't require OTP
                        priority="normal"
                    )
                    
                    if result.get("success"):
                        logger.info(f"✅ Unified automatic cashout successful for {cashout.cashout_id}")
                        return result
                    
                    logger.warning(
                        f"⚠️ Unified automatic cashout failed for {cashout.cashout_id}, "
                        f"falling back to legacy: {result.get('error')}"
                    )
            
            # Fallback to legacy service
            logger.info(f"🔄 Using legacy service for automatic cashout {cashout.cashout_id}")
            return await self.legacy_service._process_automatic_cashout(cashout, session)
            
        except Exception as e:
            logger.error(f"❌ Migration bridge error for automatic cashout {cashout.cashout_id}: {e}")
            # Final fallback
            try:
                return await self.legacy_service._process_automatic_cashout(cashout, session)
            except Exception as fallback_error:
                logger.error(f"❌ Legacy automatic cashout fallback failed: {fallback_error}")
                return {
                    "success": False,
                    "error": f"Both unified and legacy automatic cashout failed: {str(e)}"
                }
    
    def _extract_destination_info(self, cashout: Cashout) -> Optional[Dict[str, Any]]:
        """
        Extract destination information from legacy cashout record
        
        Converts legacy cashout destination formats to unified format.
        """
        try:
            cashout_type = cashout.cashout_type or CashoutType.CRYPTO.value
            
            if cashout_type == CashoutType.CRYPTO.value:
                if hasattr(cashout, 'crypto_address_id') and cashout.crypto_address_id:
                    return {"saved_address_id": cashout.crypto_address_id}
                elif cashout.destination:
                    # Try to parse destination as address
                    return {"address": cashout.destination}
            
            elif cashout_type in ["NGN_BANK", "USD_BANK"]:
                if hasattr(cashout, 'bank_account_id') and cashout.bank_account_id:
                    return {"saved_bank_id": cashout.bank_account_id}
                elif cashout.destination:
                    # Try to parse destination as bank info
                    # Legacy format might be "bank_name:account_number" or similar
                    return {"destination_string": cashout.destination}
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error extracting destination info from cashout {cashout.cashout_id}: {e}")
            return None
    
    def set_unified_mode(self, enabled: bool):
        """
        Control whether to use unified service or legacy service
        
        This allows for gradual rollout and easy rollback if needed.
        """
        self.use_unified = enabled
        logger.info(f"🔄 Migration bridge unified mode: {'ENABLED' if enabled else 'DISABLED'}")


# Global migration bridge instance
auto_cashout_bridge = AutoCashoutMigrationBridge()


# Backward compatibility: Export bridge as AutoCashoutService
# This allows existing imports to work without changes
class AutoCashoutService:
    """
    Backward compatibility wrapper that maintains the original interface
    """
    
    @classmethod
    async def process_escrow_completion(cls, escrow: Escrow, session: AsyncSession) -> Dict[str, Any]:
        """Maintain original static method interface"""
        return await auto_cashout_bridge.process_escrow_completion(escrow, session)
    
    @classmethod
    async def create_cashout_request(
        cls,
        user_id: int,
        amount: float,
        currency: str,
        cashout_type: str,
        destination: str,
        session: Session,
        defer_processing: bool = False
    ) -> Dict[str, Any]:
        """Maintain original static method interface"""
        # Convert string destination to dict for unified service
        destination_info = {"destination_string": destination}
        
        return await auto_cashout_bridge.create_cashout_request(
            user_id=user_id,
            amount=amount,
            currency=currency,
            cashout_type=cashout_type,
            destination_info=destination_info,
            session=session,
            defer_processing=defer_processing
        )
    
    @classmethod
    async def _process_automatic_cashout(cls, cashout: Cashout, session: AsyncSession) -> Dict[str, Any]:
        """Maintain original static method interface"""
        return await auto_cashout_bridge.process_automatic_cashout(cashout, session)


# Export convenience functions
async def enable_unified_migration():
    """Enable unified PaymentProcessor mode for all cashout operations"""
    auto_cashout_bridge.set_unified_mode(True)
    logger.info("🚀 Auto-cashout migration: UNIFIED MODE ENABLED")


async def disable_unified_migration():
    """Disable unified mode and fall back to legacy services"""
    auto_cashout_bridge.set_unified_mode(False)
    logger.info("⏪ Auto-cashout migration: LEGACY MODE ENABLED")


async def get_migration_status() -> Dict[str, Any]:
    """Get current migration status and health"""
    return {
        "unified_mode_enabled": auto_cashout_bridge.use_unified,
        "payment_processor_available": payment_adapter.payment_processor is not None,
        "migration_bridge_active": True,
        "timestamp": datetime.utcnow().isoformat()
    }