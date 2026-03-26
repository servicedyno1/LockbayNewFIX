"""
Admin Failure Service
Core logic for managing failed transactions requiring admin intervention
"""

import logging
import secrets
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import desc, and_, or_

from models import (
    Cashout, CashoutStatus, User, AdminActionToken, AdminActionType,
    UnifiedTransaction, WalletHolds, WalletHoldStatus
)
from utils.helpers import format_amount
from services.crypto import CryptoServiceAtomic

logger = logging.getLogger(__name__)


class AdminFailureService:
    """Service for managing failed transactions requiring admin intervention"""

    def __init__(self):
        self.token_expiry_hours = 24
        self.max_tokens_per_cashout = 3  # Prevent token flooding
    
    def get_pending_failures(self, session: Session, limit: int = 50, 
                           offset: int = 0) -> Dict[str, Any]:
        """
        Get all cashouts requiring admin intervention with comprehensive details
        
        Args:
            session: Database session
            limit: Maximum number of records to return
            offset: Number of records to skip for pagination
            
        Returns:
            Dict containing failures list, pagination info, and summary statistics
        """
        try:
            # Enhanced query for admin intervention cases including crypto funding scenarios
            base_query = session.query(Cashout).filter(
                or_(
                    # Traditional admin pending failures
                    Cashout.status == CashoutStatus.ADMIN_PENDING,
                    
                    # Crypto funding scenarios: SUCCESS status but requires admin funding
                    and_(
                        Cashout.status == CashoutStatus.SUCCESS,
                        Cashout.admin_notes.ilike('%funding%'),  # Contains funding requirement
                        Cashout.currency.in_(['BTC', 'ETH', 'LTC', 'USDT', 'DOGE', 'XMR'])  # Crypto currencies
                    )
                )
            ).order_by(desc(Cashout.created_at))
            
            # Get total count for pagination
            total_count = base_query.count()
            
            # Get paginated results with user relationship
            cashouts = (
                base_query
                .offset(offset)
                .limit(limit)
                .all()
            )
            
            # Build comprehensive failure details
            failures = []
            for cashout in cashouts:
                try:
                    # Get user details
                    user = session.query(User).filter(User.id == cashout.user_id).first()
                    
                    # Get latest unified transaction if exists (query by reference_id which stores cashout_id)
                    latest_unified = (
                        session.query(UnifiedTransaction)
                        .filter(UnifiedTransaction.reference_id == cashout.cashout_id)
                        .order_by(desc(UnifiedTransaction.created_at))
                        .first()
                    )
                    
                    # Get wallet holds info for fund status
                    wallet_hold = (
                        session.query(WalletHolds)
                        .filter(WalletHolds.cashout_id == cashout.id)
                        .order_by(desc(WalletHolds.created_at))
                        .first()
                    )
                    
                    # Get existing admin tokens for this cashout
                    existing_tokens = (
                        session.query(AdminActionToken)
                        .filter(AdminActionToken.cashout_id == cashout.id)
                        .filter(AdminActionToken.used_at.is_(None))  # Unused only
                        .count()
                    )
                    
                    # Determine if this is a crypto funding scenario
                    is_crypto_funding = (
                        cashout.status == CashoutStatus.SUCCESS and 
                        cashout.admin_notes and 
                        'funding' in cashout.admin_notes.lower() and
                        cashout.currency in ['BTC', 'ETH', 'LTC', 'USDT', 'DOGE', 'XMR']
                    )
                    
                    # Enhanced failure categorization
                    if is_crypto_funding:
                        failure_type = "crypto_funding_required"
                        display_status = "Success (Funding Required)"
                        priority_level = "medium"  # Funding issues are important but not critical
                    else:
                        failure_type = "traditional_failure"
                        display_status = "Failed"
                        priority_level = "high"  # Traditional failures are high priority
                    
                    # Build comprehensive failure info
                    failure_info = {
                        'id': cashout.id,
                        'user_id': cashout.user_id,
                        'user_name': user.first_name if user else 'Unknown User',
                        'user_username': user.username if user and user.username else None,
                        'user_telegram_id': user.telegram_id if user else None,
                        'amount': float(cashout.amount_requested),
                        'currency': cashout.currency,
                        'formatted_amount': format_amount(cashout.amount_requested, cashout.currency),
                        'destination_type': cashout.destination_type,
                        'destination_id': cashout.destination_id,
                        'created_at': cashout.created_at.isoformat(),
                        'updated_at': cashout.updated_at.isoformat(),
                        'error_message': cashout.error_message or 'No error message available',
                        'failure_reason': cashout.failure_reason or 'Unknown failure',
                        'processing_mode': cashout.processing_mode.value if cashout.processing_mode else 'manual',
                        'attempt_count': cashout.retry_count or 0,
                        'last_attempt_at': cashout.last_attempt_at.isoformat() if cashout.last_attempt_at else None,
                        'admin_notes': cashout.admin_notes or '',
                        
                        # Enhanced categorization for crypto funding scenarios
                        'failure_type': failure_type,
                        'display_status': display_status,
                        'is_crypto_funding': is_crypto_funding,
                        'priority_level': priority_level,
                        'user_sees_success': is_crypto_funding,  # Flag for admin awareness
                        
                        # Unified transaction info
                        'unified_transaction_id': latest_unified.id if latest_unified else None,
                        'unified_status': latest_unified.status.value if latest_unified else None,
                        'unified_error': latest_unified.error_message if latest_unified else None,
                        
                        # Fund status
                        'funds_status': wallet_hold.status.value if wallet_hold else 'unknown',
                        'funds_frozen': bool(wallet_hold and wallet_hold.status in [
                            WalletHoldStatus.HELD, WalletHoldStatus.FAILED_HELD
                        ]),
                        
                        # Admin action status
                        'existing_tokens': existing_tokens,
                        'can_generate_tokens': existing_tokens < self.max_tokens_per_cashout,
                        
                        # Time info
                        'hours_pending': self._calculate_hours_pending(cashout.updated_at),
                        'is_high_priority': self._is_high_priority(cashout),
                        
                        # Action eligibility
                        'can_retry': self._can_retry_transaction(cashout),
                        'can_refund': self._can_refund_transaction(cashout, wallet_hold),
                        'can_decline': True,  # Can always decline
                    }
                    
                    failures.append(failure_info)
                    
                except Exception as e:
                    logger.error(f"Error processing cashout {cashout.id}: {e}")
                    # Include basic info even if detailed processing fails
                    failures.append({
                        'id': cashout.id,
                        'error': f"Failed to process details: {str(e)}",
                        'amount': float(cashout.amount_requested),
                        'currency': cashout.currency,
                        'created_at': cashout.created_at.isoformat()
                    })
            
            # Calculate summary statistics
            total_amount = sum(f.get('amount', 0) for f in failures)
            high_priority_count = len([f for f in failures if f.get('is_high_priority', False)])
            
            # Group by currency for summary
            currency_breakdown = {}
            for failure in failures:
                currency = failure.get('currency', 'USD')
                if currency not in currency_breakdown:
                    currency_breakdown[currency] = {'count': 0, 'total_amount': 0}
                currency_breakdown[currency]['count'] += 1
                currency_breakdown[currency]['total_amount'] += failure.get('amount', 0)
            
            return {
                'failures': failures,
                'pagination': {
                    'total_count': total_count,
                    'limit': limit,
                    'offset': offset,
                    'has_next': offset + limit < total_count,
                    'has_prev': offset > 0,
                    'next_offset': offset + limit if offset + limit < total_count else None,
                    'prev_offset': max(0, offset - limit) if offset > 0 else None,
                    'total_pages': (total_count + limit - 1) // limit,
                    'current_page': (offset // limit) + 1
                },
                'summary': {
                    'total_failures': len(failures),
                    'total_amount': total_amount,
                    'high_priority_count': high_priority_count,
                    'currency_breakdown': currency_breakdown,
                    'oldest_failure': min((f['created_at'] for f in failures), default=None),
                    'newest_failure': max((f['created_at'] for f in failures), default=None)
                }
            }
            
        except Exception as e:
            logger.error(f"Error fetching pending failures: {e}")
            return {
                'failures': [],
                'pagination': {'total_count': 0, 'has_next': False, 'has_prev': False},
                'summary': {'error': str(e)},
                'error': str(e)
            }
    
    def generate_secure_token(self, session: Session, cashout_id: str, 
                            action: AdminActionType, admin_email: str,
                            admin_user_id: Optional[int] = None) -> Optional[AdminActionToken]:
        """
        Generate secure token for admin email actions
        
        Args:
            session: Database session
            cashout_id: Target cashout ID
            action: Action type (RETRY, REFUND, DECLINE)
            admin_email: Admin email address
            admin_user_id: Optional Telegram admin user ID
            
        Returns:
            AdminActionToken if successful, None if failed
        """
        try:
            # Check if cashout exists and is eligible
            cashout = session.query(Cashout).filter(Cashout.id == cashout_id).first()
            if not cashout:
                logger.error(f"Cashout {cashout_id} not found for token generation")
                return None
            
            if cashout.status != CashoutStatus.ADMIN_PENDING:
                logger.error(f"Cashout {cashout_id} is not in ADMIN_PENDING status")
                return None
            
            # Check existing token limit
            existing_count = (
                session.query(AdminActionToken)
                .filter(AdminActionToken.cashout_id == cashout_id)
                .filter(AdminActionToken.used_at.is_(None))
                .count()
            )
            
            if existing_count >= self.max_tokens_per_cashout:
                logger.warning(f"Token limit reached for cashout {cashout_id}")
                return None
            
            # Generate cryptographically secure token
            token = secrets.token_urlsafe(32)  # 43 characters, URL-safe
            
            # Set expiration time
            expires_at = datetime.utcnow() + timedelta(hours=self.token_expiry_hours)
            
            # Create token record
            action_token = AdminActionToken(
                token=token,
                action=action.value,
                cashout_id=cashout_id,
                admin_email=admin_email,
                admin_user_id=admin_user_id,
                expires_at=expires_at,
                action_metadata={
                    'cashout_amount': float(cashout.amount_requested),
                    'cashout_currency': cashout.currency,
                    'user_id': cashout.user_id,
                    'generated_by': 'admin_failure_service',
                    'action_type': action.value
                }
            )
            
            session.add(action_token)
            session.commit()
            
            logger.info(f"Generated secure token for {action.value} action on cashout {cashout_id}")
            return action_token
            
        except Exception as e:
            logger.error(f"Error generating secure token: {e}")
            session.rollback()
            return None
    
    def validate_and_use_token(self, session: Session, token: str, 
                              expected_action: str, ip_address: str = None,
                              user_agent: str = None) -> Tuple[bool, Optional[AdminActionToken], str]:
        """
        Validate token and mark as used if valid
        
        Args:
            session: Database session
            token: Token to validate
            expected_action: Expected action type
            ip_address: Client IP for audit
            user_agent: Client user agent for audit
            
        Returns:
            Tuple of (is_valid, token_object, error_message)
        """
        try:
            # Find token
            action_token = (
                session.query(AdminActionToken)
                .filter(AdminActionToken.token == token)
                .first()
            )
            
            if not action_token:
                return False, None, "Invalid token"
            
            # Check if already used
            if action_token.is_used:
                logger.warning(f"Attempt to reuse token {token[:8]}... from IP {ip_address}")
                return False, action_token, "Token has already been used"
            
            # Check expiration
            if action_token.is_expired:
                return False, action_token, "Token has expired"
            
            # Check action type
            if action_token.action != expected_action:
                return False, action_token, f"Token is for {action_token.action}, not {expected_action}"
            
            # Check if cashout still exists and is in correct status
            cashout = session.query(Cashout).filter(Cashout.id == action_token.cashout_id).first()
            if not cashout:
                return False, action_token, "Associated cashout not found"
            
            if cashout.status != CashoutStatus.ADMIN_PENDING:
                return False, action_token, f"Cashout is no longer pending (status: {cashout.status.value})"
            
            # Mark token as used
            action_token.mark_as_used(
                ip_address=ip_address,
                user_agent=user_agent,
                result="VALIDATED"
            )
            
            session.commit()
            
            logger.info(f"Token validated and marked as used for {expected_action} on cashout {action_token.cashout_id}")
            return True, action_token, ""
            
        except Exception as e:
            logger.error(f"Error validating token: {e}")
            session.rollback()
            return False, None, f"Validation error: {str(e)}"
    
    def retry_transaction(self, session: Session, cashout_id: str, 
                         admin_user_id: int, admin_notes: str = None) -> Tuple[bool, str]:
        """
        Retry a failed transaction
        
        Args:
            session: Database session
            cashout_id: Cashout to retry
            admin_user_id: Admin performing the action
            admin_notes: Optional admin notes
            
        Returns:
            Tuple of (success, message)
        """
        try:
            # Get cashout
            cashout = session.query(Cashout).filter(Cashout.id == cashout_id).first()
            if not cashout:
                return False, "Cashout not found"
            
            if cashout.status != CashoutStatus.ADMIN_PENDING:
                return False, f"Cashout is not in ADMIN_PENDING status (current: {cashout.status.value})"
            
            # Update cashout status and admin notes
            cashout.status = CashoutStatus.PENDING
            cashout.retry_count = (cashout.retry_count or 0) + 1
            cashout.last_attempt_at = datetime.utcnow()
            if admin_notes:
                existing_notes = cashout.admin_notes or ""
                timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                cashout.admin_notes = f"{existing_notes}\n[{timestamp}] RETRY by admin {admin_user_id}: {admin_notes}".strip()
            
            # Reset error messages for fresh retry
            cashout.error_message = None
            cashout.failure_reason = None
            
            session.commit()
            
            logger.info(f"Admin {admin_user_id} initiated retry for cashout {cashout_id}")
            return True, f"Transaction {cashout_id} queued for retry"
            
        except Exception as e:
            logger.error(f"Error retrying transaction {cashout_id}: {e}")
            session.rollback()
            return False, f"Retry failed: {str(e)}"
    
    def refund_transaction(self, session: Session, cashout_id: str, 
                          admin_user_id: int, admin_notes: str = None) -> Tuple[bool, str]:
        """
        Refund a failed transaction to user's wallet
        
        Args:
            session: Database session
            cashout_id: Cashout to refund
            admin_user_id: Admin performing the action
            admin_notes: Optional admin notes
            
        Returns:
            Tuple of (success, message)
        """
        try:
            # Get cashout and related data
            cashout = session.query(Cashout).filter(Cashout.id == cashout_id).first()
            if not cashout:
                return False, "Cashout not found"
            
            if cashout.status != CashoutStatus.ADMIN_PENDING:
                return False, f"Cashout is not in ADMIN_PENDING status (current: {cashout.status.value})"
            
            # Get wallet hold to ensure funds are available
            wallet_hold = (
                session.query(WalletHolds)
                .filter(WalletHolds.cashout_id == cashout_id)
                .filter(WalletHolds.status.in_([
                    WalletHoldStatus.HELD, WalletHoldStatus.FAILED_HELD
                ]))
                .first()
            )
            
            if not wallet_hold:
                return False, "No held funds found for this cashout"
            
            # Use CryptoServiceAtomic to perform the refund
            refund_result = CryptoServiceAtomic.release_cashout_hold_to_available(
                session, cashout.user_id, cashout_id, 
                f"Admin refund by user {admin_user_id}"
            )
            
            if not refund_result:
                return False, "Failed to release funds to user wallet"
            
            # Update cashout status
            cashout.status = CashoutStatus.REFUNDED
            cashout.refunded_at = datetime.utcnow()
            if admin_notes:
                existing_notes = cashout.admin_notes or ""
                timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                cashout.admin_notes = f"{existing_notes}\n[{timestamp}] REFUNDED by admin {admin_user_id}: {admin_notes}".strip()
            
            # Update wallet hold status
            wallet_hold.status = WalletHoldStatus.REFUND_APPROVED
            
            session.commit()
            
            logger.info(f"Admin {admin_user_id} refunded cashout {cashout_id} to user wallet")
            return True, f"Transaction {cashout_id} refunded to user wallet"
            
        except Exception as e:
            logger.error(f"Error refunding transaction {cashout_id}: {e}")
            session.rollback()
            return False, f"Refund failed: {str(e)}"
    
    def decline_transaction(self, session: Session, cashout_id: str, 
                           admin_user_id: int, admin_notes: str = None) -> Tuple[bool, str]:
        """
        Decline a failed transaction permanently
        
        Args:
            session: Database session
            cashout_id: Cashout to decline
            admin_user_id: Admin performing the action
            admin_notes: Optional admin notes (required for decline)
            
        Returns:
            Tuple of (success, message)
        """
        try:
            if not admin_notes:
                return False, "Admin notes are required when declining a transaction"
            
            # Get cashout
            cashout = session.query(Cashout).filter(Cashout.id == cashout_id).first()
            if not cashout:
                return False, "Cashout not found"
            
            if cashout.status != CashoutStatus.ADMIN_PENDING:
                return False, f"Cashout is not in ADMIN_PENDING status (current: {cashout.status.value})"
            
            # Update cashout status
            cashout.status = CashoutStatus.DECLINED
            cashout.declined_at = datetime.utcnow()
            
            # Add admin notes with timestamp
            existing_notes = cashout.admin_notes or ""
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cashout.admin_notes = f"{existing_notes}\n[{timestamp}] DECLINED by admin {admin_user_id}: {admin_notes}".strip()
            
            # Note: Funds remain frozen for further admin review
            # This is intentional for declined transactions
            
            session.commit()
            
            logger.info(f"Admin {admin_user_id} declined cashout {cashout_id}")
            return True, f"Transaction {cashout_id} declined permanently"
            
        except Exception as e:
            logger.error(f"Error declining transaction {cashout_id}: {e}")
            session.rollback()
            return False, f"Decline failed: {str(e)}"
    
    def get_failure_details(self, session: Session, cashout_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific failure"""
        try:
            cashout = session.query(Cashout).filter(Cashout.id == cashout_id).first()
            if not cashout:
                return None
            
            user = session.query(User).filter(User.id == cashout.user_id).first()
            
            # Get unified transaction history (query by reference_id which stores cashout.cashout_id)
            unified_transactions = (
                session.query(UnifiedTransaction)
                .filter(UnifiedTransaction.reference_id == cashout.cashout_id)
                .order_by(desc(UnifiedTransaction.created_at))
                .all()
            )
            
            # Get wallet holds
            wallet_holds = (
                session.query(WalletHolds)
                .filter(WalletHolds.cashout_id == cashout_id)
                .order_by(desc(WalletHolds.created_at))
                .all()
            )
            
            # Get admin tokens
            admin_tokens = (
                session.query(AdminActionToken)
                .filter(AdminActionToken.cashout_id == cashout_id)
                .order_by(desc(AdminActionToken.created_at))
                .all()
            )
            
            return {
                'cashout': {
                    'id': cashout.id,
                    'user_id': cashout.user_id,
                    'amount': float(cashout.amount_requested),
                    'currency': cashout.currency,
                    'status': cashout.status.value,
                    'destination_type': cashout.destination_type,
                    'destination_id': cashout.destination_id,
                    'error_message': cashout.error_message,
                    'failure_reason': cashout.failure_reason,
                    'retry_count': cashout.retry_count or 0,
                    'admin_notes': cashout.admin_notes,
                    'created_at': cashout.created_at.isoformat(),
                    'updated_at': cashout.updated_at.isoformat()
                },
                'user': {
                    'id': user.id if user else None,
                    'name': user.first_name if user else 'Unknown',
                    'username': user.username if user and user.username else None,
                    'telegram_id': user.telegram_id if user else None
                },
                'unified_transactions': [
                    {
                        'id': ut.id,
                        'status': ut.status.value,
                        'error_message': ut.error_message,
                        'created_at': ut.created_at.isoformat()
                    } for ut in unified_transactions
                ],
                'wallet_holds': [
                    {
                        'id': wh.id,
                        'status': wh.status.value,
                        'amount': float(wh.amount),
                        'created_at': wh.created_at.isoformat()
                    } for wh in wallet_holds
                ],
                'admin_tokens': [
                    {
                        'id': at.id,
                        'action': at.action,
                        'created_at': at.created_at.isoformat(),
                        'expires_at': at.expires_at.isoformat(),
                        'used_at': at.used_at.isoformat() if at.used_at else None,
                        'is_valid': at.is_valid
                    } for at in admin_tokens
                ]
            }
            
        except Exception as e:
            logger.error(f"Error getting failure details for {cashout_id}: {e}")
            return None
    
    def cleanup_expired_tokens(self, session: Session) -> int:
        """Clean up expired and used tokens"""
        try:
            cutoff_time = datetime.utcnow()
            
            # Delete expired or used tokens older than 7 days
            old_cutoff = datetime.utcnow() - timedelta(days=7)
            
            deleted_count = (
                session.query(AdminActionToken)
                .filter(
                    or_(
                        AdminActionToken.expires_at < cutoff_time,
                        and_(
                            AdminActionToken.used_at.isnot(None),
                            AdminActionToken.used_at < old_cutoff
                        )
                    )
                )
                .delete(synchronize_session=False)
            )
            
            session.commit()
            
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} expired/old admin action tokens")
            
            return deleted_count
            
        except Exception as e:
            logger.error(f"Error cleaning up expired tokens: {e}")
            session.rollback()
            return 0
    
    def _calculate_hours_pending(self, updated_at: datetime) -> float:
        """Calculate hours since last update"""
        return (datetime.utcnow() - updated_at).total_seconds() / 3600
    
    def _is_high_priority(self, cashout: Cashout) -> bool:
        """Determine if a cashout is high priority"""
        # High priority if amount is large or has been pending for long time
        hours_pending = self._calculate_hours_pending(cashout.updated_at)
        large_amount = float(cashout.amount_requested) >= 1000
        long_pending = hours_pending >= 48
        
        return large_amount or long_pending
    
    def _can_retry_transaction(self, cashout: Cashout) -> bool:
        """Check if transaction can be retried"""
        # Can retry if not too many attempts and not user error
        max_retries = 3
        current_retries = cashout.retry_count or 0
        
        return current_retries < max_retries
    
    def _can_refund_transaction(self, cashout: Cashout, wallet_hold: Optional[WalletHolds]) -> bool:
        """Check if transaction can be refunded"""
        # Can refund if funds are held
        if not wallet_hold:
            return False
        
        return wallet_hold.status in [
            WalletHoldStatus.HELD, 
            WalletHoldStatus.FAILED_HELD
        ]


# Global service instance
admin_failure_service = AdminFailureService()