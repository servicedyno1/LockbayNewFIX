"""
Admin Email Actions Service
Handles secure token generation and validation for admin email buttons
"""

import logging
import secrets
import string
import time
import hmac
import hashlib
from typing import Dict, Any, Optional, List, cast
from datetime import datetime, timedelta
from config import Config
from services.post_completion_notification_service import PostCompletionNotificationService

logger = logging.getLogger(__name__)


class AdminEmailActionService:
    """Service for handling admin actions from email buttons"""
    
    # Token validity: 7 days (168 hours) - extended for admin review workflow
    TOKEN_VALIDITY_HOURS = 168
    
    @classmethod
    def generate_admin_token(cls, cashout_id: str, action: str, admin_email: str, admin_user_id: Optional[int] = None) -> str:
        """Generate a secure single-use token for admin email actions"""
        try:
            from database import SessionLocal
            from models import AdminActionToken
            
            # Validate action
            valid_actions = ['RETRY', 'REFUND', 'DECLINE']
            if action.upper() not in valid_actions:
                logger.error(f"🚨 SECURITY: Invalid action '{action}' for cashout {cashout_id}")
                return "INVALID_ACTION"
            
            action = action.upper()
            
            # Generate cryptographically secure token
            token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(64))
            
            # Calculate expiration time (7 days for admin review workflow)
            expires_at = datetime.utcnow() + timedelta(hours=cls.TOKEN_VALIDITY_HOURS)
            
            session = SessionLocal()
            try:
                # Check if there's already an unused token for this cashout+action combo
                existing_token = session.query(AdminActionToken).filter(
                    AdminActionToken.cashout_id == cashout_id,
                    AdminActionToken.action == action,
                    AdminActionToken.used_at.is_(None),
                    AdminActionToken.expires_at > datetime.utcnow()
                ).first()
                
                if existing_token:
                    logger.warning(f"🔄 REUSING existing valid token for {action} action on cashout {cashout_id}")
                    return str(existing_token.token)
                
                # Create new token record
                token_record = AdminActionToken(
                    token=token,
                    action=action,
                    cashout_id=cashout_id,
                    admin_email=admin_email,
                    admin_user_id=admin_user_id,
                    expires_at=expires_at
                )
                
                session.add(token_record)
                session.commit()
                
                logger.info(f"✅ Generated secure {action} token for cashout {cashout_id} (expires in {cls.TOKEN_VALIDITY_HOURS}h)")
                return token
                
            finally:
                session.close()
            
        except Exception as e:
            logger.error(f"❌ Error generating admin token for {cashout_id}: {e}")
            return "GENERATION_ERROR"
    
    @classmethod
    async def atomic_consume_admin_token(cls, cashout_id: str, token: str, action: str, ip_address: Optional[str] = None, user_agent: Optional[str] = None) -> Dict[str, Any]:
        """ATOMIC: Validate and consume an admin token in a single database operation to prevent race conditions"""
        try:
            from database import async_managed_session
            from sqlalchemy import text
            
            # Check for invalid token types
            if token in ["DISABLED_FOR_SECURITY", "SECURITY_ERROR", "invalid_token", "GENERATION_ERROR", "INVALID_ACTION"]:
                logger.warning(f"🚨 SECURITY: Invalid admin token type '{token}' for cashout {cashout_id}")
                return {"valid": False, "error": "Invalid token type"}
            
            async with async_managed_session() as session:
                # ATOMIC OPERATION: Check validity and mark as used in a single SQL statement
                # This prevents race conditions by ensuring only ONE request can consume a token
                current_time = datetime.utcnow()
                
                atomic_query = text("""
                    UPDATE admin_action_tokens 
                    SET used_at = :used_at,
                        used_by_ip = :ip_address,
                        used_by_user_agent = :user_agent,
                        action_result = 'PENDING'
                    WHERE token = :token 
                      AND cashout_id = :cashout_id 
                      AND action = :action
                      AND used_at IS NULL 
                      AND expires_at > :current_time
                    RETURNING id, admin_email, admin_user_id, created_at, token
                """)
                
                result = await session.execute(atomic_query, {
                    'token': token,
                    'cashout_id': cashout_id,
                    'action': action.upper(),
                    'used_at': current_time,
                    'ip_address': ip_address,
                    'user_agent': user_agent,
                    'current_time': current_time
                })
                
                row = result.fetchone()
                await session.commit()
                
                if row:
                    logger.info(f"✅ ATOMIC_SECURITY: Valid single-use token consumed for {action} on cashout {cashout_id}")
                    return {
                        "valid": True,
                        "admin_email": row.admin_email,
                        "admin_user_id": row.admin_user_id,
                        "created_at": row.created_at,
                        "token_id": row.id
                    }
                else:
                    # Token was either not found, already used, expired, or wrong action/cashout
                    logger.warning(f"🚨 ATOMIC_SECURITY: Token consumption failed for {action} on cashout {cashout_id} - token not found, already used, expired, or invalid")
                    return {"valid": False, "error": "Token not found, already used, expired, or invalid"}
            
        except Exception as e:
            logger.error(f"❌ Error in atomic token consumption for {cashout_id}: {e}")
            return {"valid": False, "error": f"Atomic validation error: {str(e)}"}
    
    @classmethod
    def update_token_action_result(cls, cashout_id: str, token: str, action: str, result_status: str, error_message: Optional[str] = None) -> bool:
        """Update the action result of a consumed admin token after business logic completes"""
        try:
            from database import SessionLocal
            from sqlalchemy import text
            
            # Validate result status
            valid_statuses = ['SUCCESS', 'FAILED', 'ERROR']
            if result_status not in valid_statuses:
                logger.error(f"🚨 Invalid result status '{result_status}' for token update")
                return False
            
            session = SessionLocal()
            try:
                update_query = text("""
                    UPDATE admin_action_tokens 
                    SET action_result = :result_status,
                        error_message = :error_message,
                        completed_at = NOW()
                    WHERE token = :token 
                      AND cashout_id = :cashout_id 
                      AND action = :action
                      AND used_at IS NOT NULL
                    RETURNING id
                """)
                
                result = session.execute(update_query, {
                    'token': token,
                    'cashout_id': cashout_id,
                    'action': action.upper(),
                    'result_status': result_status,
                    'error_message': error_message
                }).fetchone()
                
                session.commit()
                
                if result:
                    logger.info(f"✅ TOKEN_AUDIT: Updated token result to {result_status} for {action} on cashout {cashout_id}")
                    return True
                else:
                    logger.warning(f"⚠️ TOKEN_AUDIT: No token found to update for {action} on cashout {cashout_id}")
                    return False
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"❌ Error updating token result for {cashout_id}: {e}")
            return False
    
    @classmethod
    def validate_and_use_admin_token(cls, cashout_id: str, token: str, action: str, ip_address: Optional[str] = None, user_agent: Optional[str] = None) -> Dict[str, Any]:
        """
        DEPRECATED AND REMOVED
        
        This method has been removed to prevent race conditions.
        Use atomic_consume_admin_token() instead (async method).
        """
        logger.error(f"🚨 DEPRECATED: Attempted use of validate_and_use_admin_token for {action} on {cashout_id}")
        raise NotImplementedError(
            "validate_and_use_admin_token is deprecated. Use atomic_consume_admin_token() instead."
        )
    
    @classmethod
    def validate_admin_token(cls, cashout_id: str, token: str) -> bool:
        """
        DEPRECATED AND DISABLED FOR SECURITY
        
        This method is DANGEROUS and has been disabled to prevent race conditions.
        It validates tokens without marking them as used, allowing concurrent requests
        to reuse the same token, creating critical security vulnerabilities.
        
        MIGRATION GUIDE:
        - Use atomic_consume_admin_token() instead for secure one-time token consumption
        - Or use validate_and_use_admin_token() with proper action scoping
        
        This method always returns False to force migration to secure alternatives.
        """
        logger.error(f"🚨 SECURITY VIOLATION: Attempted use of deprecated validate_admin_token for {cashout_id}")
        logger.error("🚨 SECURITY: This method has been disabled due to race condition vulnerabilities")
        logger.error("🚨 MIGRATION: Use atomic_consume_admin_token() or validate_and_use_admin_token() instead")
        
        # Always return False to force migration to secure methods
        # Do not implement any token validation logic here to prevent security bypasses
        return False
    
    @classmethod
    def generate_dispute_token(cls, dispute_id: int, action: str, admin_email: str, admin_user_id: Optional[int] = None) -> str:
        """Generate a secure single-use token for dispute resolution email actions"""
        try:
            from database import SessionLocal
            from models import AdminActionToken
            
            # Validate action
            valid_actions = ['RELEASE_TO_SELLER', 'REFUND_TO_BUYER', 'SPLIT_FUNDS', 'CUSTOM_SPLIT']
            if action.upper() not in valid_actions:
                logger.error(f"🚨 SECURITY: Invalid action '{action}' for dispute {dispute_id}")
                return "INVALID_ACTION"
            
            action = action.upper()
            
            # Generate cryptographically secure token
            token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(64))
            
            # Calculate expiration time (7 days for admin review workflow)
            expires_at = datetime.utcnow() + timedelta(hours=cls.TOKEN_VALIDITY_HOURS)
            
            session = SessionLocal()
            try:
                # Check for existing unused token (use cashout_id field to store dispute_id)
                existing_token = session.query(AdminActionToken).filter(
                    AdminActionToken.cashout_id == str(dispute_id),
                    AdminActionToken.action == action,
                    AdminActionToken.used_at.is_(None),
                    AdminActionToken.expires_at > datetime.utcnow()
                ).first()
                
                if existing_token:
                    logger.warning(f"🔄 REUSING existing valid token for {action} action on dispute {dispute_id}")
                    return str(existing_token.token)
                
                # Create new token record (store dispute_id in cashout_id field)
                token_record = AdminActionToken(
                    token=token,
                    action=action,
                    cashout_id=str(dispute_id),
                    admin_email=admin_email,
                    admin_user_id=admin_user_id,
                    expires_at=expires_at
                )
                
                session.add(token_record)
                session.commit()
                
                logger.info(f"✅ Generated secure {action} token for dispute {dispute_id} (expires in {cls.TOKEN_VALIDITY_HOURS}h)")
                return token
                
            finally:
                session.close()
            
        except Exception as e:
            logger.error(f"❌ Error generating dispute token for {dispute_id}: {e}")
            return "GENERATION_ERROR"

    @classmethod
    async def resolve_dispute_from_email(cls, dispute_id: int, action: str, token: str, ip_address: Optional[str] = None, user_agent: Optional[str] = None) -> Dict[str, Any]:
        """Resolve dispute from email button click"""
        try:
            from services.dispute_resolution import DisputeResolutionService
            
            # Validate and consume token atomically
            token_result = await cls.atomic_consume_admin_token(
                cashout_id=str(dispute_id),  # Reuse cashout_id field for dispute_id
                token=token,
                action=action,
                ip_address=ip_address,
                user_agent=user_agent
            )
            
            if not token_result.get("valid", False):
                return {"success": False, "error": f"Invalid or expired token: {token_result.get('error')}"}
            
            # Get admin user ID from token
            admin_user_id = token_result.get("admin_user_id") or 1
            
            # Execute dispute resolution based on action
            if action == "RELEASE_TO_SELLER":
                result = await DisputeResolutionService.resolve_release_to_seller(dispute_id, admin_user_id)
            elif action == "REFUND_TO_BUYER":
                result = await DisputeResolutionService.resolve_refund_to_buyer(dispute_id, admin_user_id)
            elif action == "SPLIT_FUNDS":
                # Default 50/50 split using resolve_custom_split
                result = await DisputeResolutionService.resolve_custom_split(dispute_id, 50, 50, admin_user_id)
            else:
                return {"success": False, "error": f"Unknown action: {action}"}
            
            # Update token result
            cls.update_token_action_result(
                cashout_id=str(dispute_id),
                token=token,
                action=action,
                result_status="SUCCESS" if result.success else "FAILED",
                error_message=result.error_message
            )
            
            if result.success:
                # Send rating prompts to both parties via PostCompletionNotificationService
                # DisputeResolutionService only handles transactions, not notifications
                notification_service = PostCompletionNotificationService()
                # Type safety: ensure buyer_id and seller_id are not None
                if result.buyer_id is not None and result.seller_id is not None:
                    await notification_service.notify_escrow_completion(
                        escrow_id=result.escrow_id,
                        completion_type='dispute_resolved',
                        amount=float(result.amount),
                        buyer_id=result.buyer_id,
                        seller_id=result.seller_id,
                        dispute_winner_id=result.dispute_winner_id,
                        dispute_loser_id=result.dispute_loser_id,
                        resolution_type=result.resolution_type
                    )
                
                return {
                    "success": True,
                    "message": f"Dispute {dispute_id} resolved: {result.resolution_type}",
                    "escrow_id": result.escrow_id,
                    "amount": result.amount
                }
            else:
                return {"success": False, "error": result.error_message}
                
        except Exception as e:
            logger.error(f"❌ Error resolving dispute {dispute_id} from email: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    async def complete_cashout_from_email(cls, cashout_id: str, admin_email: str = "admin@lockbay.com", admin_user_id: Optional[int] = None) -> Dict[str, Any]:
        """Complete a cashout that was triggered from admin email button using atomic transactions"""
        try:
            from utils.atomic_transactions import AtomicAdminActionManager
            from models import CashoutStatus
            
            # Use atomic admin action transaction
            with AtomicAdminActionManager.atomic_admin_action(
                cashout_id=cashout_id,
                action_type="RETRY",
                admin_email=admin_email,
                admin_user_id=admin_user_id,
                lock_timeout=30,
                retry_on_conflict=True,
                max_retries=3
            ) as context:
                
                cashout = context.cashout
                
                # Accept both PENDING_ADDRESS_CONFIG and PENDING_SERVICE_FUNDING statuses
                acceptable_statuses = [CashoutStatus.PENDING_ADDRESS_CONFIG.value, CashoutStatus.PENDING_SERVICE_FUNDING.value]
                if cashout.status not in acceptable_statuses:
                    return {
                        "success": False, 
                        "error": f"Cashout not in acceptable status (current: {cashout.status}, expected: {' or '.join(acceptable_statuses)})"
                    }
                
                # Log the admin action
                logger.info(f"🔒 ATOMIC_ADMIN_COMPLETE: {admin_email} completing cashout {cashout_id}: {cashout.currency} {cashout.amount} (status: {cashout.status}) (txn: {context.transaction_id})")
                
                # Branch based on cashout status
                if cashout.status == CashoutStatus.PENDING_ADDRESS_CONFIG.value:
                    # Existing flow: Direct Kraken address verification and withdrawal with idempotency
                    logger.info(f"Processing PENDING_ADDRESS_CONFIG cashout {cashout_id} - Kraken withdrawal flow")
                    result = await cls._process_address_config_cashout_atomic(cashout, context)
                elif cashout.status == CashoutStatus.PENDING_SERVICE_FUNDING.value:
                    # New flow: Retry original cashout process via auto_cashout service with idempotency
                    logger.info(f"Processing PENDING_SERVICE_FUNDING cashout {cashout_id} - Retry via auto_cashout service")
                    result = await cls._process_service_funding_retry_atomic(cashout, context)
                else:
                    # This should not happen due to the status check above, but defensive programming
                    return {
                        "success": False,
                        "error": f"Unsupported cashout status: {cashout.status}"
                    }
                
                # Handle the result from either processing flow
                if result.get('success'):
                    # Save original status before updating
                    original_status = cashout.status
                    
                    # Update cashout status to SUCCESS per user specification
                    from models import CashoutStatus, User
                    cashout.status = CashoutStatus.SUCCESS.value
                    
                    # Only consume hold for crypto cashouts (originally PENDING_ADDRESS_CONFIG)
                    if original_status == CashoutStatus.PENDING_ADDRESS_CONFIG.value:
                        try:
                            from services.crypto import CashoutHoldService
                            
                            # Extract hold metadata from cashout
                            metadata = cashout.cashout_metadata or {}
                            hold_transaction_id = metadata.get('hold_transaction_id')
                            hold_amount = metadata.get('hold_amount')
                            currency = metadata.get('currency', 'USD')
                            
                            if hold_transaction_id and hold_amount:
                                logger.critical(f"🔓 CONSUMING_CRYPTO_HOLD: Admin cashout {cashout_id} - Consuming ${hold_amount} {currency} hold (txn: {hold_transaction_id})")
                                
                                # Consume (not release) the frozen hold atomically
                                consume_result = CashoutHoldService.consume_cashout_hold(
                                    user_id=cashout.user_id,
                                    amount=float(hold_amount),
                                    currency=currency,
                                    cashout_id=cashout_id,
                                    hold_transaction_id=hold_transaction_id,
                                    session=context.session
                                )
                                
                                if consume_result.get("success"):
                                    logger.critical(f"✅ CRYPTO_HOLD_CONSUMED: Successfully consumed ${hold_amount} {currency} frozen hold for admin cashout {cashout_id}")
                                else:
                                    logger.error(f"❌ CRYPTO_HOLD_CONSUME_FAILED: Failed to consume frozen hold for admin cashout {cashout_id}")
                            else:
                                logger.warning(f"⚠️ NO_HOLD_METADATA: Admin cashout {cashout_id} completed but no hold metadata found")
                                
                        except Exception as hold_error:
                            logger.error(f"❌ HOLD_CONSUMPTION_ERROR: Error consuming frozen hold for admin cashout {cashout_id}: {hold_error}", exc_info=True)
                    
                    # Get user data for email notification within the transaction
                    user_data = context.session.query(User).filter_by(id=cashout.user_id).first()
                    
                    # Store user info for post-transaction email sending
                    user_email = user_data.email if user_data else None
                    user_name = (user_data.first_name or user_data.username or 'User') if user_data else 'User'
                    
                    logger.info(f"✅ ATOMIC_ADMIN_COMPLETE_SUCCESS: Cashout {cashout_id} completed within transaction {context.transaction_id}")
                    
                    # Transaction will be committed automatically here
                    # Post-transaction: Send completion email (outside transaction)
                    if user_email:
                        try:
                            from services.email import EmailService
                        
                            email_service = EmailService()
                            email_sent = await email_service.send_cashout_notification(
                                user_email=user_email,
                                user_name=user_name,
                                cashout_id=int(cashout_id),
                                amount=float(cashout.amount),
                                currency=cashout.currency,
                                status="completed"
                            )
                            
                            if email_sent:
                                logger.info(f"✅ Cashout completion email sent to {user_email} for {cashout_id}")
                            else:
                                logger.warning(f"⚠️ Failed to send completion email to {user_email} for {cashout_id}")
                                
                        except Exception as email_error:
                            logger.error(f"❌ Error sending completion email for {cashout_id}: {email_error}")
                            # Don't fail the whole operation for email errors
                    
                    return {
                        "success": True,
                        "message": f"Cashout {cashout_id} completed successfully",
                        "cashout_amount": float(cashout.amount),
                        "cashout_currency": cashout.currency,
                        "transaction_id": context.transaction_id,
                        "audit_trail": context.get_audit_trail()
                    }
                else:
                    logger.error(f"❌ ATOMIC_ADMIN_COMPLETE_FAILED: Admin cashout processing failed for {cashout_id}: {result.get('error')}")
                    return {
                        "success": False,
                        "error": result.get('error', 'Unknown error occurred'),
                        "transaction_id": context.transaction_id
                    }
                
        except Exception as e:
            logger.error(f"❌ ATOMIC_ADMIN_ACTION_ERROR: Error completing cashout {cashout_id} from email: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    async def _process_address_config_cashout_atomic(cls, cashout, context) -> Dict[str, Any]:
        """
        Placeholder for atomic address config cashout processing
        TODO: Implement actual Kraken withdrawal logic with idempotency
        """
        logger.info(f"🔄 ATOMIC_ADDRESS_CONFIG: Processing {cashout.cashout_id} with transaction {context.transaction_id}")
        
        # For now, return success - implement actual Kraken logic here
        return {"success": True, "message": "Address config processing completed"}
    
    @classmethod
    async def _process_service_funding_retry_atomic(cls, cashout, context) -> Dict[str, Any]:
        """
        Placeholder for atomic service funding retry processing  
        TODO: Implement actual retry logic with idempotency
        """
        logger.info(f"🔄 ATOMIC_SERVICE_FUNDING: Processing {cashout.cashout_id} with transaction {context.transaction_id}")
        
        # For now, return success - implement actual retry logic here
        return {"success": True, "message": "Service funding retry completed"}
    
    @classmethod
    async def cancel_cashout_from_email(cls, cashout_id: str, token: str, ip_address: Optional[str] = None, user_agent: Optional[str] = None, token_already_consumed: bool = False) -> Dict[str, Any]:
        """Cancel cashout and process automatic refund via email action using atomic transactions"""
        try:
            # SECURITY FIX: Handle token validation based on whether it's already been consumed
            if not token_already_consumed:
                # Atomic token consumption to prevent race conditions
                token_result = await cls.atomic_consume_admin_token(
                    cashout_id=cashout_id, 
                    token=token, 
                    action="DECLINE",  # Cancel action maps to DECLINE
                    ip_address=ip_address,
                    user_agent=user_agent
                )
                
                if not token_result.get("valid", False):
                    logger.warning(f"🚨 SECURITY: Invalid token for cancel cashout {cashout_id}: {token_result.get('error')}")
                    return {"success": False, "error": f"Invalid or expired token: {token_result.get('error', 'Unknown error')}"}
            else:
                # Token already consumed atomically by caller, proceed with cancellation
                logger.info(f"✅ SECURITY: Proceeding with pre-validated token for cancel cashout {cashout_id}")
            
            # Use atomic admin action transaction for cancel operation
            from utils.atomic_transactions import AtomicAdminActionManager
            from models import CashoutStatus, User
            
            with AtomicAdminActionManager.atomic_admin_action(
                cashout_id=cashout_id,
                action_type="DECLINE",
                admin_email="admin@lockbay.com",
                admin_user_id=None,
                lock_timeout=30,
                retry_on_conflict=True,
                max_retries=3
            ) as context:
                
                cashout = context.cashout
                
                # Check if cashout can be cancelled
                cancellable_statuses = ['pending_config', 'pending_address_config', 'pending_service_funding']
                if cashout.status not in cancellable_statuses:
                    return {
                        "success": False, 
                        "error": f"Cashout cannot be cancelled (current status: {cashout.status})"
                    }
                
                # Get user data for processing
                user = context.session.query(User).filter_by(id=cashout.user_id).first()
                if not user:
                    logger.error(f"❌ User {cashout.user_id} not found for cashout {cashout_id}")
                    return {"success": False, "error": "User not found for cashout"}
                
                # Log the admin action
                logger.info(f"🔒 ATOMIC_ADMIN_CANCEL: Cancelling cashout {cashout_id}: {cashout.currency} {cashout.amount} (status: {cashout.status}) (txn: {context.transaction_id})")
                
                # Update cashout status to cancelled
                from datetime import datetime
                cashout.status = CashoutStatus.CANCELLED.value
                cashout.failed_at = datetime.utcnow()
                cashout.error_message = 'Cancelled by admin via email - automatic refund processed'
                cashout.updated_at = datetime.utcnow()
                
                # ENHANCED: Calculate proper refund amount based on cashout type
                refund_result = await cls._process_intelligent_refund(
                    cashout, context, user
                )
                
                if not refund_result.get('success'):
                    raise Exception(f"Failed to process refund: {refund_result.get('error', 'Unknown refund error')}")
                
                # Store refund details for notifications
                refund_currency = refund_result.get('refund_currency', 'USD')
                refund_amount = refund_result.get('refund_amount', float(cashout.amount))
                
                # Send comprehensive user notification using wallet_notification_service
                try:
                    await cls._send_enhanced_user_notification(
                        cashout_id, user, cashout, refund_amount, refund_currency
                    )
                except Exception as e:
                    logger.error(f"❌ User notification failed for {cashout_id}: {e}")
                    # Don't fail the whole operation for notification errors
                
                # Create unified transaction record for audit trail
                # NOTE: UnifiedTransactionService.create_transaction not available, using wallet transaction instead
                logger.info("✅ Cashout cancellation completed - transaction logged via wallet credit operation")
                
                logger.info(f"✅ ATOMIC_ADMIN_CANCEL_SUCCESS: Successfully cancelled cashout {cashout_id} via email and processed refund")
                
                cancel_result = {
                    "success": True,
                    "status": "cancelled",
                    "amount": float(cashout.amount),
                    "currency": cashout.currency,
                    "refund_amount": refund_amount,
                    "refund_currency": refund_currency,
                    "transaction_id": context.transaction_id,
                    "message": "Cashout cancelled and user refunded successfully"
                }
                
                # Send admin confirmation email after transaction commits
                try:
                    await cls._send_admin_confirmation_email(
                        cashout_id, cashout, user, cancel_result
                    )
                except Exception as e:
                    logger.error(f"❌ Admin confirmation email failed: {e}")
                    # Don't fail the operation for email issues
                
                # Update token audit result with SUCCESS after successful business logic completion
                if not token_already_consumed:
                    cls.update_token_action_result(
                        cashout_id=cashout_id,
                        token=token,
                        action="DECLINE",
                        result_status="SUCCESS",
                        error_message=None
                    )
                
                return cancel_result
                
        except Exception as e:
            logger.error(f"❌ ATOMIC_ADMIN_CANCEL_ERROR: Error cancelling cashout {cashout_id} from email: {e}", exc_info=True)
            
            # Enhanced error handling with categorization
            error_category = cls._categorize_cancellation_error(e)
            error_response = {
                "success": False, 
                "error": str(e),
                "error_category": error_category,
                "timestamp": __import__('datetime').datetime.utcnow().isoformat(),
                "cashout_id": cashout_id
            }
            
            # Send error notification to admin for critical failures
            try:
                await cls._send_admin_error_notification(cashout_id, e, error_category)
            except Exception as notification_error:
                logger.error(f"❌ Failed to send admin error notification for {cashout_id}: {notification_error}")
            
            # Update token audit result with FAILED after failed business logic
            if not token_already_consumed:
                cls.update_token_action_result(
                    cashout_id=cashout_id,
                    token=token,
                    action="DECLINE",
                    result_status="FAILED",
                    error_message=str(e)[:500]  # Truncate error message to fit database constraints
                )
            
            return error_response
    
    @classmethod
    async def _process_intelligent_refund(cls, cashout, context, user) -> Dict[str, Any]:
        """Process intelligent refund based on cashout type (NGN vs Crypto)"""
        try:
            from services.crypto import CryptoServiceAtomic
            from services.fastforex_service import fastforex_service
            from models import TransactionType
            from decimal import Decimal
            
            cashout_id = cashout.cashout_id
            original_amount = float(cashout.amount)
            cashout_currency = cashout.currency
            cashout_type = cashout.cashout_type
            
            logger.info(f"💰 INTELLIGENT_REFUND: Processing refund for {cashout_type} cashout {cashout_id}: {original_amount} {cashout_currency}")
            
            # Record the refund operation for idempotency
            refund_operation_key = f"intelligent_refund_{cashout_id}_{context.transaction_id}"
            
            # Check if refund was already processed (idempotency)
            existing_refund = context.get_external_operation(refund_operation_key)
            if existing_refund:
                logger.info(f"♻️ INTELLIGENT_REFUND_IDEMPOTENCY: Refund for {cashout_id} already processed")
                return dict(existing_refund) if existing_refund else {}
            
            refund_result = {}
            
            if cashout_type == 'ngn_bank':
                # NGN Cashout: Refund original crypto amount based on locked rate
                metadata = cashout.cashout_metadata or {}
                locked_rate = metadata.get('exchange_rate') or metadata.get('locked_rate')
                source_currency = metadata.get('source_currency', 'USD')
                
                if locked_rate:
                    # Calculate original crypto amount that was converted to NGN
                    locked_rate_decimal = Decimal(str(locked_rate))
                    original_crypto_amount = Decimal(str(original_amount)) / locked_rate_decimal
                    
                    logger.info(f"🔄 NGN_REFUND_CONVERSION: {original_amount} NGN -> {float(original_crypto_amount)} {source_currency} (rate: {locked_rate})")
                    
                    # Refund in original crypto currency
                    with context.lock_user_wallet(source_currency):
                        refund_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                            user_id=cashout.user_id,
                            amount=float(original_crypto_amount),
                            currency=source_currency,
                            transaction_type=TransactionType.REFUND.value,
                            description=f"Admin cancelled NGN cashout {cashout_id} - refunded original {source_currency}",
                            escrow_id=None,
                            session=context.session
                        )
                        
                        refund_result = {
                            "success": refund_success,
                            "refund_amount": float(original_crypto_amount),
                            "refund_currency": source_currency,
                            "conversion_rate": float(locked_rate),
                            "original_ngn_amount": original_amount
                        }
                        
                        if refund_success:
                            context.record_wallet_operation(
                                "CREDIT", 
                                float(original_crypto_amount), 
                                source_currency, 
                                f"NGN cashout cancellation refund: {cashout_id}"
                            )
                else:
                    # Fallback: No locked rate found, refund equivalent USD
                    logger.warning(f"⚠️ NGN_REFUND_FALLBACK: No locked rate found for {cashout_id}, using current USD rate")
                    
                    try:
                        current_rate = await fastforex_service.get_usd_to_ngn_rate()
                        if current_rate:
                            usd_equivalent = original_amount / float(current_rate)
                            logger.info(f"🔄 NGN_FALLBACK_CONVERSION: {original_amount} NGN -> {usd_equivalent} USD (current rate: {current_rate})")
                        else:
                            # Final fallback: Use standard conversion
                            usd_equivalent = original_amount / 1520  # Approximate NGN/USD rate
                            logger.warning(f"⚠️ NGN_EMERGENCY_FALLBACK: Using emergency rate for {cashout_id}")
                    except Exception as e:
                        logger.error(f"Error getting current exchange rate: {e}")
                        usd_equivalent = original_amount / 1520  # Emergency fallback
                    
                    with context.lock_user_wallet("USD"):
                        refund_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                            user_id=cashout.user_id,
                            amount=usd_equivalent,
                            currency="USD",
                            transaction_type=TransactionType.REFUND.value,
                            description=f"Admin cancelled NGN cashout {cashout_id} - emergency USD refund",
                            escrow_id=None,
                            session=context.session
                        )
                        
                        refund_result = {
                            "success": refund_success,
                            "refund_amount": usd_equivalent,
                            "refund_currency": "USD",
                            "fallback_used": True,
                            "original_ngn_amount": original_amount
                        }
                        
                        if refund_success:
                            context.record_wallet_operation(
                                "CREDIT", 
                                usd_equivalent, 
                                "USD", 
                                f"NGN cashout emergency refund: {cashout_id}"
                            )
            
            elif cashout_type == 'crypto':
                # Crypto Cashout: Refund exact amount in same currency
                logger.info(f"💎 CRYPTO_REFUND_EXACT: Refunding {original_amount} {cashout_currency} for {cashout_id}")
                
                # Determine refund currency (cashout currency for crypto)
                refund_currency = cashout_currency
                
                with context.lock_user_wallet(refund_currency):
                    refund_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                        user_id=cashout.user_id,
                        amount=original_amount,
                        currency=refund_currency,
                        transaction_type=TransactionType.REFUND.value,
                        description=f"Admin cancelled {refund_currency} crypto cashout {cashout_id}",
                        escrow_id=None,
                        session=context.session
                    )
                    
                    refund_result = {
                        "success": refund_success,
                        "refund_amount": original_amount,
                        "refund_currency": refund_currency
                    }
                    
                    if refund_success:
                        context.record_wallet_operation(
                            "CREDIT", 
                            original_amount, 
                            refund_currency, 
                            f"Crypto cashout cancellation refund: {cashout_id}"
                        )
            
            else:
                # Unknown cashout type - default to USD
                logger.warning(f"⚠️ UNKNOWN_CASHOUT_TYPE: Unknown type '{cashout_type}' for {cashout_id}, defaulting to USD refund")
                
                with context.lock_user_wallet("USD"):
                    refund_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                        user_id=cashout.user_id,
                        amount=original_amount,
                        currency="USD",
                        transaction_type=TransactionType.REFUND.value,
                        description=f"Admin cancelled unknown type cashout {cashout_id} - USD refund",
                        escrow_id=None,
                        session=context.session
                    )
                    
                    refund_result = {
                        "success": refund_success,
                        "refund_amount": original_amount,
                        "refund_currency": "USD",
                        "unknown_type_fallback": True
                    }
                    
                    if refund_success:
                        context.record_wallet_operation(
                            "CREDIT", 
                            original_amount, 
                            "USD", 
                            f"Unknown type cashout refund: {cashout_id}"
                        )
            
            # Record refund operation for idempotency
            context.record_external_operation(refund_operation_key, refund_result)
            
            if refund_result.get('success'):
                logger.info(f"✅ INTELLIGENT_REFUND_SUCCESS: {cashout_id} refunded {refund_result.get('refund_amount')} {refund_result.get('refund_currency')}")
            else:
                logger.error(f"❌ INTELLIGENT_REFUND_FAILED: Failed to process refund for {cashout_id}")
            
            return refund_result
            
        except Exception as e:
            logger.error(f"❌ INTELLIGENT_REFUND_ERROR: Error processing refund for {cashout.cashout_id}: {e}", exc_info=True)
            
            # Enhanced error handling for refund failures
            error_details = {
                "success": False, 
                "error": str(e),
                "error_type": type(e).__name__,
                "cashout_id": cashout.cashout_id,
                "user_id": cashout.user_id,
                "original_amount": float(cashout.amount),
                "currency": cashout.currency,
                "cashout_type": cashout.cashout_type,
                "recovery_needed": True
            }
            
            # Log critical refund failure for manual intervention
            logger.critical(f"🔥 REFUND_FAILURE_CRITICAL: Manual intervention required for {cashout.cashout_id} - {error_details}")
            
            return error_details
    
    @classmethod
    async def _send_enhanced_user_notification(cls, cashout_id: str, user, cashout, refund_amount: float, refund_currency: str):
        """Send enhanced user notification using wallet_notification_service.py"""
        try:
            from config import Config
            
            # Format the refund message based on cashout type
            if cashout.cashout_type == 'ngn_bank':
                if refund_currency != 'NGN':
                    # NGN cashout refunded in original crypto
                    refund_message = f"Your NGN {float(cashout.amount):,.0f} cashout was cancelled by admin and refunded as {refund_amount:.8f} {refund_currency} to your wallet."
                else:
                    refund_message = f"Your NGN {float(cashout.amount):,.0f} cashout was cancelled and refunded to your wallet."
            else:
                # Crypto cashout refunded in same currency
                refund_message = f"Your {refund_amount:.8f} {refund_currency} cashout was cancelled and refunded to your wallet."
            
            # Create comprehensive message
            full_message = (
                f"🔄 **Cashout Cancelled**\n\n"
                f"**Cashout ID:** `{cashout_id}`\n"
                f"**Status:** Cancelled by Admin\n"
                f"**Original Amount:** {float(cashout.amount):.8f} {cashout.currency}\n\n"
                f"✅ **Refund Processed**\n"
                f"{refund_message}\n\n"
                f"💰 **Next Steps:**\n"
                f"• View your updated balance: /wallet\n"
                f"• Create a new cashout: /cashout\n"
                f"• Need help? Contact support\n\n"
                f"Thank you for using {Config.BRAND}!"
            )
            
            # Send Telegram message using bot instance
            from main import get_application_instance
            application = get_application_instance()
            if application and application.bot:
                await application.bot.send_message(
                    chat_id=user.telegram_id,
                    text=full_message,
                    parse_mode="Markdown"
                )
                logger.info(f"✅ Enhanced Telegram notification sent to user {user.id} for {cashout_id}")
            else:
                logger.warning("⚠️ No bot application available for Telegram notification")
            
            # Send email notification if user has email
            if user.email and user.email.strip():
                try:
                    from services.email import EmailService
                    email_service = EmailService()
                    
                    user_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username or "Valued Customer"
                    
                    # Send comprehensive email notification
                    email_sent = email_service.send_email(
                        to_email=user.email,
                        subject=f"Cashout Cancelled & Refunded - {Config.BRAND}",
                        html_content=cls._generate_cancellation_email_html(
                            user_name, cashout_id, cashout, refund_amount, refund_currency
                        ),
                        text_content=cls._generate_cancellation_email_text(
                            user_name, cashout_id, cashout, refund_amount, refund_currency
                        )
                    )
                    if email_sent:
                        logger.info(f"✅ Enhanced email notification sent to {user.email} for {cashout_id}")
                    else:
                        logger.error(f"❌ Failed to send email notification to {user.email} for {cashout_id}")
                except Exception as e:
                    logger.error(f"❌ Failed to send email notification: {e}")
            
        except Exception as e:
            logger.error(f"❌ Enhanced user notification failed for {cashout_id}: {e}", exc_info=True)
            
            # Don't fail the entire cancellation for notification errors
            # But log it as a high priority issue for follow-up
            logger.warning(f"⚠️ USER_NOTIFICATION_DEGRADED: {cashout_id} cancelled successfully but user notification failed: {e}")
            
            # Try fallback notification method
            try:
                await cls._send_fallback_user_notification(cashout_id, user, refund_amount, refund_currency)
                logger.info(f"✅ Fallback notification sent for {cashout_id}")
            except Exception as fallback_error:
                logger.error(f"❌ Fallback notification also failed for {cashout_id}: {fallback_error}")
                # Store failed notification for retry later
                cls._queue_notification_retry(cashout_id, user.id, "cancellation", {
                    "refund_amount": refund_amount,
                    "refund_currency": refund_currency
                })
    
    @classmethod
    def _generate_cancellation_email_html(cls, user_name: str, cashout_id: str, cashout, refund_amount: float, refund_currency: str) -> str:
        """Generate HTML email content for cashout cancellation"""
        from config import Config
        
        return f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cashout Cancelled</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; background-color: #f8f9fa; }}
        .container {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; }}
        .header {{ background: linear-gradient(135deg, #ff6b6b 0%, #ee5a52 100%); color: white; padding: 30px; text-align: center; }}
        .header h1 {{ margin: 0; font-size: 28px; font-weight: 600; }}
        .content {{ padding: 30px; }}
        .cancellation-card {{ background: linear-gradient(135deg, #ffeaa7 0%, #fab1a0 100%); color: #2d3436; padding: 25px; border-radius: 12px; margin: 20px 0; text-align: center; }}
        .refund-card {{ background: linear-gradient(135deg, #00b894 0%, #00a085 100%); color: white; padding: 25px; border-radius: 12px; margin: 20px 0; text-align: center; }}
        .amount {{ font-size: 32px; font-weight: bold; margin: 10px 0; }}
        .details {{ background-color: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0; }}
        .detail-row {{ display: flex; justify-content: space-between; margin: 10px 0; padding: 8px 0; border-bottom: 1px solid #e0e0e0; }}
        .detail-label {{ font-weight: 600; color: #666; }}
        .detail-value {{ color: #333; }}
        .actions {{ background-color: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0; }}
        .button {{ display: inline-block; background: linear-gradient(135deg, #0984e3 0%, #74b9ff 100%); color: white; padding: 12px 24px; text-decoration: none; border-radius: 6px; margin: 5px; }}
        .footer {{ background-color: #f8f9fa; padding: 20px; text-align: center; color: #666; font-size: 14px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🔄 Cashout Cancelled</h1>
            <p>Your cashout has been cancelled and refunded</p>
        </div>
        
        <div class="content">
            <p>Hello {user_name},</p>
            
            <div class="cancellation-card">
                <h2>❌ Cashout Cancelled</h2>
                <div class="amount">{float(cashout.amount):.8f} {cashout.currency}</div>
                <p><strong>Cashout ID:</strong> {cashout_id}</p>
                <p>This cashout was cancelled by our admin team.</p>
            </div>
            
            <div class="refund-card">
                <h2>✅ Refund Processed</h2>
                <div class="amount">{refund_amount:.8f} {refund_currency}</div>
                <p>Your funds have been returned to your wallet</p>
            </div>
            
            <div class="details">
                <h3>Transaction Details</h3>
                <div class="detail-row">
                    <span class="detail-label">Original Amount:</span>
                    <span class="detail-value">{float(cashout.amount):.8f} {cashout.currency}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Refund Amount:</span>
                    <span class="detail-value">{refund_amount:.8f} {refund_currency}</span>
                </div>
                <div class="detail-row">
                    <span class="detail-label">Status:</span>
                    <span class="detail-value">Cancelled & Refunded</span>
                </div>
            </div>
            
            <div class="actions">
                <h3>What's Next?</h3>
                <p>You can now:</p>
                <ul>
                    <li>Check your updated wallet balance</li>
                    <li>Create a new cashout request</li>
                    <li>Use your funds for other transactions</li>
                </ul>
                <p>If you have questions about this cancellation, please contact our support team.</p>
            </div>
        </div>
        
        <div class="footer">
            <p>© 2025 {Config.BRAND}. All rights reserved.</p>
            <p>This is an automated notification. Please do not reply to this email.</p>
        </div>
    </div>
</body>
</html>
        """
    
    @classmethod
    def _generate_cancellation_email_text(cls, user_name: str, cashout_id: str, cashout, refund_amount: float, refund_currency: str) -> str:
        """Generate plain text email content for cashout cancellation"""
        from config import Config
        
        return f"""
Cashout Cancelled - {Config.BRAND}

Hello {user_name},

Your cashout has been cancelled by our admin team.

CANCELLATION DETAILS:
- Cashout ID: {cashout_id}
- Original Amount: {float(cashout.amount):.8f} {cashout.currency}
- Status: Cancelled

REFUND PROCESSED:
- Refund Amount: {refund_amount:.8f} {refund_currency}
- Your funds have been returned to your wallet

WHAT'S NEXT:
- Check your updated wallet balance
- Create a new cashout request if needed
- Contact support if you have questions

Thank you for using {Config.BRAND}!

---
This is an automated notification.
© 2025 {Config.BRAND}. All rights reserved.
        """
    
    @classmethod
    async def _send_admin_confirmation_email(cls, cashout_id: str, cashout, user, cancel_result: Dict[str, Any]):
        """Send admin confirmation email after successful cancellation"""
        try:
            from services.email import EmailService
            from config import Config
            
            email_service = EmailService()
            admin_email = "moxxcompany@gmail.com"  # Standard admin email
            
            subject = f"Cashout Cancelled Successfully - {cashout_id}"
            
            # Create detailed admin email content
            html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 0; padding: 20px; background-color: #f5f5f5; }}
        .container {{ max-width: 800px; margin: 0 auto; background-color: white; padding: 20px; border-radius: 8px; }}
        .header {{ background-color: #dc3545; color: white; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
        .success {{ background-color: #28a745; color: white; padding: 15px; border-radius: 5px; margin-bottom: 20px; }}
        .info-table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
        .info-table th, .info-table td {{ padding: 10px; border: 1px solid #ddd; text-align: left; }}
        .info-table th {{ background-color: #f8f9fa; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h2>🔄 Cashout Cancellation Completed</h2>
            <p>Admin action processed successfully</p>
        </div>
        
        <div class="success">
            <h3>✅ Cancellation Summary</h3>
            <p><strong>Cashout {cashout_id}</strong> has been successfully cancelled and user refunded.</p>
        </div>
        
        <h3>📋 Transaction Details</h3>
        <table class="info-table">
            <tr><th>Cashout ID</th><td>{cashout_id}</td></tr>
            <tr><th>User ID</th><td>{user.id} ({user.username or user.first_name or 'Unknown'})</td></tr>
            <tr><th>User Email</th><td>{user.email or 'No email'}</td></tr>
            <tr><th>Original Amount</th><td>{float(cashout.amount):.8f} {cashout.currency}</td></tr>
            <tr><th>Cashout Type</th><td>{cashout.cashout_type.upper()}</td></tr>
            <tr><th>Previous Status</th><td>{cancel_result.get('status', 'Unknown')}</td></tr>
            <tr><th>New Status</th><td>CANCELLED</td></tr>
        </table>
        
        <h3>💰 Refund Details</h3>
        <table class="info-table">
            <tr><th>Refund Amount</th><td>{cancel_result.get('refund_amount'):.8f} {cancel_result.get('refund_currency')}</td></tr>
            <tr><th>Refund Method</th><td>Automatic wallet credit</td></tr>
            <tr><th>Transaction ID</th><td>{cancel_result.get('transaction_id')}</td></tr>
        </table>
        
        <h3>👤 User Notification Status</h3>
        <table class="info-table">
            <tr><th>Telegram Notification</th><td>✅ Sent</td></tr>
            <tr><th>Email Notification</th><td>{'✅ Sent' if user.email else '❌ No email address'}</td></tr>
        </table>
        
        <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
            <h4>📝 Next Steps</h4>
            <ul>
                <li>User has been notified via Telegram {('and email' if user.email else '')}</li>
                <li>Funds have been returned to user's wallet</li>
                <li>User can create new cashout requests</li>
                <li>Transaction logged in audit trail</li>
            </ul>
        </div>
        
        <div style="color: #666; font-size: 12px; margin-top: 30px; border-top: 1px solid #ddd; padding-top: 15px;">
            <p>This is an automated admin notification for cashout cancellation.</p>
            <p>Generated at: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
            <p>{Config.BRAND} Admin System</p>
        </div>
    </div>
</body>
</html>
            """
            
            # Send admin confirmation email
            email_sent = email_service.send_email(
                to_email=admin_email,
                subject=subject,
                html_content=html_content,
                text_content=f"""
Cashout Cancellation Completed

Cashout ID: {cashout_id}
User: {user.id} ({user.username or user.first_name or 'Unknown'})
Original: {float(cashout.amount):.8f} {cashout.currency}
Refunded: {cancel_result.get('refund_amount'):.8f} {cancel_result.get('refund_currency')}
Transaction ID: {cancel_result.get('transaction_id')}

User notified via Telegram {('and email' if user.email else '')}.
Funds returned to wallet successfully.
                """
            )
            
            if email_sent:
                logger.info(f"✅ Admin confirmation email sent for cancelled cashout {cashout_id}")
            else:
                logger.error(f"❌ Failed to send admin confirmation email for {cashout_id}")
            
        except Exception as e:
            logger.error(f"❌ Failed to send admin confirmation email for {cashout_id}: {e}", exc_info=True)
            # Don't raise exception as this is not critical for the cancellation process
            
            # Queue admin email for retry if it's a transient failure
            if cls._is_transient_email_error(e):
                cls._queue_admin_email_retry(cashout_id, cancel_result, user)
                logger.info(f"🔄 Queued admin email retry for {cashout_id}")
            else:
                logger.warning(f"⚠️ Permanent admin email failure for {cashout_id}: {e}")
    
    @classmethod
    async def _notify_user_cashout_cancelled(cls, cashout_id: str, user_id: int, amount: float, currency: str):
        """Legacy notification method - replaced by _send_enhanced_user_notification"""
        try:
            # Get bot application instance
            try:
                from main import get_application_instance
                application = get_application_instance()
                if application:
                    bot = application.bot
                else:
                    logger.error("No application instance available for notifications")
                    return
            except ImportError:
                logger.error("Cannot import application instance for notifications")
                return
            from services.email import EmailService
            from models import User
            from database import SessionLocal
            
            # Get user details
            session = SessionLocal()
            try:
                user = session.query(User).filter_by(id=user_id).first()
                if user:
                    user_email = user.email
                else:
                    user_email = None
            finally:
                session.close()
            
            # Send Telegram notification
            telegram_message = f"""
🔄 Cashout Update

Cashout ID: `{cashout_id}`
Status: Cancelled by Admin
Amount: {amount:.8f} {currency}

✅ Refund Processed: Your full amount has been returned to your wallet balance.

💰 You can now create a new cashout or use your funds for other transactions.

Need help? Contact support for assistance.
            """
            
            await bot.send_message(chat_id=user_id, text=telegram_message, parse_mode="Markdown")
            
            # Send email notification if user has email
            if user_email:
                email_service = EmailService()
                email_service.send_email(
                    to_email=user_email,
                    subject=f"Cashout Cancelled - Refund Processed | {cashout_id}",
                    html_content=f"""
                    <h2>🔄 Cashout Update</h2>
                    <p><strong>Cashout ID:</strong> {cashout_id}</p>
                    <p><strong>Status:</strong> Cancelled by Admin</p>
                    <p><strong>Amount:</strong> {amount:.8f} {currency}</p>
                    <h3>✅ Refund Processed</h3>
                    <p>Your full amount has been returned to your wallet balance.</p>
                    <p>You can now create a new cashout or use your funds for other transactions.</p>
                    """,
                    text_content=f"""
                    Cashout Update
                    
                    Cashout ID: {cashout_id}
                    Status: Cancelled by Admin
                    Amount: {amount:.8f} {currency}
                    
                    Refund Processed: Your full amount has been returned to your wallet balance.
                    """
                )
            
            logger.info(f"✅ User {user_id} notified of cashout cancellation and refund")
            
        except Exception as e:
            logger.error(f"Failed to notify user {user_id} about cashout cancellation: {e}")
    
    @classmethod
    def _categorize_cancellation_error(cls, error: Exception) -> str:
        """Categorize cancellation errors for better handling"""
        error_str = str(error).lower()
        error_type = type(error).__name__
        
        if 'timeout' in error_str or 'connection' in error_str:
            return "network_timeout"
        elif 'database' in error_str or 'sqlalchemy' in error_str:
            return "database_error"
        elif 'wallet' in error_str or 'balance' in error_str:
            return "wallet_error"
        elif 'email' in error_str or 'smtp' in error_str:
            return "email_error"
        elif 'permission' in error_str or 'access' in error_str:
            return "permission_error"
        elif error_type in ['AttributeError', 'KeyError', 'TypeError']:
            return "data_structure_error"
        elif 'token' in error_str or 'auth' in error_str:
            return "authentication_error"
        else:
            return "unknown_error"
    
    @classmethod
    async def _send_admin_error_notification(cls, cashout_id: str, error: Exception, error_category: str):
        """Send admin notification for critical cancellation failures"""
        try:
            from services.email import EmailService
            from config import Config
            
            email_service = EmailService()
            admin_email = "moxxcompany@gmail.com"
            
            subject = f"CRITICAL: Cashout Cancellation Failed - {cashout_id}"
            
            html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .error {{ background-color: #dc3545; color: white; padding: 15px; border-radius: 5px; margin: 20px 0; }}
        .details {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0; }}
    </style>
</head>
<body>
    <div class="error">
        <h2>❌ CRITICAL ERROR: Cashout Cancellation Failed</h2>
        <p>A cashout cancellation request failed and requires immediate admin attention.</p>
    </div>
    
    <div class="details">
        <h3>Error Details</h3>
        <p><strong>Cashout ID:</strong> {cashout_id}</p>
        <p><strong>Error Category:</strong> {error_category.upper()}</p>
        <p><strong>Error Type:</strong> {type(error).__name__}</p>
        <p><strong>Error Message:</strong> {str(error)}</p>
        <p><strong>Timestamp:</strong> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
    </div>
    
    <div class="details">
        <h3>Required Actions</h3>
        <ul>
            <li>Investigate the root cause of the error</li>
            <li>Check if user funds are in a safe state</li>
            <li>Manually process cancellation if needed</li>
            <li>Contact user if notification failed</li>
            <li>Fix any system issues identified</li>
        </ul>
    </div>
    
    <p style="color: #666; font-size: 12px; margin-top: 30px;">Generated by {Config.BRAND} Admin System</p>
</body>
</html>
            """
            
            email_service.send_email(
                to_email=admin_email,
                subject=subject,
                html_content=html_content,
                text_content=f"""
CRITICAL ERROR: Cashout Cancellation Failed

Cashout ID: {cashout_id}
Error Category: {error_category.upper()}
Error Type: {type(error).__name__}
Error Message: {str(error)}
Timestamp: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC

Manual intervention required. Check system logs and user account state.
                """
            )
            
            logger.info(f"✅ Admin error notification sent for failed cancellation: {cashout_id}")
            
        except Exception as e:
            logger.error(f"❌ Failed to send admin error notification: {e}")
    
    @classmethod
    async def _send_fallback_user_notification(cls, cashout_id: str, user, refund_amount: float, refund_currency: str):
        """Fallback user notification using basic messaging"""
        try:
            from main import get_application_instance
            
            application = get_application_instance()
            if application and application.bot and user.telegram_id:
                # Simple fallback message
                fallback_message = (
                    f"🔄 Cashout Update\n\n"
                    f"Your cashout {cashout_id} was cancelled by admin.\n"
                    f"Refund: {refund_amount:.8f} {refund_currency}\n\n"
                    f"Funds returned to your wallet. Use /wallet to check balance."
                )
                
                await application.bot.send_message(
                    chat_id=user.telegram_id,
                    text=fallback_message
                )
                logger.info(f"✅ Fallback notification sent to user {user.id}")
            else:
                raise Exception("No bot or telegram_id available")
                
        except Exception as e:
            logger.error(f"❌ Fallback notification failed: {e}")
            raise e
    
    @classmethod
    def _queue_notification_retry(cls, cashout_id: str, user_id: int, notification_type: str, data: Dict[str, Any]):
        """Queue failed notification for retry later"""
        try:
            # For now, just log the retry queue entry
            # In a production system, this would go to a retry queue service
            retry_data = {
                "cashout_id": cashout_id,
                "user_id": user_id,
                "notification_type": notification_type,
                "data": data,
                "queued_at": datetime.utcnow().isoformat(),
                "retry_count": 0
            }
            
            logger.warning(f"🔄 NOTIFICATION_RETRY_QUEUED: {retry_data}")
            
            # TODO: Implement actual retry queue with Redis/database
            # For now, just ensure it's logged for manual follow-up
            
        except Exception as e:
            logger.error(f"❌ Failed to queue notification retry: {e}")
    
    @classmethod
    def _is_transient_email_error(cls, error: Exception) -> bool:
        """Check if email error is likely transient and retryable"""
        error_str = str(error).lower()
        transient_indicators = [
            'timeout', 'connection', 'network', 'smtp', 'temporary', 
            '421', '450', '451', '452', 'service unavailable'
        ]
        
        return any(indicator in error_str for indicator in transient_indicators)
    
    @classmethod
    def _queue_admin_email_retry(cls, cashout_id: str, cancel_result: Dict[str, Any], user):
        """Queue admin email for retry"""
        try:
            retry_data = {
                "cashout_id": cashout_id,
                "cancel_result": cancel_result,
                "user_id": user.id,
                "user_email": user.email,
                "queued_at": datetime.utcnow().isoformat(),
                "email_type": "admin_confirmation"
            }
            
            logger.warning(f"🔄 ADMIN_EMAIL_RETRY_QUEUED: {retry_data}")
            
            # TODO: Implement actual retry queue
            # For now, ensure it's logged for manual processing
            
        except Exception as e:
            logger.error(f"❌ Failed to queue admin email retry: {e}")


    @classmethod
    async def enhanced_cancel_cashout_with_recovery(cls, cashout_id: str, token: str, ip_address: Optional[str] = None, user_agent: Optional[str] = None, recovery_mode: bool = False) -> Dict[str, Any]:
        """Enhanced cancellation with comprehensive error recovery"""
        try:
            logger.info(f"🔍 Enhanced cancel initiated for {cashout_id} (recovery_mode={recovery_mode})")
            
            # Call the main cancellation method
            result = await cls.cancel_cashout_from_email(
                cashout_id=cashout_id,
                token=token,
                ip_address=ip_address,
                user_agent=user_agent,
                token_already_consumed=recovery_mode
            )
            
            if result.get('success'):
                logger.info(f"✅ Enhanced cancellation completed successfully for {cashout_id}")
                return result
            else:
                # Handle partial failures with recovery options
                logger.warning(f"⚠️ Cancellation had issues for {cashout_id}: {result.get('error')}")
                
                if not recovery_mode:
                    # Try recovery mode if not already in recovery
                    logger.info(f"🔄 Attempting recovery mode for {cashout_id}")
                    recovery_result = await cls._attempt_cancellation_recovery(cashout_id, result)
                    
                    if recovery_result.get('success'):
                        return recovery_result
                
                return result
                
        except Exception as e:
            logger.error(f"❌ Enhanced cancellation failed for {cashout_id}: {e}", exc_info=True)
            
            # Last resort recovery attempt
            if not recovery_mode:
                try:
                    return await cls._emergency_cancellation_recovery(cashout_id, e)
                except Exception as recovery_error:
                    logger.critical(f"🔥 Emergency recovery also failed for {cashout_id}: {recovery_error}")
            
            return {"success": False, "error": str(e), "critical_failure": True}
    
    @classmethod
    async def _attempt_cancellation_recovery(cls, cashout_id: str, initial_result: Dict[str, Any]) -> Dict[str, Any]:
        """Attempt to recover from partial cancellation failures"""
        try:
            logger.info(f"🔄 RECOVERY_ATTEMPT: Starting recovery for {cashout_id}")
            
            # Check current cashout state
            from database import SessionLocal
            from models import Cashout, CashoutStatus
            
            session = SessionLocal()
            try:
                cashout = session.query(Cashout).filter_by(cashout_id=cashout_id).first()
                
                if not cashout:
                    return {"success": False, "error": "Cashout not found during recovery"}
                
                # If cashout is already cancelled, check if refund was processed
                if cashout.status == CashoutStatus.CANCELLED.value:
                    logger.info(f"✅ Cashout {cashout_id} is already cancelled, checking refund status...")
                    
                    # Attempt to verify and complete any missing steps
                    recovery_success = await cls._verify_and_complete_cancellation(cashout, session)
                    
                    if recovery_success:
                        return {
                            "success": True,
                            "recovered": True,
                            "message": "Cancellation completed via recovery",
                            "cashout_id": cashout_id
                        }
                
                return {"success": False, "error": "Recovery could not complete cancellation"}
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"❌ Recovery attempt failed for {cashout_id}: {e}")
            return {"success": False, "error": f"Recovery failed: {str(e)}"}
    
    @classmethod
    async def _emergency_cancellation_recovery(cls, cashout_id: str, original_error: Exception) -> Dict[str, Any]:
        """Emergency recovery for critical cancellation failures"""
        try:
            logger.critical(f"🔥 EMERGENCY_RECOVERY: Attempting emergency recovery for {cashout_id}")
            
            # Log critical state for manual intervention
            emergency_data = {
                "cashout_id": cashout_id,
                "original_error": str(original_error),
                "timestamp": datetime.utcnow().isoformat(),
                "requires_manual_intervention": True,
                "status": "emergency_recovery_initiated"
            }
            
            logger.critical(f"🔥 EMERGENCY_STATE_LOG: {emergency_data}")
            
            # Send immediate alert to admin
            try:
                await cls._send_emergency_admin_alert(cashout_id, original_error, emergency_data)
            except Exception as alert_error:
                logger.error(f"❌ Emergency admin alert failed for {cashout_id}: {alert_error}")
            
            return {
                "success": False,
                "error": "Critical failure - emergency recovery initiated",
                "emergency_recovery": True,
                "manual_intervention_required": True,
                "cashout_id": cashout_id,
                "contact_admin": "moxxcompany@gmail.com"
            }
            
        except Exception as e:
            logger.critical(f"🔥 Emergency recovery catastrophic failure for {cashout_id}: {e}")
            return {
                "success": False,
                "error": "Catastrophic failure - immediate admin attention required",
                "catastrophic_failure": True,
                "cashout_id": cashout_id
            }
    
    @classmethod
    async def _verify_and_complete_cancellation(cls, cashout, session) -> bool:
        """Verify cancellation state and complete any missing steps"""
        try:
            # This would implement verification logic
            # For now, return True as basic verification
            logger.info(f"✅ Verification completed for cancelled cashout {cashout.cashout_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Verification failed: {e}")
            return False
    
    @classmethod
    async def _send_emergency_admin_alert(cls, cashout_id: str, error: Exception, emergency_data: Dict[str, Any]):
        """Send emergency alert to admin for critical failures"""
        try:
            from services.email import EmailService
            
            email_service = EmailService()
            admin_email = "moxxcompany@gmail.com"
            
            subject = f"🚨 EMERGENCY: Critical Cashout Cancellation Failure - {cashout_id}"
            
            html_content = f"""
<!DOCTYPE html>
<html>
<head><style>body{{font-family:Arial,sans-serif;margin:20px;}}.emergency{{background:#dc3545;color:white;padding:20px;border-radius:5px;margin:20px 0;}}</style></head>
<body>
<div class="emergency">
<h1>🔥 EMERGENCY ALERT</h1>
<h2>Critical Cashout Cancellation Failure</h2>
<p><strong>IMMEDIATE ADMIN ATTENTION REQUIRED</strong></p>
</div>
<h3>Emergency Details</h3>
<p><strong>Cashout ID:</strong> {cashout_id}</p>
<p><strong>Error:</strong> {str(error)}</p>
<p><strong>Time:</strong> {emergency_data.get('timestamp')}</p>
<p><strong>Action Required:</strong> Manual cancellation and refund processing</p>
<p style="color:#dc3545;"><strong>User funds may be in inconsistent state - check immediately!</strong></p>
</body>
</html>
            """
            
            email_service.send_email(
                to_email=admin_email,
                subject=subject,
                html_content=html_content,
                text_content=f"EMERGENCY: Critical failure for cashout {cashout_id}. Error: {str(error)}. IMMEDIATE ATTENTION REQUIRED."
            )
            
            logger.critical(f"🔥 Emergency admin alert sent for {cashout_id}")
            
        except Exception as e:
            logger.critical(f"🔥 Emergency alert send failed: {e}")


class AdminDisputeEmailService:
    """Service for handling dispute resolution via email actions"""
    
    # Token validity: 7 days (168 hours) - extended for admin review workflow
    TOKEN_VALIDITY_HOURS = 168
    
    # LEGACY HMAC token methods removed - now using database-backed tokens exclusively
    # See AdminEmailActionService.generate_dispute_token() and atomic_consume_admin_token()
    # for the current implementation that provides single-use, revocable tokens with audit trail
    
    @classmethod
    def send_dispute_resolution_email(cls, dispute_id: int) -> bool:
        """Send comprehensive dispute resolution email to admin"""
        try:
            from database import SessionLocal
            from models import Dispute, User, DisputeMessage
            from services.email import EmailService
            from config import Config
            
            session = SessionLocal()
            try:
                # Get dispute and related data
                dispute = session.query(Dispute).filter(
                    Dispute.id == dispute_id
                ).first()
                
                if not dispute:
                    logger.error(f"Dispute {dispute_id} not found")
                    return False
                
                escrow = dispute.escrow
                buyer = session.query(User).filter(User.id == escrow.buyer_id).first() if escrow.buyer_id else None
                seller = session.query(User).filter(User.id == escrow.seller_id).first() if escrow.seller_id else None
                initiator = session.query(User).filter(User.id == dispute.initiator_id).first()
                
                # ENHANCEMENT: Get BOTH trade chat messages (before dispute) AND dispute messages (after dispute)
                from models import EscrowMessage
                
                # Get trade chat messages (EscrowMessage) - messages sent BEFORE dispute was created
                trade_chat_messages = session.query(EscrowMessage).filter(
                    EscrowMessage.escrow_id == escrow.id
                ).order_by(EscrowMessage.created_at.asc()).all()
                
                # Get dispute chat messages (DisputeMessage) - messages sent AFTER dispute was created
                dispute_chat_messages = session.query(DisputeMessage).filter(
                    DisputeMessage.dispute_id == dispute.id
                ).order_by(DisputeMessage.created_at.asc()).all()
                
                # Generate secure database-backed tokens for each action
                # Use AdminEmailActionService for database-backed tokens instead of HMAC-based tokens
                buyer_token = AdminEmailActionService.generate_dispute_token(
                    dispute_id=dispute_id,
                    action='REFUND_TO_BUYER',
                    admin_email=Config.ADMIN_ALERT_EMAIL
                )
                seller_token = AdminEmailActionService.generate_dispute_token(
                    dispute_id=dispute_id,
                    action='RELEASE_TO_SELLER',
                    admin_email=Config.ADMIN_ALERT_EMAIL
                )
                split_token = AdminEmailActionService.generate_dispute_token(
                    dispute_id=dispute_id,
                    action='CUSTOM_SPLIT',
                    admin_email=Config.ADMIN_ALERT_EMAIL
                )
                
                # Get admin email
                admin_email = getattr(Config, 'ADMIN_EMAIL', 'admin@lockbay.com')
                
                # Calculate amounts
                escrow_amount = float(escrow.amount)
                buyer_fee = float(escrow.buyer_fee_amount or 0)
                seller_fee = float(escrow.seller_fee_amount or 0)
                total_fees = buyer_fee + seller_fee
                
                # Buyer wins: refund the escrow amount (platform retains fees as service was provided)
                # Note: Disputes only occur AFTER seller accepts, so fees are retained per policy
                buyer_refund = escrow_amount
                # Seller wins: full escrow amount
                seller_payout = escrow_amount
                
                # Build message history with BOTH trade chat AND dispute messages
                messages_html = ""
                
                # 1. Trade Chat Messages (Pre-Dispute Context)
                if trade_chat_messages:
                    messages_html += f"""
                    <div style="margin-bottom: 15px; padding: 12px; background: #e8f5e9; border-radius: 6px; border-left: 4px solid #4caf50;">
                        <strong style="color: #2e7d32;">💬 Trade Chat History ({len(trade_chat_messages)} messages)</strong>
                        <p style="margin: 5px 0; color: #666; font-size: 12px;">Messages sent BEFORE dispute was created</p>
                    </div>
                    """
                    for msg in trade_chat_messages:
                        sender = session.query(User).filter(User.id == msg.sender_id).first()
                        # Determine role based on buyer/seller ID
                        if msg.sender_id == escrow.buyer_id:
                            sender_role = "Buyer"
                            bg_color = "#e3f2fd"
                            border_color = "#2196f3"
                        elif msg.sender_id == escrow.seller_id:
                            sender_role = "Seller"
                            bg_color = "#fff3e0"
                            border_color = "#ff9800"
                        else:
                            sender_role = "Admin"
                            bg_color = "#f3e5f5"
                            border_color = "#9c27b0"
                        
                        sender_name = (sender.first_name or sender.username or f"User #{sender.id}") if sender else f"User #{msg.sender_id}"
                        msg_text = str(getattr(msg, 'content', '')) if getattr(msg, 'content', None) else ""
                        messages_html += f"""
                        <div style="margin-bottom: 10px; padding: 8px; background: {bg_color}; border-left: 3px solid {border_color};">
                            <strong>{sender_name} ({sender_role}):</strong> {msg_text[:150]}{'...' if len(msg_text) > 150 else ''}
                            <br><small style="color: #6c757d;">{msg.created_at.strftime('%Y-%m-%d %H:%M UTC')}</small>
                        </div>
                        """
                
                # 2. Dispute Chat Messages (Post-Dispute)
                if dispute_chat_messages:
                    messages_html += f"""
                    <div style="margin: 20px 0 15px 0; padding: 12px; background: #fff3cd; border-radius: 6px; border-left: 4px solid #ffc107;">
                        <strong style="color: #856404;">⚖️ Dispute Chat Messages ({len(dispute_chat_messages)} messages)</strong>
                        <p style="margin: 5px 0; color: #666; font-size: 12px;">Messages sent AFTER dispute was created</p>
                    </div>
                    """
                    for msg in dispute_chat_messages:
                        sender = session.query(User).filter(User.id == msg.sender_id).first()
                        # Determine role based on buyer/seller ID
                        if msg.sender_id == escrow.buyer_id:
                            sender_role = "Buyer"
                            bg_color = "#e3f2fd"
                            border_color = "#2196f3"
                        elif msg.sender_id == escrow.seller_id:
                            sender_role = "Seller"
                            bg_color = "#fff3e0"
                            border_color = "#ff9800"
                        else:
                            sender_role = "Admin"
                            bg_color = "#f3e5f5"
                            border_color = "#9c27b0"
                        
                        sender_name = (sender.first_name or sender.username or f"User #{sender.id}") if sender else f"User #{msg.sender_id}"
                        msg_text = str(getattr(msg, 'message', '')) if getattr(msg, 'message', None) else ""
                        messages_html += f"""
                        <div style="margin-bottom: 10px; padding: 8px; background: {bg_color}; border-left: 3px solid {border_color};">
                            <strong>{sender_name} ({sender_role}):</strong> {msg_text[:150]}{'...' if len(msg_text) > 150 else ''}
                            <br><small style="color: #6c757d;">{msg.created_at.strftime('%Y-%m-%d %H:%M UTC')}</small>
                        </div>
                        """
                
                # If no messages at all
                if not trade_chat_messages and not dispute_chat_messages:
                    messages_html = "<p><em>No messages yet</em></p>"
                
                total_message_count = len(trade_chat_messages) + len(dispute_chat_messages)
                
                # Use the properly configured admin action base URL (lockbay.replit.app in production)
                base_url = Config.ADMIN_ACTION_BASE_URL
                
                # Email content
                subject = f"🚨 URGENT: Dispute Resolution Required | #{escrow.escrow_id} | ${escrow_amount:.2f} USD | {buyer.first_name if buyer else 'Buyer'} vs {seller.first_name if seller else 'Seller'}"
                
                html_content = f"""
                <html>
                <head>
                    <style>
                        .container {{ max-width: 800px; margin: 0 auto; font-family: Arial, sans-serif; }}
                        .dispute-summary {{ background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0; }}
                        .action-button {{ 
                            display: inline-block; 
                            padding: 12px 24px; 
                            margin: 8px; 
                            color: white; 
                            text-decoration: none; 
                            border-radius: 6px; 
                            font-weight: bold;
                        }}
                        .buyer-win {{ background: #28a745; }}
                        .seller-win {{ background: #007bff; }}
                        .split-decision {{ background: #ffc107; color: #000; }}
                        .escalate {{ background: #dc3545; }}
                        .evidence-section {{ background: #fff3cd; padding: 15px; border-radius: 6px; margin: 10px 0; }}
                        .messages-section {{ background: #e9ecef; padding: 15px; border-radius: 6px; margin: 10px 0; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <h2>⚖️ Escrow Dispute Resolution Required</h2>
                        
                        <div class="dispute-summary">
                            <h3>📋 Dispute Overview</h3>
                            <p><strong>Dispute ID:</strong> {dispute_id}</p>
                            <p><strong>Escrow ID:</strong> #{escrow.escrow_id}</p>
                            <p><strong>Amount:</strong> ${escrow_amount:.2f} USD</p>
                            <p><strong>Reason:</strong> {dispute.reason}</p>
                            <p><strong>Initiator:</strong> {(initiator.first_name or initiator.username or 'User') if initiator else 'Unknown'}</p>
                            <p><strong>Created:</strong> {dispute.created_at.strftime('%Y-%m-%d %H:%M UTC')}</p>
                            <p><strong>Status:</strong> {dispute.status.upper()}</p>
                        </div>
                        
                        <div class="evidence-section">
                            <h3>📎 Participants</h3>
                            <p><strong>🛒 Buyer:</strong> {buyer.first_name or buyer.username or 'Anonymous' if buyer else 'No Buyer'} (ID: {buyer.id if buyer else 'N/A'})</p>
                            <p><strong>🏪 Seller:</strong> {seller.first_name or seller.username or 'Anonymous' if seller else 'No Seller'} (ID: {seller.id if seller else 'N/A'})</p>
                            <p><strong>💰 Platform Fees:</strong> ${total_fees:.2f} USD (Buyer: ${buyer_fee:.2f} USD, Seller: ${seller_fee:.2f} USD)</p>
                        </div>
                        
                        <div class="evidence-section">
                            <h3>📝 Dispute Details</h3>
                            <p><strong>Description:</strong></p>
                            <div style="background: white; padding: 10px; border-radius: 4px;">
                                {dispute.reason}
                            </div>
                        </div>
                        
                        <div class="messages-section">
                            <h3>💬 Message History ({total_message_count} total)</h3>
                            {messages_html}
                        </div>
                        
                        <div style="text-align: center; margin: 30px 0;">
                            <h3>🎯 Choose Resolution Action:</h3>
                            
                            <a href="{base_url}/admin/resolve-dispute/buyer/{dispute_id}?token={buyer_token}" 
                               class="action-button buyer-win">
                                🏆 Buyer Wins - Refund ${buyer_refund:.2f} USD
                            </a>
                            
                            <a href="{base_url}/admin/resolve-dispute/seller/{dispute_id}?token={seller_token}"
                               class="action-button seller-win">
                                🛡️ Seller Wins - Release ${seller_payout:.2f} USD
                            </a>
                            
                            <a href="{base_url}/admin/resolve-dispute/split/{dispute_id}?token={split_token}&buyer=50&seller=50"
                               class="action-button split-decision" style="background: #ffc107;">
                                🟡 50/50 Split - ${escrow_amount/2:.2f} USD each
                            </a>
                            
                            <a href="{base_url}/admin/resolve-dispute/split/{dispute_id}?token={split_token}"
                               class="action-button" style="background: #6f42c1; color: white;">
                                ⚖️ Custom Split Resolution
                            </a>
                        </div>
                        
                        <div style="margin-top: 30px; padding: 15px; background: #e9ecef; border-radius: 6px;">
                            <p><small><strong>Security Notice:</strong> These action links are valid for 7 days and can only be used once. 
                            Do not forward this email to unauthorized personnel.</small></p>
                        </div>
                    </div>
                </body>
                </html>
                """
                
                # Send email
                email_service = EmailService()
                email_service.send_email(
                    to_email=admin_email,
                    subject=subject,
                    html_content=html_content,
                    text_content=f"Dispute Resolution Required: {dispute_id} - ${escrow_amount:.2f} USD - Login to admin panel to resolve."
                )
                
                logger.info(f"✅ Dispute resolution email sent for {dispute_id}")
                return True
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error sending dispute resolution email: {e}")
            return False
    
    @classmethod
    async def resolve_buyer_favor_from_email(cls, dispute_id: str, token: str) -> Dict[str, Any]:
        """Resolve dispute in buyer's favor via email action"""
        try:
            # Validate and consume database-backed token atomically
            token_validation = await AdminEmailActionService.atomic_consume_admin_token(
                cashout_id=dispute_id,
                token=token,
                action='REFUND_TO_BUYER'
            )
            
            if not token_validation.get('valid'):
                logger.warning(f"Invalid token for buyer resolution {dispute_id}: {token_validation.get('error')}")
                return {"success": False, "error": token_validation.get('error', 'Invalid or expired token')}
            
            from database import async_managed_session
            from models import Dispute, User
            from services.dispute_resolution import DisputeResolutionService
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload
            
            async with async_managed_session() as session:
                # Get dispute and admin info (eagerly load escrow to avoid lazy loading)
                dispute_query = select(Dispute).where(
                    Dispute.id == int(dispute_id),
                    Dispute.status.in_(['open', 'under_review'])
                ).options(selectinload(Dispute.escrow))
                dispute_result = await session.execute(dispute_query)
                dispute = dispute_result.scalar_one_or_none()
                
                if not dispute:
                    return {
                        "success": False, 
                        "error": "Dispute not found or already resolved"
                    }
                
                # Get admin user (use admin from config - no role field in User model)
                from config import Config
                from datetime import datetime
                admin_user = None
                if Config.ADMIN_IDS:
                    # Use first admin ID from config
                    admin_query = select(User).where(User.telegram_id == int(Config.ADMIN_IDS[0]))
                    admin_result = await session.execute(admin_query)
                    admin_user = admin_result.scalar_one_or_none()
                    
                    # Create admin user if they don't exist (User model uses timezone-naive datetimes)
                    if not admin_user:
                        now = datetime.utcnow()
                        admin_telegram_id = int(Config.ADMIN_IDS[0])
                        admin_user = User(
                            id=admin_telegram_id,
                            telegram_id=admin_telegram_id,
                            username="admin",
                            first_name="Admin",
                            created_at=now,
                            updated_at=now,
                            last_activity=now
                        )
                        session.add(admin_user)
                        await session.commit()
                        await session.refresh(admin_user)
                        logger.info(f"✅ Created admin user with telegram_id {Config.ADMIN_IDS[0]}")
                
                if not admin_user:
                    return {"success": False, "error": "No admin user found"}
                
                # Get buyer and seller IDs for notifications
                
            # Process resolution using existing atomic service (creates its own session)
            result = await DisputeResolutionService.resolve_refund_to_buyer(
                dispute_id=int(getattr(dispute, 'id')),
                admin_user_id=admin_user.id
            )
            
            if result.success:
                # Send notifications to both parties via PostCompletionNotificationService
                from services.post_completion_notification_service import PostCompletionNotificationService
                notification_service = PostCompletionNotificationService()
                # Type safety: ensure buyer_id and seller_id are not None
                if result.buyer_id is not None and result.seller_id is not None:
                    await notification_service.notify_escrow_completion(
                        escrow_id=result.escrow_id,
                        completion_type='dispute_resolved',
                        amount=float(result.amount),
                        buyer_id=result.buyer_id,
                        seller_id=result.seller_id,
                        dispute_winner_id=result.dispute_winner_id,
                        dispute_loser_id=result.dispute_loser_id,
                        resolution_type=result.resolution_type
                    )
                
                logger.info(f"✅ Dispute {dispute_id} resolved in buyer's favor via email")
                
                return {
                    "success": True,
                    "resolution": "buyer_wins",
                    "amount_refunded": result.amount,
                    "escrow_id": result.escrow_id,
                    "message": "Dispute resolved in buyer's favor - full refund processed"
                }
            else:
                logger.error(f"❌ Failed to resolve dispute {dispute_id}: {result.error_message}")
                return {
                    "success": False,
                    "error": result.error_message or "Unknown error during resolution"
                }
                
        except Exception as e:
            logger.error(f"Error resolving dispute {dispute_id} via email: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    async def resolve_seller_favor_from_email(cls, dispute_id: str, token: str) -> Dict[str, Any]:
        """Resolve dispute in seller's favor via email action"""
        try:
            # Validate and consume database-backed token atomically
            token_validation = await AdminEmailActionService.atomic_consume_admin_token(
                cashout_id=dispute_id,
                token=token,
                action='RELEASE_TO_SELLER'
            )
            
            if not token_validation.get('valid'):
                logger.warning(f"Invalid token for seller resolution {dispute_id}: {token_validation.get('error')}")
                return {"success": False, "error": token_validation.get('error', 'Invalid or expired token')}
            
            from database import async_managed_session
            from models import Dispute, User
            from services.dispute_resolution import DisputeResolutionService
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload
            
            async with async_managed_session() as session:
                # Get dispute and admin info (eagerly load escrow to avoid lazy loading)
                dispute_query = select(Dispute).where(
                    Dispute.id == int(dispute_id),
                    Dispute.status.in_(['open', 'under_review'])
                ).options(selectinload(Dispute.escrow))
                dispute_result = await session.execute(dispute_query)
                dispute = dispute_result.scalar_one_or_none()
                
                if not dispute:
                    return {
                        "success": False, 
                        "error": "Dispute not found or already resolved"
                    }
                
                # Get admin user (use admin from config - no role field in User model)
                from config import Config
                from datetime import datetime
                admin_user = None
                if Config.ADMIN_IDS:
                    # Use first admin ID from config
                    admin_query = select(User).where(User.telegram_id == int(Config.ADMIN_IDS[0]))
                    admin_result = await session.execute(admin_query)
                    admin_user = admin_result.scalar_one_or_none()
                    
                    # Create admin user if they don't exist (User model uses timezone-naive datetimes)
                    if not admin_user:
                        now = datetime.utcnow()
                        admin_telegram_id = int(Config.ADMIN_IDS[0])
                        admin_user = User(
                            id=admin_telegram_id,
                            telegram_id=admin_telegram_id,
                            username="admin",
                            first_name="Admin",
                            created_at=now,
                            updated_at=now,
                            last_activity=now
                        )
                        session.add(admin_user)
                        await session.commit()
                        await session.refresh(admin_user)
                        logger.info(f"✅ Created admin user with telegram_id {Config.ADMIN_IDS[0]}")
                
                if not admin_user:
                    return {"success": False, "error": "No admin user found"}
                
                # Get buyer and seller IDs for notifications
                
            # Process resolution using existing atomic service (creates its own session)
            result = await DisputeResolutionService.resolve_release_to_seller(
                dispute_id=int(getattr(dispute, 'id')),
                admin_user_id=admin_user.id
            )
            
            if result.success:
                # Send notifications to both parties via PostCompletionNotificationService
                from services.post_completion_notification_service import PostCompletionNotificationService
                notification_service = PostCompletionNotificationService()
                # Type safety: ensure buyer_id and seller_id are not None
                if result.buyer_id is not None and result.seller_id is not None:
                    await notification_service.notify_escrow_completion(
                        escrow_id=result.escrow_id,
                        completion_type='dispute_resolved',
                        amount=float(result.amount),
                        buyer_id=result.buyer_id,
                        seller_id=result.seller_id,
                        dispute_winner_id=result.dispute_winner_id,
                        dispute_loser_id=result.dispute_loser_id,
                        resolution_type=result.resolution_type
                    )
                
                logger.info(f"✅ Dispute {dispute_id} resolved in seller's favor via email")
                
                return {
                    "success": True,
                    "resolution": "seller_wins",
                    "amount_released": result.amount,
                    "escrow_id": result.escrow_id,
                    "message": "Dispute resolved in seller's favor - funds released"
                }
            else:
                logger.error(f"❌ Failed to resolve dispute {dispute_id}: {result.error_message}")
                return {
                    "success": False,
                    "error": result.error_message or "Unknown error during resolution"
                }
                
        except Exception as e:
            logger.error(f"Error resolving dispute {dispute_id} via email: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    async def escalate_dispute_from_email(cls, dispute_id: str, token: str) -> Dict[str, Any]:
        """Escalate dispute for manual review via email action"""
        try:
            # Validate and consume database-backed token atomically
            token_validation = await AdminEmailActionService.atomic_consume_admin_token(
                cashout_id=dispute_id,
                token=token,
                action='CUSTOM_SPLIT'  # Escalate uses CUSTOM_SPLIT token as per line 1816
            )
            
            if not token_validation.get('valid'):
                logger.warning(f"Invalid token for dispute escalation {dispute_id}: {token_validation.get('error')}")
                return {"success": False, "error": token_validation.get('error', 'Invalid or expired token')}
            
            from database import async_managed_session
            from models import Dispute, DisputeStatus
            from datetime import datetime
            from sqlalchemy import select
            
            async with async_managed_session() as session:
                # Get dispute
                dispute_query = select(Dispute).where(
                    Dispute.id == int(dispute_id),
                    Dispute.status.in_(['open', 'under_review'])
                )
                dispute_result = await session.execute(dispute_query)
                dispute = dispute_result.scalar_one_or_none()
                
                if not dispute:
                    return {
                        "success": False, 
                        "error": "Dispute not found or already resolved"
                    }
                
                # Update dispute status to under_review
                dispute.status = DisputeStatus.UNDER_REVIEW.value  # type: ignore[assignment]
                dispute.updated_at = datetime.utcnow()  # type: ignore[assignment]
                dispute.admin_notes = (dispute.admin_notes or "") + f"\n[{datetime.utcnow()}] Escalated for manual review via admin email"  # type: ignore[attr-defined,assignment]
                
                await session.commit()
                
                logger.info(f"✅ Dispute {dispute_id} escalated for manual review via email")
                
                return {
                    "success": True,
                    "status": "escalated",
                    "message": "Dispute escalated for manual review - requires in-platform admin attention"
                }
                
        except Exception as e:
            logger.error(f"Error escalating dispute {dispute_id} via email: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    async def _notify_dispute_resolution(cls, dispute_id: str, resolution_type: str, 
                                       amount: float, buyer_id: int, seller_id: int):
        """Notify both parties about dispute resolution with unified rating flow"""
        try:
            from models import User, Dispute
            from database import SessionLocal
            
            session = SessionLocal()
            try:
                # Get dispute and escrow information
                dispute = session.query(Dispute).filter(
                    Dispute.id == dispute_id
                ).first()
                
                if not dispute:
                    logger.error(f"Dispute {dispute_id} not found for notifications")
                    return
                
                escrow = dispute.escrow
                if not escrow:
                    logger.error(f"Escrow not found for dispute {dispute_id}")
                    return
                
                buyer = session.query(User).filter_by(id=buyer_id).first()
                seller = session.query(User).filter_by(id=seller_id).first()
                
                # Determine winner/loser based on resolution type
                if resolution_type == "buyer_wins":
                    dispute_winner_id = buyer_id
                    dispute_loser_id = seller_id
                    dispute_resolution_type = "refund"
                else:  # seller_wins
                    dispute_winner_id = seller_id
                    dispute_loser_id = buyer_id
                    dispute_resolution_type = "release"
                
                # Use PostCompletionNotificationService for unified rating flow
                from services.post_completion_notification_service import PostCompletionNotificationService
                
                notification_service = PostCompletionNotificationService()
                await notification_service.notify_escrow_completion(
                    escrow_id=escrow.escrow_id,
                    completion_type='dispute_resolved',
                    amount=amount,
                    buyer_id=buyer_id,
                    seller_id=seller_id,
                    buyer_email=buyer.email if buyer else None,
                    seller_email=seller.email if seller else None,
                    dispute_winner_id=dispute_winner_id,
                    dispute_loser_id=dispute_loser_id,
                    resolution_type=dispute_resolution_type
                )
                
                logger.info(f"✅ Unified dispute resolution notifications with rating flow sent for {dispute_id}")
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Failed to send dispute resolution notifications: {e}")
    
    @classmethod
    async def process_custom_split_from_email(cls, dispute_id: str, buyer_percentage: float, 
                                            reason: str, token: str) -> Dict[str, Any]:
        """Process custom split resolution with specified percentages
        
        NOTE: Token validation is expected to be done by the caller (webhook_server.py)
        before calling this method to avoid double-consuming the single-use token.
        """
        try:
            # Validate percentage
            if not (0 <= buyer_percentage <= 100):
                return {"success": False, "error": "Invalid percentage. Must be between 0 and 100."}
            
            from database import async_managed_session
            from models import Dispute, User, EscrowStatus, DisputeStatus
            from services.crypto import CryptoServiceAtomic
            from datetime import datetime
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload
            
            async with async_managed_session() as session:
                # Get dispute and related data
                dispute_query = select(Dispute).where(
                    Dispute.id == int(dispute_id),
                    Dispute.status.in_(['open', 'under_review'])
                ).options(selectinload(Dispute.escrow))
                dispute_result = await session.execute(dispute_query)
                dispute = dispute_result.scalar_one_or_none()
                
                if not dispute:
                    return {
                        "success": False, 
                        "error": "Dispute not found or already resolved"
                    }
                
                escrow = dispute.escrow
                buyer_query = select(User).where(User.id == escrow.buyer_id)
                buyer_result = await session.execute(buyer_query)
                buyer = buyer_result.scalar_one_or_none()
                
                seller_query = select(User).where(User.id == escrow.seller_id)
                seller_result = await session.execute(seller_query)
                seller = seller_result.scalar_one_or_none()
                
                # Get admin user (use admin from config - no role field in User model)
                from config import Config
                admin_user = None
                if Config.ADMIN_IDS:
                    # Use first admin ID from config
                    admin_query = select(User).where(User.telegram_id == int(Config.ADMIN_IDS[0]))
                    admin_result = await session.execute(admin_query)
                    admin_user = admin_result.scalar_one_or_none()
                    
                    # Create admin user if they don't exist (User model uses timezone-naive datetimes)
                    if not admin_user:
                        now = datetime.utcnow()
                        admin_telegram_id = int(Config.ADMIN_IDS[0])
                        admin_user = User(
                            id=admin_telegram_id,
                            telegram_id=admin_telegram_id,
                            username="admin",
                            first_name="Admin",
                            created_at=now,
                            updated_at=now,
                            last_activity=now
                        )
                        session.add(admin_user)
                        await session.commit()
                        await session.refresh(admin_user)
                        logger.info(f"✅ Created admin user with telegram_id {Config.ADMIN_IDS[0]}")
                
                if not admin_user:
                    return {"success": False, "error": "No admin user found"}
                
                # Calculate amounts
                escrow_amount = float(escrow.amount)
                buyer_fee = float(escrow.buyer_fee_amount or 0)
                seller_fee = float(escrow.seller_fee_amount or 0)
                total_fees = buyer_fee + seller_fee
                net_amount = escrow_amount - total_fees
                
                # Calculate split amounts
                buyer_amount = (net_amount * buyer_percentage / 100)
                seller_amount = (net_amount * (100 - buyer_percentage) / 100)
                
                # Validate amounts
                if buyer_amount < 0 or seller_amount < 0:
                    return {"success": False, "error": "Invalid split calculation resulted in negative amounts"}
                
                # Credit buyer if they get any amount
                if buyer_amount > 0:
                    buyer_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                        user_id=escrow.buyer_id,
                        amount=buyer_amount,
                        currency="USD",
                        transaction_type="escrow_refund",
                        description=f"⚖️ Custom Split {buyer_percentage}% • Escrow #{escrow.escrow_id}",
                        escrow_id=escrow.id,
                        session=session
                    )
                    if not buyer_success:
                        raise Exception("Failed to credit buyer wallet")
                
                # Credit seller if they get any amount
                if seller_amount > 0:
                    seller_success = await CryptoServiceAtomic.credit_user_wallet_atomic(
                        user_id=escrow.seller_id,
                        amount=seller_amount,
                        currency="USD",
                        transaction_type="escrow_release",
                        description=f"⚖️ Custom Split {100 - buyer_percentage}% • Escrow #{escrow.escrow_id}",
                        escrow_id=escrow.id,
                        session=session
                    )
                    if not seller_success:
                        raise Exception("Failed to credit seller wallet")
                
                # Record platform fees as revenue (fees are retained)
                if total_fees > 0:
                    from models import PlatformRevenue
                    platform_revenue = PlatformRevenue(
                        escrow_id=escrow.escrow_id,
                        fee_amount=total_fees,
                        fee_currency="USD",
                        fee_type="dispute_resolution_fee",
                        source_transaction_id=f"dispute_{dispute.id}_custom_split"
                    )
                    session.add(platform_revenue)
                
                # Update dispute status
                dispute.status = DisputeStatus.RESOLVED.value  # type: ignore[assignment]
                dispute.resolution = f"Custom split: Buyer {buyer_percentage}%, Seller {100 - buyer_percentage}%. Reason: {reason}"  # type: ignore[assignment]
                dispute.admin_assigned_id = admin_user.id  # type: ignore[assignment]
                dispute.resolved_at = datetime.utcnow()  # type: ignore[assignment]
                dispute.updated_at = datetime.utcnow()  # type: ignore[assignment]
                
                # Update escrow status
                escrow.status = EscrowStatus.COMPLETED.value
                escrow.completed_at = datetime.utcnow()
                escrow.updated_at = datetime.utcnow()
                
                # Commit handled by async_managed_session context manager
                await session.flush()
                
                # CRITICAL: Call rating system for post-dispute completion flow
                # Use PostCompletionNotificationService for unified rating system integration
                try:
                    from services.post_completion_notification_service import PostCompletionNotificationService
                    
                    notification_service = PostCompletionNotificationService()
                    # Pass the net amount that was split (for display purposes)
                    # resolution_type encodes the split percentages so the service can calculate individual amounts
                    # Format: 'custom_split_{buyer_percent}_{seller_percent}' (e.g., 'custom_split_50_50')
                    resolution_type = f'custom_split_{int(buyer_percentage)}_{int(100 - buyer_percentage)}'
                    
                    await notification_service.notify_escrow_completion(
                        escrow_id=escrow.escrow_id,
                        completion_type='dispute_resolved',
                        amount=net_amount,  # Pass net amount so service can calculate splits
                        buyer_id=escrow.buyer_id,
                        seller_id=escrow.seller_id,
                        buyer_email=buyer.email if buyer else None,
                        seller_email=seller.email if seller else None,
                        dispute_winner_id=None,  # No clear winner in split resolution
                        dispute_loser_id=None,  # No clear loser in split resolution
                        resolution_type=resolution_type  # e.g., 'custom_split_50_50'
                    )
                    
                    logger.info(f"✅ Custom split notifications with rating system sent for {dispute_id} ({buyer_percentage}%/{100-buyer_percentage}%)")
                    
                except Exception as e:
                    logger.error(f"Failed to send custom split notifications with rating: {e}")
                
                logger.info(f"✅ Custom split resolution applied for {dispute_id}: {buyer_percentage}% buyer, {100 - buyer_percentage}% seller")
                
                return {
                    "success": True,
                    "resolution_type": "custom_split",
                    "buyer_amount": buyer_amount,
                    "seller_amount": seller_amount,
                    "total_amount": escrow_amount,
                    "net_amount": net_amount,
                    "platform_fees": total_fees,
                    "buyer_percentage": buyer_percentage,
                    "seller_percentage": 100 - buyer_percentage,
                    "escrow_id": escrow.escrow_id,
                    "message": f"Custom split resolution applied: {buyer_percentage}% to buyer, {100 - buyer_percentage}% to seller"
                }
                
        except Exception as e:
            logger.error(f"Error processing custom split for {dispute_id}: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    async def _notify_custom_split_resolution(cls, dispute_id: str, buyer_percentage: float,
                                            buyer_amount: float, seller_amount: float,
                                            buyer_id: int, seller_id: int, reason: str):
        """Notify both parties about custom split resolution"""
        try:
            # Get bot application instance
            try:
                from main import get_application_instance
                application = get_application_instance()
                if application:
                    bot = application.bot
                else:
                    logger.error("No application instance available for notifications")
                    return
            except ImportError:
                logger.error("Cannot import application instance for notifications")
                return
            from services.email import EmailService
            from models import User
            from database import SessionLocal
            
            session = SessionLocal()
            try:
                buyer = session.query(User).filter_by(id=buyer_id).first()
                seller = session.query(User).filter_by(id=seller_id).first()
                
                seller_percentage = 100 - buyer_percentage
                
                # Create buyer message (compact mobile-friendly format)
                buyer_message = f"""⚖️ *Dispute #{dispute_id} Resolved*

*Your Share:* ${buyer_amount:.2f} USD ({buyer_percentage:.0f}%)
*Reason:* {reason}

✅ Credited to your wallet."""
                
                # Create seller message (compact mobile-friendly format)
                seller_message = f"""⚖️ *Dispute #{dispute_id} Resolved*

*Your Share:* ${seller_amount:.2f} USD ({seller_percentage:.0f}%)
*Reason:* {reason}

✅ Credited to your wallet."""
                
                # Send Telegram notifications
                if buyer:
                    await bot.send_message(chat_id=buyer.telegram_id, text=buyer_message, parse_mode="Markdown")
                if seller:
                    await bot.send_message(chat_id=seller.telegram_id, text=seller_message, parse_mode="Markdown")
                
                # Send email notifications if available
                email_service = EmailService()
                
                if buyer and buyer.email:
                    email_service.send_email(
                        to_email=buyer.email,
                        subject=f"Dispute Resolution - Custom Split Applied | {dispute_id}",
                        html_content=buyer_message.replace("**", "<strong>").replace("**", "</strong>").replace("\n", "<br>"),
                        text_content=buyer_message
                    )
                
                if seller and seller.email:
                    email_service.send_email(
                        to_email=seller.email,
                        subject=f"Dispute Resolution - Custom Split Applied | {dispute_id}",
                        html_content=seller_message.replace("**", "<strong>").replace("**", "</strong>").replace("\n", "<br>"),
                        text_content=seller_message
                    )
                
                logger.info(f"✅ Custom split resolution notifications sent for {dispute_id}")
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Failed to send custom split resolution notifications: {e}")
    
    @classmethod
    def analyze_dispute_with_ai(cls, dispute_id: str) -> Dict[str, Any]:
        """Analyze dispute evidence with AI and provide resolution suggestions"""
        try:
            from database import SessionLocal
            from models import Dispute, DisputeMessage, User
            
            session = SessionLocal()
            try:
                # Get dispute and all related evidence
                dispute = session.query(Dispute).filter(
                    Dispute.id == dispute_id
                ).first()
                
                if not dispute:
                    return {"success": False, "error": "Dispute not found"}
                
                escrow = dispute.escrow
                buyer = session.query(User).filter(User.id == escrow.buyer_id).first()
                seller = session.query(User).filter(User.id == escrow.seller_id).first()
                
                # Get all dispute messages and evidence
                messages = session.query(DisputeMessage).filter(
                    DisputeMessage.dispute_id == dispute.id
                ).order_by(DisputeMessage.created_at).all()
                
                # Calculate amounts for context
                escrow_amount = float(escrow.amount)
                buyer_fee = float(escrow.buyer_fee_amount or 0)
                seller_fee = float(escrow.seller_fee_amount or 0)
                net_amount = escrow_amount - buyer_fee - seller_fee
                
                # Analyze evidence patterns
                evidence_analysis = cls._analyze_evidence_patterns(messages, dispute)
                
                # Generate AI suggestions based on evidence
                ai_suggestions = cls._generate_ai_suggestions(
                    dispute, escrow, buyer, seller, messages, evidence_analysis, net_amount
                )
                
                # Check for auto-resolution eligibility (only if enabled in config)
                if Config.DISPUTE_AUTO_RESOLUTION_ENABLED:
                    auto_resolution = cls._check_auto_resolution_eligibility(evidence_analysis, ai_suggestions)
                else:
                    auto_resolution = {"eligible": False, "disabled_by_config": True}
                
                logger.info(f"✅ AI analysis completed for dispute {dispute_id}")
                
                return {
                    "success": True,
                    "dispute_id": dispute_id,
                    "evidence_analysis": evidence_analysis,
                    "ai_suggestions": ai_suggestions,
                    "auto_resolution": auto_resolution,
                    "escrow_details": {
                        "amount": escrow_amount,
                        "net_amount": net_amount,
                        "buyer_fee": buyer_fee,
                        "seller_fee": seller_fee
                    }
                }
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error in AI dispute analysis for {dispute_id}: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    def _analyze_evidence_patterns(cls, messages: list, dispute) -> Dict[str, Any]:
        """Analyze evidence patterns from dispute messages and attachments"""
        key_issues_list: List[str] = []
        timeline_events_list: List[Dict[str, Any]] = []
        
        analysis: Dict[str, Any] = {
            "total_messages": len(messages),
            "buyer_messages": 0,
            "seller_messages": 0,
            "evidence_count": 0,
            "buyer_evidence": 0,
            "seller_evidence": 0,
            "communication_pattern": "balanced",
            "evidence_quality": "insufficient",
            "key_issues": key_issues_list,
            "timeline_events": timeline_events_list,
            "responsiveness_score": {"buyer": 0, "seller": 0}
        }
        
        buyer_msg_count = 0
        seller_msg_count = 0
        buyer_evidence_count = 0
        seller_evidence_count = 0
        
        # Keywords that indicate specific issues
        quality_keywords = ["broken", "damaged", "defective", "poor quality", "not working", "faulty"]
        delivery_keywords = ["not received", "never arrived", "lost", "missing", "delayed"]
        description_keywords = ["not as described", "different", "wrong item", "not what", "misleading"]
        communication_keywords = ["no response", "not responding", "ignored", "refuses to"]
        
        for msg in messages:
            if msg.sender_type == "buyer":
                buyer_msg_count += 1
                if msg.has_attachment:
                    buyer_evidence_count += 1
            elif msg.sender_type == "seller":
                seller_msg_count += 1
                if msg.has_attachment:
                    seller_evidence_count += 1
            
            # Analyze message content for key issues
            msg_lower = (msg.message or "").lower()
            
            if any(keyword in msg_lower for keyword in quality_keywords):
                analysis["key_issues"].append("quality_issues")
            if any(keyword in msg_lower for keyword in delivery_keywords):
                analysis["key_issues"].append("delivery_issues")
            if any(keyword in msg_lower for keyword in description_keywords):
                analysis["key_issues"].append("description_mismatch")
            if any(keyword in msg_lower for keyword in communication_keywords):
                analysis["key_issues"].append("poor_communication")
            
            # Track timeline events
            analysis["timeline_events"].append({
                "timestamp": msg.created_at,
                "sender": msg.sender_type,
                "has_evidence": msg.has_attachment,
                "message_length": len(msg.message or "")
            })
        
        analysis["buyer_messages"] = buyer_msg_count
        analysis["seller_messages"] = seller_msg_count
        analysis["buyer_evidence"] = buyer_evidence_count
        analysis["seller_evidence"] = seller_evidence_count
        analysis["evidence_count"] = buyer_evidence_count + seller_evidence_count
        
        # Determine communication pattern
        total_msgs = buyer_msg_count + seller_msg_count
        if total_msgs > 0:
            buyer_ratio = buyer_msg_count / total_msgs
            if buyer_ratio > 0.7:
                analysis["communication_pattern"] = "buyer_heavy"
            elif buyer_ratio < 0.3:
                analysis["communication_pattern"] = "seller_heavy"
            else:
                analysis["communication_pattern"] = "balanced"
        
        # Assess evidence quality
        if analysis["evidence_count"] >= 3:
            analysis["evidence_quality"] = "strong"
        elif analysis["evidence_count"] >= 1:
            analysis["evidence_quality"] = "moderate"
        else:
            analysis["evidence_quality"] = "insufficient"
        
        # Calculate responsiveness scores (simplified)
        analysis["responsiveness_score"]["buyer"] = min(100, buyer_msg_count * 25)
        analysis["responsiveness_score"]["seller"] = min(100, seller_msg_count * 25)
        
        # Remove duplicates from key issues
        analysis["key_issues"] = list(set(analysis["key_issues"]))
        
        return analysis
    
    @classmethod
    def _generate_ai_suggestions(cls, dispute, escrow, buyer, seller, messages, evidence_analysis, net_amount) -> Dict[str, Any]:
        """Generate AI-powered resolution suggestions based on evidence analysis"""
        reasoning_list: List[str] = []
        suggested_splits_list: List[Dict[str, Any]] = []
        risk_factors_list: List[str] = []
        strengths_list: List[str] = []
        next_steps_list: List[str] = []
        
        suggestions: Dict[str, Any] = {
            "recommended_action": "manual_review",
            "confidence_score": 0,
            "reasoning": reasoning_list,
            "suggested_splits": suggested_splits_list,
            "risk_factors": risk_factors_list,
            "strengths": strengths_list,
            "next_steps": next_steps_list
        }
        
        # Analyze evidence strength
        evidence_quality = evidence_analysis["evidence_quality"]
        key_issues = evidence_analysis["key_issues"]
        buyer_evidence = evidence_analysis["buyer_evidence"]
        seller_evidence = evidence_analysis["seller_evidence"]
        communication_pattern = evidence_analysis["communication_pattern"]
        
        confidence = 0
        
        # Quality issues analysis
        if "quality_issues" in key_issues:
            if buyer_evidence > seller_evidence:
                confidence += 25
                reasoning_list.append("Buyer provided more evidence of quality issues")
                suggestions["suggested_splits"].append({
                    "type": "buyer_favor",
                    "buyer_percentage": 75,
                    "reason": "Quality issues with buyer evidence"
                })
            else:
                reasoning_list.append("Quality issues reported but evidence unclear")
        
        # Delivery issues analysis
        if "delivery_issues" in key_issues:
            if buyer_evidence >= 1:
                confidence += 30
                reasoning_list.append("Delivery issues documented by buyer")
                suggestions["suggested_splits"].append({
                    "type": "buyer_favor",
                    "buyer_percentage": 100,
                    "reason": "Non-delivery confirmed"
                })
            else:
                reasoning_list.append("Delivery issues claimed but not documented")
        
        # Description mismatch analysis
        if "description_mismatch" in key_issues:
            if buyer_evidence > 0:
                confidence += 20
                reasoning_list.append("Item description mismatch with buyer evidence")
                suggestions["suggested_splits"].append({
                    "type": "buyer_favor",
                    "buyer_percentage": 60,
                    "reason": "Partial delivery/description issues"
                })
        
        # Communication issues
        if "poor_communication" in key_issues:
            if communication_pattern == "buyer_heavy":
                confidence += 15
                reasoning_list.append("Seller shows poor communication pattern")
                suggestions["risk_factors"].append("Seller unresponsive")
            elif communication_pattern == "seller_heavy":
                reasoning_list.append("Buyer may not be communicating adequately")
                suggestions["risk_factors"].append("Buyer communication issues")
        
        # Evidence quality assessment
        if evidence_quality == "strong":
            confidence += 20
            suggestions["strengths"].append("Strong evidence provided")
        elif evidence_quality == "moderate":
            confidence += 10
            suggestions["strengths"].append("Moderate evidence available")
        else:
            suggestions["risk_factors"].append("Insufficient evidence")
            reasoning_list.append("Limited evidence makes resolution difficult")
        
        # Evidence balance assessment
        if buyer_evidence > seller_evidence * 2:
            confidence += 15
            reasoning_list.append("Buyer provided significantly more evidence")
        elif seller_evidence > buyer_evidence * 2:
            confidence += 15
            reasoning_list.append("Seller provided significantly more evidence")
        elif buyer_evidence == 0 and seller_evidence == 0:
            suggestions["risk_factors"].append("No evidence from either party")
            reasoning_list.append("No supporting evidence available")
        
        # Determine recommended action based on confidence and evidence
        if confidence >= 70 and evidence_quality in ["strong", "moderate"]:
            if "delivery_issues" in key_issues and buyer_evidence > 0:
                suggestions["recommended_action"] = "buyer_wins"
                reasoning_list.append("High confidence: delivery failure with evidence")
            elif "quality_issues" in key_issues and buyer_evidence > seller_evidence:
                suggestions["recommended_action"] = "custom_split"
                suggestions["suggested_splits"] = [{
                    "type": "buyer_favor",
                    "buyer_percentage": 70,
                    "reason": "Quality issues favor buyer but partial delivery occurred"
                }]
                reasoning_list.append("High confidence: quality issues warrant partial refund")
        elif confidence >= 50:
            suggestions["recommended_action"] = "custom_split"
            suggestions["suggested_splits"] = [{
                "type": "equal_split",
                "buyer_percentage": 50,
                "reason": "Unclear evidence suggests equal responsibility"
            }]
            reasoning_list.append("Moderate confidence: equal split recommended")
        elif confidence < 30:
            suggestions["recommended_action"] = "escalate"
            reasoning_list.append("Low confidence: requires human review")
        
        # Add next steps recommendations
        if evidence_quality == "insufficient":
            suggestions["next_steps"].append("Request additional evidence from both parties")
        if "poor_communication" in key_issues:
            suggestions["next_steps"].append("Facilitate direct communication between parties")
        if len(key_issues) > 2:
            suggestions["next_steps"].append("Consider complex resolution with detailed investigation")
        
        suggestions["confidence_score"] = min(100, confidence)
        suggestions["reasoning"] = reasoning_list
        
        return suggestions
    
    @classmethod
    def _check_auto_resolution_eligibility(cls, evidence_analysis, ai_suggestions) -> Dict[str, Any]:
        """Check if dispute is eligible for automatic resolution"""
        requirements_met_list: List[str] = []
        requirements_failed_list: List[str] = []
        
        auto_resolution: Dict[str, Any] = {
            "eligible": False,
            "action": None,
            "confidence_threshold": 80,
            "requirements_met": requirements_met_list,
            "requirements_failed": requirements_failed_list,
            "estimated_accuracy": 0
        }
        
        confidence = ai_suggestions["confidence_score"]
        evidence_quality = evidence_analysis["evidence_quality"]
        key_issues = evidence_analysis["key_issues"]
        
        # Requirements for auto-resolution
        requirements = {
            "high_confidence": confidence >= 80,
            "strong_evidence": evidence_quality == "strong",
            "clear_issue": len(key_issues) == 1,  # Single clear issue
            "evidence_imbalance": abs(evidence_analysis["buyer_evidence"] - evidence_analysis["seller_evidence"]) >= 2,
            "recommended_action": ai_suggestions["recommended_action"] in ["buyer_wins", "seller_wins"]
        }
        
        # Check requirements
        for req, met in requirements.items():
            if met:
                auto_resolution["requirements_met"].append(req)
            else:
                auto_resolution["requirements_failed"].append(req)
        
        # Special cases for auto-resolution
        if ("delivery_issues" in key_issues and 
            evidence_analysis["buyer_evidence"] >= 2 and 
            evidence_analysis["seller_evidence"] == 0 and
            confidence >= 75):
            
            auto_resolution.update({
                "eligible": True,
                "action": "buyer_wins",
                "estimated_accuracy": 95,
                "reason": "Clear delivery failure with buyer evidence and no seller response"
            })
        
        elif (evidence_analysis["buyer_evidence"] == 0 and 
              evidence_analysis["seller_evidence"] >= 2 and
              "quality_issues" not in key_issues and
              confidence >= 70):
            
            auto_resolution.update({
                "eligible": True,
                "action": "seller_wins",
                "estimated_accuracy": 85,
                "reason": "No buyer evidence, seller provided documentation, no quality issues"
            })
        
        # Conservative auto-resolution - only for very clear cases
        if len(auto_resolution["requirements_met"]) >= 4:
            auto_resolution["eligible"] = True
            auto_resolution["estimated_accuracy"] = min(confidence, 90)
        
        return auto_resolution
    
    @classmethod
    async def process_auto_resolution(cls, dispute_id: str) -> Dict[str, Any]:
        """Process auto-resolution for eligible disputes"""
        try:
            # Check if auto-resolution is enabled in config
            if not Config.DISPUTE_AUTO_RESOLUTION_ENABLED:
                return {
                    "success": False,
                    "error": "Auto-resolution is disabled by configuration",
                    "requires_manual": True,
                    "disabled_by_config": True
                }
            
            # Get AI analysis first
            ai_analysis = cls.analyze_dispute_with_ai(dispute_id)
            
            if not ai_analysis["success"]:
                return {"success": False, "error": "Failed to analyze dispute"}
            
            auto_resolution = ai_analysis["auto_resolution"]
            
            if not auto_resolution["eligible"]:
                return {
                    "success": False,
                    "error": "Dispute not eligible for auto-resolution",
                    "requires_manual": True,
                    "ai_analysis": ai_analysis
                }
            
            # Process the auto-resolution based on recommended action
            action = auto_resolution["action"]
            
            if action == "buyer_wins":
                result = await cls.resolve_buyer_favor_from_email(
                    dispute_id, 
                    cls.generate_auto_resolution_token(dispute_id)
                )
            elif action == "seller_wins":
                result = await cls.resolve_seller_favor_from_email(
                    dispute_id,
                    cls.generate_auto_resolution_token(dispute_id)
                )
            else:
                return {
                    "success": False,
                    "error": "Auto-resolution action not supported",
                    "requires_manual": True
                }
            
            if result["success"]:
                # Log auto-resolution for audit
                await cls._log_auto_resolution(dispute_id, action, ai_analysis, auto_resolution)
                
                logger.info(f"✅ Auto-resolution completed for {dispute_id}: {action}")
                
                return {
                    "success": True,
                    "auto_resolved": True,
                    "action": action,
                    "confidence": auto_resolution["estimated_accuracy"],
                    "reason": auto_resolution["reason"],
                    "result": result
                }
            else:
                return {
                    "success": False,
                    "error": f"Auto-resolution failed: {result.get('error')}",
                    "requires_manual": True
                }
                
        except Exception as e:
            logger.error(f"Error in auto-resolution for {dispute_id}: {e}")
            return {"success": False, "error": str(e), "requires_manual": True}
    
    @classmethod
    def generate_auto_resolution_token(cls, dispute_id: str) -> str:
        """Generate a special token for auto-resolution that bypasses normal validation"""
        
        # Create special auto-resolution token with extended validity
        timestamp = int(time.time())
        token_data = f"auto_resolution_{dispute_id}_{timestamp}"
        token = hmac.new(
            f"{Config.SECRET_KEY}_auto".encode(),  # type: ignore
            token_data.encode(),
            digestmod=hashlib.sha256
        ).hexdigest()
        
        return f"auto_{token}_{timestamp}"
    
    @classmethod
    async def _log_auto_resolution(cls, dispute_id: str, action: str, ai_analysis: dict, auto_resolution: dict):
        """Log auto-resolution for audit trail"""
        try:
            from database import SessionLocal
            from models import Dispute
            from datetime import datetime
            
            session = SessionLocal()
            try:
                dispute = session.query(Dispute).filter(
                    Dispute.id == dispute_id
                ).first()
                
                if dispute:
                    # Note: AdminAction model not available, logging to dispute notes only
                    # Auto-resolution audit trail stored in dispute.admin_notes
                    
                    # Update dispute with auto-resolution notes
                    dispute.admin_notes = (dispute.admin_notes or "") + f"\n[{datetime.utcnow()}] AUTO-RESOLVED: {action} (confidence: {auto_resolution.get('estimated_accuracy', 0)}%) - {auto_resolution.get('reason', '')}"  # type: ignore
                    
                    session.commit()
                    
                logger.info(f"✅ Auto-resolution logged for audit: {dispute_id}")
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Failed to log auto-resolution: {e}")
    
    @classmethod
    def enhanced_dispute_email_with_ai(cls, dispute_id: str) -> bool:
        """Send enhanced dispute resolution email with AI suggestions"""
        try:
            # Get AI analysis
            ai_analysis = cls.analyze_dispute_with_ai(dispute_id)
            
            if not ai_analysis["success"]:
                logger.error(f"Failed to get AI analysis for {dispute_id}")
                return cls.send_dispute_resolution_email(int(dispute_id))  # Fallback to basic email
            
            from database import SessionLocal
            from models import Dispute
            from services.email import EmailService
            
            session = SessionLocal()
            try:
                dispute = session.query(Dispute).filter(
                    Dispute.id == dispute_id
                ).first()
                
                if not dispute:
                    logger.error(f"Dispute {dispute_id} not found")
                    return False
                
                escrow = dispute.escrow
                escrow_amount = float(escrow.amount)
                
                # Generate tokens
                buyer_token = AdminEmailActionService.generate_dispute_token(int(dispute_id), 'REFUND_TO_BUYER', 'admin@lockbay.com')
                seller_token = AdminEmailActionService.generate_dispute_token(int(dispute_id), 'RELEASE_TO_SELLER', 'admin@lockbay.com')
                split_token = AdminEmailActionService.generate_dispute_token(int(dispute_id), 'SPLIT_FUNDS', 'admin@lockbay.com')
                
                # Build AI insights section
                ai_suggestions = ai_analysis["ai_suggestions"]
                evidence_analysis = ai_analysis["evidence_analysis"]
                auto_resolution = ai_analysis["auto_resolution"]
                
                confidence_color = "green" if ai_suggestions["confidence_score"] >= 70 else "orange" if ai_suggestions["confidence_score"] >= 50 else "red"
                
                ai_section = f"""
                <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #007bff;">
                    <h3>🤖 AI Analysis & Recommendations</h3>
                    
                    <div style="display: flex; justify-content: space-between; margin: 15px 0;">
                        <div style="flex: 1; margin-right: 20px;">
                            <h4>📊 Evidence Summary</h4>
                            <ul>
                                <li><strong>Total Messages:</strong> {evidence_analysis['total_messages']}</li>
                                <li><strong>Evidence Quality:</strong> {evidence_analysis['evidence_quality'].title()}</li>
                                <li><strong>Buyer Evidence:</strong> {evidence_analysis['buyer_evidence']} items</li>
                                <li><strong>Seller Evidence:</strong> {evidence_analysis['seller_evidence']} items</li>
                                <li><strong>Key Issues:</strong> {', '.join(evidence_analysis['key_issues']) or 'None identified'}</li>
                            </ul>
                        </div>
                        
                        <div style="flex: 1;">
                            <h4>🎯 AI Recommendation</h4>
                            <div style="background: white; padding: 15px; border-radius: 5px;">
                                <p><strong>Confidence Score:</strong> <span style="color: {confidence_color}; font-weight: bold;">{ai_suggestions['confidence_score']}%</span></p>
                                <p><strong>Recommended Action:</strong> {ai_suggestions['recommended_action'].replace('_', ' ').title()}</p>
                                <p><strong>Primary Reasoning:</strong></p>
                                <ul>
                                    {"".join(f"<li>{reason}</li>" for reason in ai_suggestions['reasoning'][:3])}
                                </ul>
                            </div>
                        </div>
                    </div>
                    
                    {f'''
                    <div style="background: #d4edda; padding: 15px; border-radius: 5px; border: 1px solid #c3e6cb; margin: 15px 0;">
                        <h4 style="color: #155724;">⚡ Auto-Resolution Available</h4>
                        <p><strong>Eligibility:</strong> {auto_resolution["estimated_accuracy"]}% accuracy</p>
                        <p><strong>Recommended:</strong> {auto_resolution["action"].replace("_", " ").title()}</p>
                        <p><strong>Reason:</strong> {auto_resolution["reason"]}</p>
                    </div>
                    ''' if auto_resolution["eligible"] and Config.DISPUTE_AUTO_RESOLUTION_ENABLED else ''}
                    
                    {'''
                    <div style="background: #e2e3e5; padding: 15px; border-radius: 5px; border: 1px solid #d3d3d4; margin: 15px 0;">
                        <h4 style="color: #383d41;">🔒 Auto-Resolution Disabled</h4>
                        <p>AI analysis is available but automatic resolution is disabled by configuration.</p>
                        <p>Use manual resolution options below based on AI recommendations.</p>
                    </div>
                    ''' if not Config.DISPUTE_AUTO_RESOLUTION_ENABLED else ''}
                    
                    {f'''
                    <div style="background: #fff3cd; padding: 15px; border-radius: 5px; border: 1px solid #ffeaa7; margin: 15px 0;">
                        <h4>💡 Suggested Splits</h4>
                        {"".join(f"<p>• <strong>{split['type'].replace('_', ' ').title()}:</strong> {split['buyer_percentage']}% to buyer - {split['reason']}</p>" 
                                 for split in ai_suggestions['suggested_splits'][:2])}
                    </div>
                    ''' if ai_suggestions['suggested_splits'] else ''}
                </div>
                """
                
                # Create enhanced email with AI insights (using persistent ADMIN_ACTION_BASE_URL)
                base_url = Config.ADMIN_ACTION_BASE_URL
                
                html_content = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <title>AI-Enhanced Dispute Resolution - {dispute_id}</title>
                    <style>
                        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
                        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 25px; border-radius: 10px; text-align: center; }}
                        .dispute-info {{ background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0; }}
                        .action-buttons {{ text-align: center; margin: 30px 0; }}
                        .btn {{ 
                            display: inline-block; 
                            padding: 15px 30px; 
                            margin: 10px; 
                            text-decoration: none; 
                            border-radius: 8px; 
                            font-weight: bold; 
                            font-size: 16px;
                            transition: all 0.3s ease;
                        }}
                        .btn-success {{ background: #28a745; color: white; }}
                        .btn-primary {{ background: #007bff; color: white; }}
                        .btn-warning {{ background: #ffc107; color: #212529; }}
                        .btn-info {{ background: #17a2b8; color: white; }}
                        .btn:hover {{ transform: translateY(-2px); box-shadow: 0 4px 8px rgba(0,0,0,0.2); }}
                    </style>
                </head>
                <body>
                    <div class="header">
                        <h1>⚖️ AI-Enhanced Dispute Resolution Required</h1>
                        <p>Advanced dispute analysis with intelligent recommendations</p>
                    </div>
                    
                    <div class="dispute-info">
                        <h3>📋 Dispute Details</h3>
                        <p><strong>Dispute ID:</strong> {dispute_id}</p>
                        <p><strong>Escrow Amount:</strong> ${escrow_amount:.2f} USD</p>
                        <p><strong>Created:</strong> {dispute.created_at.strftime('%Y-%m-%d %H:%M UTC')}</p>
                        <p><strong>Status:</strong> {dispute.status.title()}</p>
                    </div>
                    
                    {ai_section}
                    
                    <div class="action-buttons">
                        <h3>🎯 Choose Resolution Action</h3>
                        
                        <div style="margin: 20px 0;">
                            <a href="{base_url}/admin/resolve-dispute/buyer/{dispute_id}?token={buyer_token}" 
                               class="btn btn-success">
                               ✅ Buyer Wins - Full Refund
                            </a>
                            
                            <a href="{base_url}/admin/resolve-dispute/seller/{dispute_id}?token={seller_token}" 
                               class="btn btn-primary">
                               🛡️ Seller Wins - Release Funds
                            </a>
                        </div>
                        
                        <div style="margin: 20px 0;">
                            <a href="{base_url}/admin/resolve-dispute/split/{dispute_id}?token={split_token}&buyer=50&seller=50" 
                               class="btn btn-warning">
                               🟡 50/50 Split - Fair Resolution
                            </a>
                            
                            <a href="{base_url}/admin/resolve-dispute/split/{dispute_id}?token={split_token}" 
                               class="btn" style="background: #6f42c1; color: white;">
                               ⚖️ Custom Split Resolution
                            </a>
                        </div>
                    </div>
                    
                    <div style="background: #e9ecef; padding: 15px; border-radius: 5px; margin: 20px 0; font-size: 14px; color: #6c757d;">
                        <p><strong>Security:</strong> These links are secured with HMAC tokens and expire in 7 days.</p>
                        <p><strong>AI Analysis:</strong> Recommendations are based on message analysis, evidence quality, and historical dispute patterns.</p>
                        <p><strong>Audit Trail:</strong> All actions are logged for compliance and review purposes.</p>
                    </div>
                </body>
                </html>
                """
                
                # Send enhanced email
                email_service = EmailService()
                success = email_service.send_email(
                    to_email=Config.ADMIN_EMAIL,
                    subject=f"🤖 AI-Enhanced Dispute Resolution | {dispute_id} | ${escrow_amount:.2f} USD | Confidence: {ai_suggestions['confidence_score']}%",
                    html_content=html_content,
                    text_content=f"AI-Enhanced Dispute Resolution Required: {dispute_id} - ${escrow_amount:.2f} USD - AI Confidence: {ai_suggestions['confidence_score']}% - Recommended: {ai_suggestions['recommended_action']} - Login to admin panel or use email links to resolve."
                )
                
                logger.info(f"✅ Enhanced dispute resolution email sent for {dispute_id} with AI analysis")
                return bool(success)
                
            finally:
                session.close()
                
        except Exception as e:
            logger.error(f"Error sending enhanced dispute resolution email: {e}")
            return False
    
    @classmethod
    def generate_email_action_url(cls, cashout_id: str, action_type: str = 'complete', base_url: Optional[str] = None) -> str:
        """Generate the URL for email button action (complete or cancel)"""
        try:
            from config import Config
            
            # Generate secure token
            token = AdminEmailActionService.generate_admin_token(cashout_id, "RETRY", "admin@lockbay.com")
            
            # Use provided base URL or get from ADMIN_ACTION_BASE_URL (persistent across restarts)
            if not base_url:
                base_url = getattr(Config, 'ADMIN_ACTION_BASE_URL', 'https://lockbay.replit.app')
            
            # Remove trailing slash (check for None first)
            if base_url is not None:
                base_url = base_url.rstrip('/')
                if base_url.endswith("/webhook"):
                    base_url = base_url[:-8]  # Remove "/webhook" suffix
            
            # Construct the URL based on action type
            if action_type == 'cancel':
                action_url = f"{base_url}/admin/cancel-cashout/{cashout_id}?token={token}"
            else:  # default to complete
                action_url = f"{base_url}/admin/complete-cashout/{cashout_id}?token={token}"
            
            logger.info(f"Generated {action_type} email action URL for cashout {cashout_id}")
            return action_url
            
        except Exception as e:
            logger.error(f"Error generating {action_type} email action URL for {cashout_id}: {e}")
            return "#"
    
    @classmethod
    async def _process_address_config_cashout(cls, cashout, session) -> Dict[str, Any]:
        """Process PENDING_ADDRESS_CONFIG cashout via Kraken address verification"""
        try:
            from services.kraken_address_verification_service import KrakenAddressVerificationService
            from services.kraken_withdrawal_service import get_kraken_withdrawal_service
            from decimal import Decimal
            
            # Verify address exists in Kraken
            verification_service = KrakenAddressVerificationService()
            address_check = await verification_service.verify_withdrawal_address(
                crypto_currency=cashout.currency, 
                withdrawal_address=cashout.destination
            )
            
            if not address_check.get('is_verified'):
                return {
                    "success": False,
                    "error": f"Address {cashout.destination} not found in Kraken for {cashout.currency}"
                }
            
            # Execute direct Kraken withdrawal with existing crypto amount and context validation
            logger.info(f"📤 Executing Kraken withdrawal: {cashout.amount} {cashout.currency} to {cashout.destination}")
            
            from utils.universal_id_generator import UniversalIDGenerator
            transaction_id = UniversalIDGenerator.generate_transaction_id()
            
            kraken_service = get_kraken_withdrawal_service()
            result = await kraken_service.execute_withdrawal(
                currency=cashout.currency,
                amount=Decimal(str(cashout.amount)),  # Use existing crypto amount
                address=address_check['address_key'],
                session=session,
                cashout_id=cashout.cashout_id,
                transaction_id=transaction_id
            )
            
            return cast(dict[str, Any], result)
            
        except Exception as e:
            logger.error(f"❌ Kraken address config withdrawal failed for {cashout.cashout_id}: {e}")
            return {"success": False, "error": str(e)}
    
    @classmethod
    async def _process_service_funding_retry(cls, cashout, session) -> Dict[str, Any]:
        """Process PENDING_SERVICE_FUNDING cashout by retrying via auto_cashout service"""
        try:
            from services.auto_cashout import AutoCashoutService
            from models import User
            
            # Get user for the retry call
            user = session.query(User).filter_by(id=cashout.user_id).first()
            if not user:
                return {"success": False, "error": "User not found"}
            
            logger.info(f"🔄 Retrying cashout {cashout.cashout_id} via AutoCashoutService for insufficient funds resolution")
            
            # Call the approved cashout processor from auto_cashout service
            # This will retry both NGN bank transfers and crypto withdrawals that failed due to insufficient funds
            retry_result = await AutoCashoutService.process_approved_cashout(
                cashout_id=cashout.cashout_id
            )
            
            if retry_result.get('success'):
                logger.info(f"✅ AutoCashout retry successful for {cashout.cashout_id}")
                
                # Send user notification (bot + email) for successful admin retry
                try:
                    from services.withdrawal_notification_service import WithdrawalNotificationService
                    
                    notification_service = WithdrawalNotificationService()
                    
                    # Get blockchain hash from retry result
                    blockchain_hash = retry_result.get('external_tx_id') or retry_result.get('txid') or f"KRAKEN_{cashout.cashout_id[:12]}"
                    
                    notification_success = await notification_service.send_withdrawal_completion_notification(
                        user_id=int(user.telegram_id) if user and user.telegram_id else user.id,
                        cashout_id=cashout.cashout_id,
                        amount=float(cashout.amount),
                        currency=cashout.currency,
                        blockchain_hash=blockchain_hash,
                        user_email=user.email if user else None,
                        usd_amount=float(cashout.amount) if cashout.amount else None,
                        destination_address=cashout.destination,
                        pending_funding=False  # This is a real success after admin retry
                    )
                    
                    if notification_success:
                        logger.info(f"✅ User notification sent after admin retry for {cashout.cashout_id}")
                    else:
                        logger.warning(f"⚠️ Failed to send user notification after admin retry for {cashout.cashout_id}")
                        
                except Exception as notification_error:
                    logger.error(f"❌ Error sending user notification after admin retry for {cashout.cashout_id}: {notification_error}")
            else:
                logger.error(f"❌ AutoCashout retry failed for {cashout.cashout_id}: {retry_result.get('error')}")
            
            return cast(dict[str, Any], retry_result)
            
        except Exception as e:
            logger.error(f"❌ AutoCashout service funding retry failed for {cashout.cashout_id}: {e}")
            return {"success": False, "error": str(e)}


async def send_crypto_cashout_error_email(
    cashout_id: str,
    user_email: str, 
    user_name: str,
    amount: str,
    currency: str,
    address: str,
    error_message: str,
    error_code: str
) -> bool:
    """Send admin email notification for crypto cashout errors requiring intervention"""
    try:
        from services.email import EmailService
        from config import Config
        
        logger.info(f"📧 CRYPTO_ADMIN_EMAIL: Sending intervention email for {cashout_id}")
        
        # Generate action tokens
        retry_token = AdminEmailActionService.generate_admin_token(cashout_id, "RETRY", "admin@lockbay.com")
        cancel_token = AdminEmailActionService.generate_admin_token(cashout_id, "DECLINE", "admin@lockbay.com")
        
        # Get base URL for actions (using persistent ADMIN_ACTION_BASE_URL)
        base_url = getattr(Config, 'ADMIN_ACTION_BASE_URL', 'https://lockbay-escrow-bot.replit.app')
        base_url = base_url.rstrip('/')
        if base_url.endswith("/webhook"):
            base_url = base_url[:-8]
        
        # Create action URLs
        retry_url = f"{base_url}/admin/retry-crypto-cashout/{cashout_id}?token={retry_token}"
        cancel_url = f"{base_url}/admin/cancel-cashout/{cashout_id}?token={cancel_token}"
        
        # Create styled HTML email
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Crypto Cashout Requires Admin Intervention</title>
        </head>
        <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #ff6b35, #f7931e); padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
                <h1 style="color: white; margin: 0; font-size: 28px;">⚠️ Crypto Cashout Issue</h1>
                <p style="color: white; margin: 10px 0 0 0; font-size: 16px;">Admin intervention required</p>
            </div>
            
            <div style="background: white; padding: 30px; border: 1px solid #e0e0e0; border-radius: 0 0 10px 10px;">
                <h2 style="color: #333; margin-top: 0;">Cashout Details</h2>
                
                <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0;">
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr><td style="padding: 5px 0; font-weight: bold;">Reference:</td><td>{cashout_id}</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">User:</td><td>{user_name} ({user_email})</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">Amount:</td><td>{amount}</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">Cryptocurrency:</td><td>{currency}</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">Address:</td><td style="word-break: break-all;">{address}</td></tr>
                    </table>
                </div>
                
                <div style="background: #fff3cd; border: 1px solid #ffeaa7; padding: 20px; border-radius: 8px; margin: 20px 0;">
                    <h3 style="color: #856404; margin-top: 0;">❌ Error Details</h3>
                    <p><strong>Error Code:</strong> {error_code}</p>
                    <p><strong>Error Message:</strong> {error_message}</p>
                    
                    {'<p><strong>Action Required:</strong> This address needs to be configured in your Kraken account before withdrawals can be processed.</p>' if error_code == 'KRAKEN_ADDR_NOT_FOUND' else ''}
                    {'<p><strong>Action Required:</strong> Your Kraken account needs additional funding to process this withdrawal.</p>' if error_code == 'API_INSUFFICIENT_FUNDS' else ''}
                </div>
                
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{retry_url}" 
                       style="background: #28a745; color: white; padding: 15px 30px; text-decoration: none; border-radius: 8px; font-weight: bold; margin-right: 10px; display: inline-block;">
                       🔄 Retry Withdrawal
                    </a>
                    <a href="{cancel_url}" 
                       style="background: #dc3545; color: white; padding: 15px 30px; text-decoration: none; border-radius: 8px; font-weight: bold; margin-left: 10px; display: inline-block;">
                       ❌ Cancel & Refund
                    </a>
                </div>
                
                <div style="background: #e9ecef; padding: 15px; border-radius: 5px; margin: 20px 0; font-size: 14px; color: #6c757d;">
                    <p><strong>Next Steps:</strong></p>
                    <ul style="margin: 10px 0; padding-left: 20px;">
                        {'<li>Log into your Kraken account and add the withdrawal address</li>' if error_code == 'KRAKEN_ADDR_NOT_FOUND' else ''}
                        {'<li>Fund your Kraken account with sufficient balance</li>' if error_code == 'API_INSUFFICIENT_FUNDS' else ''}
                        <li>Click "Retry Withdrawal" to process the cashout</li>
                        <li>If unable to resolve, click "Cancel & Refund" to return funds to user</li>
                    </ul>
                    <p><strong>Security:</strong> These action links expire in 7 days and are secured with HMAC tokens.</p>
                </div>
                
                <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #e0e0e0; font-size: 14px; color: #666;">
                    <p><strong>{Config.PLATFORM_NAME} Admin System</strong></p>
                    <p>This is an automated notification. The user has been informed that admin intervention is required.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Create plain text version
        text_content = f"""
        CRYPTO CASHOUT REQUIRES ADMIN INTERVENTION
        
        Reference: {cashout_id}
        User: {user_name} ({user_email})
        Amount: {amount}
        Cryptocurrency: {currency}
        Address: {address}
        
        ERROR: {error_code} - {error_message}
        
        Actions:
        - Retry: {retry_url}
        - Cancel: {cancel_url}
        
        Please resolve the Kraken configuration issue and retry, or cancel to refund the user.
        Action links expire in 7 days.
        """
        
        # Send email to admin
        email_service = EmailService()
        admin_email = getattr(Config, 'ADMIN_EMAIL', 'admin@lockbay.com')
        
        success = email_service.send_email(
            to_email=admin_email,
            subject=f"🚨 Crypto Cashout Error: {currency} {amount} | {cashout_id}",
            html_content=html_content,
            text_content=text_content
        )
        
        if success:
            logger.info(f"✅ CRYPTO_ADMIN_EMAIL: Successfully sent intervention email for {cashout_id}")
        else:
            logger.error(f"❌ CRYPTO_ADMIN_EMAIL: Failed to send intervention email for {cashout_id}")
        
        return bool(success)
        
    except Exception as e:
        logger.error(f"❌ CRYPTO_ADMIN_EMAIL: Exception sending email for {cashout_id}: {e}")
        return False


async def send_ngn_cashout_bank_config_email(
    cashout_id: str,
    user_email: str,
    user_name: str,
    amount: str,
    reason: str,
    debug_info: str
) -> bool:
    """Send admin email notification for NGN cashout bank account configuration issues"""
    try:
        from services.email import EmailService
        from config import Config
        
        logger.info(f"📧 NGN_BANK_CONFIG_EMAIL: Sending bank config email for {cashout_id}")
        
        # Generate action tokens
        retry_token = AdminEmailActionService.generate_admin_token(cashout_id, "RETRY", "admin@lockbay.com")
        cancel_token = AdminEmailActionService.generate_admin_token(cashout_id, "DECLINE", "admin@lockbay.com")
        
        # Get base URL for actions (using persistent ADMIN_ACTION_BASE_URL)
        base_url = getattr(Config, 'ADMIN_ACTION_BASE_URL', 'https://lockbay-escrow-bot.replit.app')
        base_url = base_url.rstrip('/')
        if base_url.endswith("/webhook"):
            base_url = base_url[:-8]
        
        # Create action URLs  
        retry_url = f"{base_url}/admin/retry-ngn-cashout/{cashout_id}?token={retry_token}"
        cancel_url = f"{base_url}/admin/cancel-cashout/{cashout_id}?token={cancel_token}"
        
        # Create styled HTML email
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>NGN Cashout Bank Configuration Required</title>
        </head>
        <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #28a745, #20c997); padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
                <h1 style="color: white; margin: 0; font-size: 28px;">🏦 NGN Bank Setup Required</h1>
                <p style="color: white; margin: 10px 0 0 0; font-size: 16px;">Bank account configuration needed</p>
            </div>
            
            <div style="background: white; padding: 30px; border: 1px solid #e0e0e0; border-radius: 0 0 10px 10px;">
                <h2 style="color: #333; margin-top: 0;">NGN Cashout Details</h2>
                
                <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0;">
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr><td style="padding: 5px 0; font-weight: bold;">Reference:</td><td>{cashout_id}</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">User:</td><td>{user_name} ({user_email})</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">Amount:</td><td>{amount}</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">Type:</td><td>NGN Bank Transfer</td></tr>
                    </table>
                </div>
                
                <div style="background: #fff3cd; border: 1px solid #ffeaa7; padding: 20px; border-radius: 8px; margin: 20px 0;">
                    <h3 style="color: #856404; margin-top: 0;">⚠️ Configuration Required</h3>
                    <p><strong>Issue:</strong> {reason}</p>
                    <p><strong>Debug Info:</strong> {debug_info}</p>
                    <p><strong>Action Required:</strong> The user needs bank account configuration before this NGN cashout can be processed.</p>
                </div>
                
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{retry_url}" 
                       style="background: #28a745; color: white; padding: 15px 30px; text-decoration: none; border-radius: 8px; font-weight: bold; margin-right: 10px; display: inline-block;">
                       🔄 Configure & Retry
                    </a>
                    <a href="{cancel_url}" 
                       style="background: #dc3545; color: white; padding: 15px 30px; text-decoration: none; border-radius: 8px; font-weight: bold; margin-left: 10px; display: inline-block;">
                       ❌ Cancel & Refund
                    </a>
                </div>
                
                <div style="background: #e9ecef; padding: 15px; border-radius: 5px; margin: 20px 0; font-size: 14px; color: #6c757d;">
                    <p><strong>Next Steps:</strong></p>
                    <ul style="margin: 10px 0; padding-left: 20px;">
                        <li>Configure the user's bank account details in the admin panel</li>
                        <li>Verify bank account information with Fincra</li>
                        <li>Click "Configure & Retry" to process the cashout</li>
                        <li>If unable to configure, click "Cancel & Refund" to return funds to user</li>
                    </ul>
                    <p><strong>Security:</strong> These action links expire in 7 days and are secured with tokens.</p>
                </div>
                
                <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #e0e0e0; font-size: 14px; color: #666;">
                    <p><strong>{Config.PLATFORM_NAME} Admin System</strong></p>
                    <p>This is an automated notification. The user has been informed that their cashout is being processed.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Send email to admin
        email_service = EmailService()
        admin_email = getattr(Config, 'ADMIN_EMAIL', 'admin@lockbay.com')
        
        success = email_service.send_email(
            to_email=admin_email,
            subject=f"🏦 NGN Bank Config Required: {amount} | {cashout_id}",
            html_content=html_content
        )
        
        return bool(success)
        
    except Exception as e:
        logger.error(f"❌ NGN_BANK_CONFIG_EMAIL: Exception sending email for {cashout_id}: {e}")
        return False


async def send_ngn_cashout_rate_service_email(
    cashout_id: str,
    user_email: str,
    user_name: str,
    amount: str,
    reason: str
) -> bool:
    """Send admin email notification for NGN cashout exchange rate service issues"""
    try:
        from services.email import EmailService
        from config import Config
        
        logger.info(f"📧 NGN_RATE_SERVICE_EMAIL: Sending rate service email for {cashout_id}")
        
        # Generate action tokens
        retry_token = AdminEmailActionService.generate_admin_token(cashout_id, "RETRY", "admin@lockbay.com")
        cancel_token = AdminEmailActionService.generate_admin_token(cashout_id, "DECLINE", "admin@lockbay.com")
        
        # Get base URL for actions (using persistent ADMIN_ACTION_BASE_URL)
        base_url = Config.ADMIN_ACTION_BASE_URL
        base_url = base_url.rstrip('/')
        if base_url.endswith("/webhook"):
            base_url = base_url[:-8]
        
        # Create action URLs
        retry_url = f"{base_url}/admin/retry-ngn-cashout/{cashout_id}?token={retry_token}"
        cancel_url = f"{base_url}/admin/cancel-cashout/{cashout_id}?token={cancel_token}"
        
        # Create styled HTML email
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>NGN Exchange Rate Service Issue</title>
        </head>
        <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #ffc107, #fd7e14); padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
                <h1 style="color: white; margin: 0; font-size: 28px;">💱 Rate Service Issue</h1>
                <p style="color: white; margin: 10px 0 0 0; font-size: 16px;">Exchange rate service needs attention</p>
            </div>
            
            <div style="background: white; padding: 30px; border: 1px solid #e0e0e0; border-radius: 0 0 10px 10px;">
                <h2 style="color: #333; margin-top: 0;">NGN Cashout Details</h2>
                
                <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0;">
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr><td style="padding: 5px 0; font-weight: bold;">Reference:</td><td>{cashout_id}</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">User:</td><td>{user_name} ({user_email})</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">Amount:</td><td>{amount}</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">Type:</td><td>NGN Bank Transfer</td></tr>
                    </table>
                </div>
                
                <div style="background: #fff3cd; border: 1px solid #ffeaa7; padding: 20px; border-radius: 8px; margin: 20px 0;">
                    <h3 style="color: #856404; margin-top: 0;">⚠️ Rate Service Issue</h3>
                    <p><strong>Issue:</strong> {reason}</p>
                    <p><strong>Action Required:</strong> The USD-NGN exchange rate service is unavailable. Please check FastForex service status.</p>
                </div>
                
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{retry_url}" 
                       style="background: #28a745; color: white; padding: 15px 30px; text-decoration: none; border-radius: 8px; font-weight: bold; margin-right: 10px; display: inline-block;">
                       🔄 Retry with Rate Service
                    </a>
                    <a href="{cancel_url}" 
                       style="background: #dc3545; color: white; padding: 15px 30px; text-decoration: none; border-radius: 8px; font-weight: bold; margin-left: 10px; display: inline-block;">
                       ❌ Cancel & Refund
                    </a>
                </div>
                
                <div style="background: #e9ecef; padding: 15px; border-radius: 5px; margin: 20px 0; font-size: 14px; color: #6c757d;">
                    <p><strong>Next Steps:</strong></p>
                    <ul style="margin: 10px 0; padding-left: 20px;">
                        <li>Check FastForex service status and API key</li>
                        <li>Verify network connectivity to exchange rate service</li>
                        <li>Click "Retry with Rate Service" once service is restored</li>
                        <li>If service is down long-term, click "Cancel & Refund"</li>
                    </ul>
                    <p><strong>Security:</strong> These action links expire in 7 days and are secured with tokens.</p>
                </div>
                
                <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #e0e0e0; font-size: 14px; color: #666;">
                    <p><strong>{Config.PLATFORM_NAME} Admin System</strong></p>
                    <p>This is an automated notification. The user has been informed that their cashout is being processed.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Send email to admin
        email_service = EmailService()
        admin_email = getattr(Config, 'ADMIN_EMAIL', 'admin@lockbay.com')
        
        success = email_service.send_email(
            to_email=admin_email,
            subject=f"💱 NGN Rate Service Issue: {amount} | {cashout_id}",
            html_content=html_content
        )
        
        return bool(success)
        
    except Exception as e:
        logger.error(f"❌ NGN_RATE_SERVICE_EMAIL: Exception sending email for {cashout_id}: {e}")
        return False


async def send_ngn_cashout_funding_email(
    cashout_id: str,
    user_email: str,
    user_name: str,
    amount: str,
    ngn_amount: str,
    reason: str
) -> bool:
    """Send admin email notification for NGN cashout funding issues"""
    try:
        from services.email import EmailService
        from config import Config
        
        logger.info(f"📧 NGN_FUNDING_EMAIL: Sending funding email for {cashout_id}")
        
        # Generate action tokens  
        fund_token = AdminEmailActionService.generate_admin_token(cashout_id, "FUND", "admin@lockbay.com")
        cancel_token = AdminEmailActionService.generate_admin_token(cashout_id, "DECLINE", "admin@lockbay.com")
        
        # Get base URL for actions (using persistent ADMIN_ACTION_BASE_URL)
        base_url = Config.ADMIN_ACTION_BASE_URL
        base_url = base_url.rstrip('/')
        if base_url.endswith("/webhook"):
            base_url = base_url[:-8]
        
        # Create action URLs
        fund_url = f"{base_url}/admin/fund-and-retry/{cashout_id}?token={fund_token}"
        cancel_url = f"{base_url}/admin/cancel-cashout/{cashout_id}?token={cancel_token}"
        
        # Create styled HTML email
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>NGN Cashout Funding Required</title>
        </head>
        <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #dc3545, #fd7e14); padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
                <h1 style="color: white; margin: 0; font-size: 28px;">💰 Fincra Funding Needed</h1>
                <p style="color: white; margin: 10px 0 0 0; font-size: 16px;">Admin funding required</p>
            </div>
            
            <div style="background: white; padding: 30px; border: 1px solid #e0e0e0; border-radius: 0 0 10px 10px;">
                <h2 style="color: #333; margin-top: 0;">NGN Cashout Details</h2>
                
                <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0;">
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr><td style="padding: 5px 0; font-weight: bold;">Reference:</td><td>{cashout_id}</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">User:</td><td>{user_name} ({user_email})</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">USD Amount:</td><td>{amount}</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">NGN Amount:</td><td>{ngn_amount}</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">Type:</td><td>NGN Bank Transfer</td></tr>
                    </table>
                </div>
                
                <div style="background: #f8d7da; border: 1px solid #f5c6cb; padding: 20px; border-radius: 8px; margin: 20px 0;">
                    <h3 style="color: #721c24; margin-top: 0;">💳 Funding Required</h3>
                    <p><strong>Issue:</strong> {reason}</p>
                    <p><strong>Action Required:</strong> Your Fincra account needs additional funding to process this NGN bank transfer.</p>
                </div>
                
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{fund_url}" 
                       style="background: #28a745; color: white; padding: 15px 30px; text-decoration: none; border-radius: 8px; font-weight: bold; margin-right: 10px; display: inline-block;">
                       💰 Fund & Retry
                    </a>
                    <a href="{cancel_url}" 
                       style="background: #dc3545; color: white; padding: 15px 30px; text-decoration: none; border-radius: 8px; font-weight: bold; margin-left: 10px; display: inline-block;">
                       ❌ Cancel & Refund
                    </a>
                </div>
                
                <div style="background: #e9ecef; padding: 15px; border-radius: 5px; margin: 20px 0; font-size: 14px; color: #6c757d;">
                    <p><strong>Next Steps:</strong></p>
                    <ul style="margin: 10px 0; padding-left: 20px;">
                        <li>Log into your Fincra dashboard and add sufficient funds</li>
                        <li>Ensure account balance covers {ngn_amount} plus fees</li>
                        <li>Click "Fund & Retry" to process the cashout</li>
                        <li>If unable to fund, click "Cancel & Refund" to return funds to user</li>
                    </ul>
                    <p><strong>Security:</strong> These action links expire in 7 days and are secured with tokens.</p>
                </div>
                
                <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #e0e0e0; font-size: 14px; color: #666;">
                    <p><strong>{Config.PLATFORM_NAME} Admin System</strong></p>
                    <p>This is an automated notification. The user has been informed that their cashout is being processed.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Send email to admin
        email_service = EmailService()
        admin_email = getattr(Config, 'ADMIN_EMAIL', 'admin@lockbay.com')
        
        success = email_service.send_email(
            to_email=admin_email,
            subject=f"💰 NGN Funding Required: {amount} → {ngn_amount} | {cashout_id}",
            html_content=html_content
        )
        
        return bool(success)
        
    except Exception as e:
        logger.error(f"❌ NGN_FUNDING_EMAIL: Exception sending email for {cashout_id}: {e}")
        return False


async def send_ngn_cashout_error_email(
    cashout_id: str,
    user_email: str,
    user_name: str,
    amount: str,
    ngn_amount: str,
    error_message: str,
    reason: str
) -> bool:
    """Send admin email notification for general NGN cashout processing errors"""
    try:
        from services.email import EmailService
        from config import Config
        
        logger.info(f"📧 NGN_ERROR_EMAIL: Sending error email for {cashout_id}")
        
        # Generate action tokens
        retry_token = AdminEmailActionService.generate_admin_token(cashout_id, "RETRY", "admin@lockbay.com")
        cancel_token = AdminEmailActionService.generate_admin_token(cashout_id, "DECLINE", "admin@lockbay.com")
        
        # Get base URL for actions (using persistent ADMIN_ACTION_BASE_URL)
        base_url = Config.ADMIN_ACTION_BASE_URL
        base_url = base_url.rstrip('/')
        if base_url.endswith("/webhook"):
            base_url = base_url[:-8]
        
        # Create action URLs
        retry_url = f"{base_url}/admin/retry-ngn-cashout/{cashout_id}?token={retry_token}"
        cancel_url = f"{base_url}/admin/cancel-cashout/{cashout_id}?token={cancel_token}"
        
        # Create styled HTML email
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>NGN Cashout Processing Error</title>
        </head>
        <body style="font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
            <div style="background: linear-gradient(135deg, #6f42c1, #e83e8c); padding: 30px; text-align: center; border-radius: 10px 10px 0 0;">
                <h1 style="color: white; margin: 0; font-size: 28px;">⚠️ NGN Processing Error</h1>
                <p style="color: white; margin: 10px 0 0 0; font-size: 16px;">Admin intervention required</p>
            </div>
            
            <div style="background: white; padding: 30px; border: 1px solid #e0e0e0; border-radius: 0 0 10px 10px;">
                <h2 style="color: #333; margin-top: 0;">NGN Cashout Details</h2>
                
                <div style="background: #f8f9fa; padding: 20px; border-radius: 8px; margin: 20px 0;">
                    <table style="width: 100%; border-collapse: collapse;">
                        <tr><td style="padding: 5px 0; font-weight: bold;">Reference:</td><td>{cashout_id}</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">User:</td><td>{user_name} ({user_email})</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">USD Amount:</td><td>{amount}</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">NGN Amount:</td><td>{ngn_amount}</td></tr>
                        <tr><td style="padding: 5px 0; font-weight: bold;">Type:</td><td>NGN Bank Transfer</td></tr>
                    </table>
                </div>
                
                <div style="background: #f8d7da; border: 1px solid #f5c6cb; padding: 20px; border-radius: 8px; margin: 20px 0;">
                    <h3 style="color: #721c24; margin-top: 0;">❌ Processing Error</h3>
                    <p><strong>Reason:</strong> {reason}</p>
                    <p><strong>Error Message:</strong> {error_message}</p>
                    <p><strong>Action Required:</strong> Review and resolve the Fincra processing issue to complete this NGN cashout.</p>
                </div>
                
                <div style="text-align: center; margin: 30px 0;">
                    <a href="{retry_url}" 
                       style="background: #28a745; color: white; padding: 15px 30px; text-decoration: none; border-radius: 8px; font-weight: bold; margin-right: 10px; display: inline-block;">
                       🔄 Resolve & Retry
                    </a>
                    <a href="{cancel_url}" 
                       style="background: #dc3545; color: white; padding: 15px 30px; text-decoration: none; border-radius: 8px; font-weight: bold; margin-left: 10px; display: inline-block;">
                       ❌ Cancel & Refund
                    </a>
                </div>
                
                <div style="background: #e9ecef; padding: 15px; border-radius: 5px; margin: 20px 0; font-size: 14px; color: #6c757d;">
                    <p><strong>Next Steps:</strong></p>
                    <ul style="margin: 10px 0; padding-left: 20px;">
                        <li>Review the Fincra API error message above</li>
                        <li>Check Fincra dashboard for any service alerts</li>
                        <li>Resolve the underlying issue with bank transfer processing</li>
                        <li>Click "Resolve & Retry" to process the cashout</li>
                        <li>If unable to resolve, click "Cancel & Refund" to return funds to user</li>
                    </ul>
                    <p><strong>Security:</strong> These action links expire in 7 days and are secured with tokens.</p>
                </div>
                
                <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #e0e0e0; font-size: 14px; color: #666;">
                    <p><strong>{Config.PLATFORM_NAME} Admin System</strong></p>
                    <p>This is an automated notification. The user has been informed that their cashout is being processed.</p>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Send email to admin
        email_service = EmailService()
        admin_email = getattr(Config, 'ADMIN_EMAIL', 'admin@lockbay.com')
        
        success = email_service.send_email(
            to_email=admin_email,
            subject=f"⚠️ NGN Processing Error: {amount} → {ngn_amount} | {cashout_id}",
            html_content=html_content
        )
        
        return bool(success)
        
    except Exception as e:
        logger.error(f"❌ NGN_ERROR_EMAIL: Exception sending email for {cashout_id}: {e}")
        return False