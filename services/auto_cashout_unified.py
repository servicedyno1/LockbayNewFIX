#!/usr/bin/env python3
"""
Unified Auto-Cashout Service - New Simplified Architecture

Replaces the complex 3800+ line auto_cashout.py with a clean, simplified implementation
that uses the new PaymentProcessor architecture. This provides:

- Single PaymentProcessor entry point instead of 20+ service dependencies
- Unified error handling instead of complex error transformation chains
- 5-state system instead of multiple competing state management systems
- Clean validation instead of overlapping validation systems
- Simplified retry logic using unified error classification

Key Features:
- process_auto_cashout(): Main entry point for all auto-cashout operations
- process_escrow_completion(): Simplified escrow completion handling
- Backward compatibility with existing cashout records and flows
- Unified error handling across all payment types (crypto and fiat)
- Optimized balance checking with existing cache systems
"""

import logging
from typing import Dict, Any, Optional
from decimal import Decimal
from datetime import datetime
from sqlalchemy.orm import Session
from database import async_managed_session

from models import (
    User, Escrow, Transaction, TransactionType, SavedAddress, SavedBankAccount, 
    Wallet, CashoutType
)

# Import the new simplified payment architecture
from services.core.payment_processor import PaymentProcessor
from services.core.payment_data_structures import (
    PayoutRequest, PaymentDestination, PaymentResult
)


logger = logging.getLogger(__name__)


