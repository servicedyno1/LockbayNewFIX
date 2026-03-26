"""
Comprehensive Receipt Generation Service for LockBay
Phase 3B implementation of branded receipt system for all transaction types
"""

import logging
from decimal import Decimal
from datetime import datetime
from typing import Dict, List, Optional, Any
from sqlalchemy import or_

from database import SessionLocal
from models import (
    User, Escrow, Cashout, UnifiedTransaction, TransactionType
)
from utils.branding_utils import BrandingUtils
from utils.branding import SecurityIcons, UserRetentionElements

logger = logging.getLogger(__name__)


class ReceiptGenerationService:
    """Comprehensive service for generating branded receipts for all transaction types"""
    
    # Transaction type mappings for receipts
    RECEIPT_TYPE_MAPPINGS = {
        "escrow_completed": {
            "emoji": SecurityIcons.VERIFIED,
            "display_name": "Escrow Release",
            "description": "Escrow funds released successfully"
        },
        "cashout_completed": {
            "emoji": "💸", 
            "display_name": "Cashout",
            "description": "Funds withdrawn to external account"
        },
        "exchange_completed": {
            "emoji": "🔄",
            "display_name": "Exchange",
            "description": "Currency exchange completed"
        },
        "deposit_completed": {
            "emoji": "💰",
            "display_name": "Deposit",
            "description": "Funds deposited to wallet"
        },
        "transfer_completed": {
            "emoji": "📤",
            "display_name": "Transfer",
            "description": "Internal wallet transfer"
        },
        "fee_deduction": {
            "emoji": "💸",
            "display_name": "Fee",
            "description": "Platform fee deducted"
        }
    }
    
    @classmethod
    def generate_escrow_completion_receipt(cls, escrow_id: str, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Generate comprehensive receipt for escrow completion
        
        Args:
            escrow_id: Escrow ID that was completed
            user_id: User ID receiving the receipt
            
        Returns:
            Receipt data or None if error
        """
        try:
            session = SessionLocal()
            try:
                # Get escrow details
                escrow = session.query(Escrow).filter(Escrow.escrow_id == escrow_id).first()
                if not escrow:
                    logger.error(f"Escrow {escrow_id} not found for receipt generation")
                    return None
                
                # Get user details
                user = session.query(User).filter(User.id == user_id).first()
                if not user:
                    logger.error(f"User {user_id} not found for receipt generation")
                    return None
                
                # Determine user role and amount
                if user_id == escrow.buyer_id:
                    user_role = "buyer"
                elif user_id == escrow.seller_id:
                    user_role = "seller"
                else:
                    logger.error(f"User {user_id} not involved in escrow {escrow_id}")
                    return None
                
                # Calculate amounts
                base_amount = escrow.amount
                fee_amount = escrow.buyer_fee_amount if user_role == "buyer" else escrow.seller_fee_amount or Decimal('0')
                net_amount = base_amount - fee_amount if user_role == "buyer" else base_amount
                
                # Prepare additional details
                additional_details = {
                    "fee_amount": fee_amount,
                    "fee_currency": escrow.currency,
                    "net_amount": net_amount,
                    "recipient": (escrow.seller_contact_display or escrow.seller.username) if user_role == "buyer" else escrow.buyer.username,
                    "escrow_duration": cls._calculate_escrow_duration(escrow),
                    "completion_time": escrow.completed_at.isoformat() if escrow.completed_at else datetime.utcnow().isoformat(),
                    "user_role": user_role,
                    "counterparty": (escrow.seller_contact_display or escrow.seller.first_name or escrow.seller.username or "Seller") if user_role == "buyer" else escrow.buyer.first_name,
                    "description": escrow.description[:100] if escrow.description else "Escrow transaction"
                }
                
                # Generate comprehensive receipt
                receipt_data = BrandingUtils.make_shareable_receipt(
                    tx_id=escrow_id,
                    amount=base_amount,
                    asset=escrow.currency,
                    tx_type="escrow_completed",
                    additional_details=additional_details,
                    include_qr=True
                )
                
                # Add escrow-specific metadata
                receipt_data["escrow_metadata"] = {
                    "escrow_id": escrow_id,
                    "buyer_id": escrow.buyer_id,
                    "seller_id": escrow.seller_id,
                    "user_role": user_role,
                    "amount": str(base_amount),
                    "currency": escrow.currency,
                    "fee_amount": str(fee_amount),
                    "net_amount": str(net_amount),
                    "status": escrow.status,
                    "completion_timestamp": escrow.completed_at.isoformat() if escrow.completed_at else None
                }
                
                # Generate celebration message if first trade
                if user.completed_trades == 1:
                    # Use the MILESTONE_MESSAGES directly (no create_celebration_message method exists)
                    celebration_message = UserRetentionElements.MILESTONE_MESSAGES.get("first_completion", "")
                    if celebration_message:
                        receipt_data["celebration_message"] = celebration_message
                
                logger.info(f"Generated escrow completion receipt for user {user_id}, escrow {escrow_id}")
                return receipt_data
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error generating escrow completion receipt for {escrow_id}: {e}")
            return None
    
    @classmethod
    def generate_cashout_completion_receipt(cls, cashout_id: str) -> Optional[Dict[str, Any]]:
        """
        Generate comprehensive receipt for cashout completion
        
        Args:
            cashout_id: Cashout ID that was completed
            
        Returns:
            Receipt data or None if error
        """
        try:
            session = SessionLocal()
            try:
                # Get cashout details
                cashout = session.query(Cashout).filter(Cashout.cashout_id == cashout_id).first()
                if not cashout:
                    logger.error(f"Cashout {cashout_id} not found for receipt generation")
                    return None
                
                # Get user details
                user = session.query(User).filter(User.id == cashout.user_id).first()
                if not user:
                    logger.error(f"User {cashout.user_id} not found for receipt generation")
                    return None
                
                # Calculate amounts
                base_amount = cashout.amount
                fee_amount = cashout.fee_amount or Decimal('0')
                net_amount = base_amount - fee_amount
                
                # Prepare additional details
                additional_details = {
                    "fee_amount": fee_amount,
                    "fee_currency": cashout.currency,
                    "net_amount": net_amount,
                    "recipient": cashout.destination or "External Account",
                    "network": cashout.currency,
                    "processing_time": cls._calculate_cashout_duration(cashout),
                    "destination_type": "crypto" if cashout.destination else "bank"
                }
                
                # Generate comprehensive receipt
                receipt_data = BrandingUtils.make_shareable_receipt(
                    tx_id=cashout_id,
                    amount=base_amount,
                    asset=cashout.currency,
                    tx_type="cashout_completed",
                    additional_details=additional_details,
                    include_qr=True
                )
                
                # Add cashout-specific metadata
                receipt_data["cashout_metadata"] = {
                    "cashout_id": cashout_id,
                    "user_id": cashout.user_id,
                    "amount": str(base_amount),
                    "currency": cashout.currency,
                    "fee_amount": str(fee_amount),
                    "net_amount": str(net_amount),
                    "destination": cashout.destination,
                    "status": cashout.status,
                    "completion_timestamp": cashout.completed_at.isoformat() if cashout.completed_at else None
                }
                
                logger.info(f"Generated cashout completion receipt for user {cashout.user_id}, cashout {cashout_id}")
                return receipt_data
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error generating cashout completion receipt for {cashout_id}: {e}")
            return None
    
    @classmethod
    def generate_unified_transaction_receipt(cls, transaction_id: str) -> Optional[Dict[str, Any]]:
        """
        Generate receipt for unified transaction system
        
        Args:
            transaction_id: Unified transaction ID
            
        Returns:
            Receipt data or None if error
        """
        try:
            session = SessionLocal()
            try:
                # Get transaction details
                transaction = session.query(UnifiedTransaction).filter(
                    UnifiedTransaction.transaction_id == transaction_id
                ).first()
                
                if not transaction:
                    logger.error(f"Transaction {transaction_id} not found for receipt generation")
                    return None
                
                # Get user details
                user = session.query(User).filter(User.id == transaction.user_id).first()
                if not user:
                    logger.error(f"User {transaction.user_id} not found for receipt generation")
                    return None
                
                # Prepare transaction type display
                tx_type_display = cls._get_transaction_type_display(transaction.transaction_type)
                
                # Calculate amounts
                base_amount = transaction.amount
                fee_amount = getattr(transaction, 'fee_amount', None) or Decimal('0')
                net_amount = base_amount
                
                # Adjust net amount based on transaction type
                if transaction.transaction_type in [TransactionType.CASHOUT.value, TransactionType.EXCHANGE_SELL.value]:
                    net_amount = base_amount - fee_amount
                elif transaction.transaction_type in [TransactionType.DEPOSIT.value, TransactionType.EXCHANGE_BUY.value]:
                    net_amount = base_amount + fee_amount
                
                # Prepare additional details
                additional_details = {
                    "fee_amount": fee_amount,
                    "fee_currency": transaction.currency,
                    "net_amount": net_amount,
                    "transaction_type": transaction.transaction_type,
                    "status": transaction.status,
                    "confirmation_blocks": getattr(transaction, 'confirmation_blocks', None),
                    "network": transaction.currency if transaction.currency in ['BTC', 'ETH', 'LTC'] else None
                }
                
                # Generate comprehensive receipt
                receipt_data = BrandingUtils.make_shareable_receipt(
                    tx_id=transaction_id,
                    amount=base_amount,
                    asset=transaction.currency,
                    tx_type=tx_type_display,
                    additional_details=additional_details,
                    include_qr=True
                )
                
                # Add transaction-specific metadata
                receipt_data["transaction_metadata"] = {
                    "transaction_id": transaction_id,
                    "user_id": transaction.user_id,
                    "amount": str(base_amount),
                    "currency": transaction.currency,
                    "transaction_type": transaction.transaction_type,
                    "status": transaction.status,
                    "created_at": transaction.created_at.isoformat(),
                    "completed_at": transaction.updated_at.isoformat()
                }
                
                logger.info(f"Generated unified transaction receipt for user {transaction.user_id}, tx {transaction_id}")
                return receipt_data
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error generating unified transaction receipt for {transaction_id}: {e}")
            return None
    
    @classmethod
    def generate_bulk_receipts(cls, user_id: int, date_range: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Generate receipts for multiple transactions for a user
        
        Args:
            user_id: User ID to generate receipts for
            date_range: Optional date range filter
            
        Returns:
            List of receipt data
        """
        try:
            session = SessionLocal()
            try:
                receipts = []
                
                # Get recent completed escrows
                escrow_query = session.query(Escrow).filter(
                    or_(Escrow.buyer_id == user_id, Escrow.seller_id == user_id),
                    Escrow.status == 'completed'
                )
                
                if date_range:
                    start_date = date_range.get('start_date')
                    end_date = date_range.get('end_date')
                    if start_date:
                        escrow_query = escrow_query.filter(Escrow.completed_at >= start_date)
                    if end_date:
                        escrow_query = escrow_query.filter(Escrow.completed_at <= end_date)
                
                escrows = escrow_query.order_by(Escrow.completed_at.desc()).limit(10).all()
                
                # Generate receipts for escrows
                for escrow in escrows:
                    receipt = cls.generate_escrow_completion_receipt(escrow.escrow_id, user_id)
                    if receipt:
                        receipts.append(receipt)
                
                # Get recent completed cashouts
                cashout_query = session.query(Cashout).filter(
                    Cashout.user_id == user_id,
                    Cashout.status == 'completed'
                )
                
                if date_range:
                    if start_date:
                        cashout_query = cashout_query.filter(Cashout.completed_at >= start_date)
                    if end_date:
                        cashout_query = cashout_query.filter(Cashout.completed_at <= end_date)
                
                cashouts = cashout_query.order_by(Cashout.completed_at.desc()).limit(5).all()
                
                # Generate receipts for cashouts
                for cashout in cashouts:
                    receipt = cls.generate_cashout_completion_receipt(cashout.cashout_id)
                    if receipt:
                        receipts.append(receipt)
                
                # Sort by timestamp
                receipts.sort(key=lambda x: x.get('transaction_summary', {}).get('timestamp', ''), reverse=True)
                
                logger.info(f"Generated {len(receipts)} bulk receipts for user {user_id}")
                return receipts
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error generating bulk receipts for user {user_id}: {e}")
            return []
    
    @classmethod
    def _calculate_escrow_duration(cls, escrow: Escrow) -> Optional[str]:
        """Calculate escrow duration for receipt display"""
        try:
            if escrow.completed_at and escrow.created_at:
                duration = escrow.completed_at - escrow.created_at
                days = duration.days
                hours = duration.seconds // 3600
                
                if days > 0:
                    return f"{days} day(s)"
                elif hours > 0:
                    return f"{hours} hour(s)"
                else:
                    return "< 1 hour"
            return None
        except Exception as e:
            logger.error(f"Error calculating escrow duration: {e}")
            return None
    
    @classmethod
    def _calculate_cashout_duration(cls, cashout: Cashout) -> Optional[str]:
        """Calculate cashout processing time for receipt display"""
        try:
            if cashout.completed_at and cashout.created_at:
                duration = cashout.completed_at - cashout.created_at
                hours = duration.total_seconds() / 3600
                
                if hours < 1:
                    return "< 1 hour"
                elif hours < 24:
                    return f"{int(hours)} hour(s)"
                else:
                    return f"{int(hours/24)} day(s)"
            return None
        except Exception as e:
            logger.error(f"Error calculating cashout duration: {e}")
            return None
    
    @classmethod
    def _get_transaction_type_display(cls, transaction_type: str) -> str:
        """Get display name for transaction type"""
        type_mapping = {
            TransactionType.DEPOSIT.value: "deposit_completed",
            TransactionType.WITHDRAWAL.value: "cashout_completed", 
            TransactionType.ESCROW_DEPOSIT.value: "escrow_completed",
            TransactionType.ESCROW_RELEASE.value: "escrow_completed",
            TransactionType.EXCHANGE_BUY.value: "exchange_completed",
            TransactionType.EXCHANGE_SELL.value: "exchange_completed",
            TransactionType.FEE.value: "fee_deduction",
            TransactionType.TRANSFER.value: "transfer_completed"
        }
        
        return type_mapping.get(transaction_type, transaction_type)


# Logging setup
logger.info("✅ ReceiptGenerationService loaded successfully")