class UnifiedAutoCashoutService:
    """
    Simplified Auto-Cashout Service using PaymentProcessor
    
    Replaces the complex multi-service architecture with clean, direct calls
    to the unified PaymentProcessor while maintaining all existing functionality.
    """
    
    def __init__(self):
        """Initialize the unified auto-cashout service"""
        self.payment_processor = PaymentProcessor()
        logger.info("🚀 UnifiedAutoCashoutService initialized with PaymentProcessor")
    
    async def process_escrow_completion(
        self,
        escrow_id: str,
        user_id: int,
        amount: Decimal,
        currency: str = "USD",
        force_manual: bool = False
    ) -> Dict[str, Any]:
        """
        Process escrow completion with simplified auto-cashout logic
        
        Uses PaymentProcessor for all payment operations instead of complex
        multi-service chains. Maintains backward compatibility with existing
        escrow completion flows.
        
        Args:
            escrow_id: The escrow ID that was completed
            user_id: User receiving the funds
            amount: Amount to process for auto-cashout
            currency: Currency (default USD)
            force_manual: Force manual processing instead of auto-cashout
            
        Returns:
            Dict with processing results and status
        """
        try:
            logger.info(
                f"🔄 ESCROW_COMPLETION: Processing escrow {escrow_id} "
                f"for user {user_id} - {currency} {amount}"
            )
            
            # Get user and escrow details
            async with async_managed_session() as session:
                user = session.query(User).filter_by(id=user_id).first()
                if not user:
                    return {"success": False, "error": "User not found"}
                
                escrow = session.query(Escrow).filter_by(escrow_id=escrow_id).first()
                if not escrow:
                    return {"success": False, "error": "Escrow not found"}
                
                # Check if user has auto-cashout enabled and a valid destination
                auto_cashout_enabled = getattr(user, 'auto_cashout_enabled', False)
                
                if force_manual or not auto_cashout_enabled:
                    logger.info(f"⏭️ MANUAL_MODE: Auto-cashout disabled or forced manual for user {user_id}")
                    return await self._credit_wallet_and_notify(user_id, amount, currency, escrow_id, session)
                
                # Get user's preferred auto-cashout destination
                destination = await self._get_auto_cashout_destination(user_id, currency, session)
                if not destination:
                    logger.info(f"📋 NO_DESTINATION: No auto-cashout destination configured for user {user_id}")
                    return await self._credit_wallet_and_notify(user_id, amount, currency, escrow_id, session)
                
                # Process auto-cashout using PaymentProcessor
                return await self._process_auto_cashout_unified(
                    user_id=user_id,
                    amount=amount,
                    currency=currency,
                    destination=destination,
                    reference_id=escrow_id,
                    payment_type="escrow_release",
                    session=session
                )
                
        except Exception as e:
            logger.error(f"❌ ESCROW_COMPLETION_ERROR: {e}")
            return {
                "success": False,
                "error": f"Escrow completion processing failed: {str(e)}"
            }
    
    async def process_auto_cashout(
        self,
        user_id: int,
        amount: Decimal,
        currency: str,
        cashout_type: str,
        destination_info: Dict[str, Any],
        requires_otp: bool = True,
        priority: str = "normal"
    ) -> Dict[str, Any]:
        """
        Main entry point for auto-cashout processing
        
        Simplified interface that replaces multiple complex cashout methods
        with a single, clean entry point using PaymentProcessor.
        
        Args:
            user_id: User ID requesting cashout
            amount: Amount to cashout
            currency: Currency (BTC, ETH, LTC, USD, NGN, etc.)
            cashout_type: Type of cashout (CRYPTO, NGN_BANK, USD_BANK)
            destination_info: Destination details (address, bank account, etc.)
            requires_otp: Whether OTP verification is required
            priority: Processing priority (normal, high, urgent)
            
        Returns:
            Dict with processing results and transaction details
        """
        try:
            logger.info(
                f"🔄 AUTO_CASHOUT: Processing {currency} {amount} "
                f"for user {user_id} ({cashout_type})"
            )
            
            async with async_managed_session():
                # Create payment destination from cashout details
                destination = self._create_payment_destination(cashout_type, destination_info, currency)
                
                # Check balance before processing
                balance_check = await self.payment_processor.check_balance([currency])
                if not self._validate_sufficient_balance(balance_check, currency, amount):
                    return {
                        "success": False,
                        "error": f"Insufficient {currency} balance for cashout",
                        "error_category": "business"
                    }
                
                # Create payout request using new unified structure
                payout_request = PayoutRequest(
                    user_id=user_id,
                    amount=amount,
                    currency=currency,
                    destination=destination,
                    payment_type="cashout",
                    requires_otp=requires_otp,
                    priority=priority,
                    metadata={
                        "cashout_type": cashout_type,
                        "auto_generated": True,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                )
                
                # Process payout using PaymentProcessor
                result = await self.payment_processor.process_payout(payout_request)
                
                # Convert PaymentResult to legacy format for backward compatibility
                return self._convert_payment_result_to_legacy(result, cashout_type)
                
        except Exception as e:
            logger.error(f"❌ AUTO_CASHOUT_ERROR: {e}")
            return {
                "success": False,
                "error": f"Auto-cashout processing failed: {str(e)}",
                "error_category": "technical"
            }
    
    async def _process_auto_cashout_unified(
        self,
        user_id: int,
        amount: Decimal,
        currency: str,
        destination: PaymentDestination,
        reference_id: str,
        payment_type: str,
        session: Session
    ) -> Dict[str, Any]:
        """
        Process auto-cashout using unified PaymentProcessor
        
        Internal method that handles the actual cashout processing using
        the new simplified architecture.
        """
        try:
            # Create payout request
            payout_request = PayoutRequest(
                user_id=user_id,
                amount=amount,
                currency=currency,
                destination=destination,
                payment_type=payment_type,
                reference_id=reference_id,
                requires_otp=False,  # Auto-cashouts don't require OTP
                metadata={
                    "auto_cashout": True,
                    "reference_id": reference_id,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
            
            # Process using PaymentProcessor
            result = await self.payment_processor.process_payout(payout_request)
            
            if result.success:
                logger.info(
                    f"✅ AUTO_CASHOUT_SUCCESS: {currency} {amount} processed "
                    f"for user {user_id} via {result.provider.value}"
                )
                return {
                    "success": True,
                    "transaction_id": result.transaction_id,
                    "provider": result.provider.value,
                    "status": result.status.value,
                    "estimated_completion": result.estimated_completion
                }
            else:
                logger.warning(
                    f"⚠️ AUTO_CASHOUT_FAILED: {currency} {amount} failed "
                    f"for user {user_id} - {result.error_message}"
                )
                # For auto-cashout failures, credit wallet instead
                return await self._credit_wallet_and_notify(
                    user_id, amount, currency, reference_id, session
                )
                
        except Exception as e:
            logger.error(f"❌ AUTO_CASHOUT_UNIFIED_ERROR: {e}")
            # Fallback to wallet credit on any errors
            return await self._credit_wallet_and_notify(
                user_id, amount, currency, reference_id, session
            )
    
    async def _get_auto_cashout_destination(
        self, 
        user_id: int, 
        currency: str, 
        session: Session
    ) -> Optional[PaymentDestination]:
        """
        Get user's preferred auto-cashout destination for currency
        
        Simplified destination lookup that supports both crypto addresses
        and bank accounts using the new unified destination structure.
        """
        try:
            # For crypto currencies, look for saved addresses
            if currency in ['BTC', 'ETH', 'LTC', 'USDT', 'USDC', 'TRX', 'DOGE']:
                saved_address = session.query(SavedAddress).filter_by(
                    user_id=user_id,
                    currency=currency,
                    is_auto_cashout_default=True
                ).first()
                
                if saved_address:
                    return PaymentDestination(
                        type="crypto_address",
                        address=saved_address.address,
                        currency=currency,
                        network=saved_address.network
                    )
            
            # For fiat currencies, look for saved bank accounts
            elif currency in ['NGN', 'USD']:
                saved_bank = session.query(SavedBankAccount).filter_by(
                    user_id=user_id,
                    currency=currency,
                    is_auto_cashout_default=True
                ).first()
                
                if saved_bank:
                    return PaymentDestination(
                        type="bank_account",
                        bank_code=saved_bank.bank_code,
                        account_number=saved_bank.account_number,
                        account_name=saved_bank.account_name,
                        currency=currency
                    )
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Error getting auto-cashout destination for user {user_id}: {e}")
            return None
    
    async def _credit_wallet_and_notify(
        self,
        user_id: int,
        amount: Decimal,
        currency: str,
        reference_id: str,
        session: Session
    ) -> Dict[str, Any]:
        """
        Credit user wallet when auto-cashout is not possible
        
        Simplified wallet crediting that maintains existing functionality
        but uses cleaner logic and error handling.
        """
        try:
            # Get or create user wallet
            wallet = session.query(Wallet).filter_by(
                user_id=user_id,
                currency=currency
            ).first()
            
            if not wallet:
                wallet = Wallet(
                    user_id=user_id,
                    currency=currency,
                    available_balance=Decimal('0')
                )
                session.add(wallet)
            
            # Credit the wallet
            wallet.available_balance += amount
            
            # Create transaction record
            transaction = Transaction(
                user_id=user_id,
                transaction_type=TransactionType.ESCROW_RELEASE.value,
                amount=amount,
                currency=currency,
                description=f"Escrow completion credit - {reference_id}",
                created_at=datetime.utcnow()
            )
            session.add(transaction)
            
            session.commit()
            
            logger.info(
                f"💰 WALLET_CREDITED: {currency} {amount} credited to user {user_id} wallet"
            )
            
            return {
                "success": True,
                "wallet_credited": True,
                "amount": float(amount),
                "currency": currency,
                "new_balance": float(wallet.available_balance)
            }
            
        except Exception as e:
            logger.error(f"❌ Error crediting wallet for user {user_id}: {e}")
            session.rollback()
            return {
                "success": False,
                "error": f"Failed to credit wallet: {str(e)}"
            }
    
    def _create_payment_destination(
        self, 
        cashout_type: str, 
        destination_info: Dict[str, Any], 
        currency: str
    ) -> PaymentDestination:
        """
        Create PaymentDestination from legacy cashout info
        
        Converts existing cashout destination formats to the new
        unified PaymentDestination structure.
        """
        if cashout_type == CashoutType.CRYPTO.value:
            return PaymentDestination(
                type="crypto_address",
                address=destination_info.get("address"),
                currency=currency,
                network=destination_info.get("network"),
                memo=destination_info.get("memo")
            )
        
        elif cashout_type in ["NGN_BANK", "USD_BANK"]:
            return PaymentDestination(
                type="bank_account",
                bank_code=destination_info.get("bank_code"),
                account_number=destination_info.get("account_number"),
                account_name=destination_info.get("account_name"),
                currency=currency
            )
        
        else:
            raise ValueError(f"Unsupported cashout type: {cashout_type}")
    
    def _validate_sufficient_balance(
        self, 
        balance_check: Any, 
        currency: str, 
        amount: Decimal
    ) -> bool:
        """
        Validate sufficient balance using PaymentProcessor results
        
        Simplified balance validation that uses the new unified
        balance checking system.
        """
        try:
            if not balance_check or not balance_check.success:
                return False
            
            for snapshot in balance_check.balances:
                if snapshot.currency == currency:
                    return snapshot.available_balance >= amount
            
            return False
            
        except Exception as e:
            logger.error(f"❌ Error validating balance: {e}")
            return False
    
    def _convert_payment_result_to_legacy(
        self, 
        result: PaymentResult, 
        cashout_type: str
    ) -> Dict[str, Any]:
        """
        Convert PaymentResult to legacy cashout result format
        
        Maintains backward compatibility with existing code that
        expects the legacy cashout result structure.
        """
        return {
            "success": result.success,
            "status": result.status.value,
            "transaction_id": result.transaction_id,
            "provider_transaction_id": result.provider_transaction_id,
            "error": result.error.value if result.error else None,
            "error_message": result.error_message,
            "error_code": result.error_code,
            "cashout_type": cashout_type,
            "provider": result.provider.value if result.provider else None,
            "estimated_completion": result.estimated_completion,
            "payment_details": result.payment_details,
            "requires_otp": result.requires_otp,
            "requires_user_action": result.requires_user_action,
            "next_action": result.next_action
        }


# Create global instance for backward compatibility
unified_auto_cashout_service = UnifiedAutoCashoutService()


# Backward compatibility functions that delegate to the unified service
async def process_escrow_completion_unified(
    escrow_id: str,
    user_id: int,
    amount: Decimal,
    currency: str = "USD"
) -> Dict[str, Any]:
    """Backward compatibility wrapper for escrow completion"""
    return await unified_auto_cashout_service.process_escrow_completion(
        escrow_id, user_id, amount, currency
    )


async def create_auto_cashout_unified(
    user_id: int,
    amount: Decimal,
    currency: str,
    cashout_type: str,
    destination_info: Dict[str, Any]
) -> Dict[str, Any]:
    """Backward compatibility wrapper for auto-cashout creation"""
    return await unified_auto_cashout_service.process_auto_cashout(
        user_id, amount, currency, cashout_type, destination_info
    )