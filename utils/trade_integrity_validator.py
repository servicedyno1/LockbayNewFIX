"""
Trade Integrity Validator - Prevents data integrity issues in trade system
This module ensures all trade data remains consistent and prevents critical failures.
"""

import logging
from database import SessionLocal
from models import Escrow, User, NotificationPreference
from typing import Dict, Any

logger = logging.getLogger(__name__)

class TradeIntegrityValidator:
    """Comprehensive validation system to prevent trade data integrity issues"""
    
    @staticmethod
    def validate_seller_assignment(escrow_id: str, user_id: int) -> Dict[str, Any]:
        """
        Validate that seller assignment is successful and consistent
        Returns validation result with status and details
        """
        session = SessionLocal()
        try:
            # Get the escrow
            escrow = session.query(Escrow).filter(Escrow.escrow_id == escrow_id).first()
            if not escrow:
                return {
                    "valid": False,
                    "error": f"Escrow {escrow_id} not found",
                    "critical": True
                }
            
            # Get the user
            user = session.query(User).filter(User.id == user_id).first()
            if not user:
                return {
                    "valid": False,
                    "error": f"User {user_id} not found",
                    "critical": True
                }
            
            # Validate seller_id assignment
            if escrow.seller_id != user_id:
                logger.error(f"🚨 CRITICAL: Seller ID mismatch - Expected: {user_id}, Got: {escrow.seller_id}")
                return {
                    "valid": False,
                    "error": f"Seller ID assignment failed - database shows {escrow.seller_id} instead of {user_id}",
                    "critical": True,
                    "corrective_action": "seller_id_reassignment_needed"
                }
            
            # Check notification preferences exist
            notification_prefs = session.query(NotificationPreference).filter(
                NotificationPreference.user_id == user_id
            ).first()
            
            notification_issue = None
            if not notification_prefs:
                notification_issue = f"Missing NotificationPreference for user {user_id}"
                logger.warning(f"⚠️ NOTIFICATION INTEGRITY: {notification_issue}")
            
            return {
                "valid": True,
                "escrow_id": escrow_id,
                "seller_id": user_id,
                "seller_username": user.username,
                "notification_prefs_exist": notification_prefs is not None,
                "notification_issue": notification_issue,
                "trade_status": escrow.status
            }
            
        except Exception as e:
            logger.error(f"❌ Error validating seller assignment for {escrow_id}: {e}")
            return {
                "valid": False,
                "error": f"Validation failed: {str(e)}",
                "critical": True
            }
        finally:
            session.close()
    
    @staticmethod
    def fix_missing_notification_preferences(user_id: int) -> bool:
        """
        Create missing notification preferences for a user
        Returns True if successful, False otherwise
        """
        session = SessionLocal()
        try:
            # Check if already exists
            existing = session.query(NotificationPreference).filter(
                NotificationPreference.user_id == user_id
            ).first()
            
            if existing:
                logger.info(f"✅ Notification preferences already exist for user {user_id}")
                return True
            
            # Create default preferences (using actual model fields - both email and bot by default)
            preference = NotificationPreference(
                user_id=user_id,
                telegram_enabled=True,
                email_enabled=True,
                escrow_updates=True,
                payment_notifications=True,
                dispute_notifications=True,
                security_alerts=True,
                marketing_emails=False,
            )
            session.add(preference)
            session.commit()
            logger.info(f"✅ INTEGRITY FIX: Created missing notification preferences for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to fix notification preferences for user {user_id}: {e}")
            session.rollback()
            return False
        finally:
            session.close()
    
    @staticmethod
    def validate_trade_consistency(escrow_id: str) -> Dict[str, Any]:
        """
        Comprehensive trade consistency check
        Validates all critical trade data integrity
        """
        session = SessionLocal()
        try:
            escrow = session.query(Escrow).filter(Escrow.escrow_id == escrow_id).first()
            if not escrow:
                return {
                    "valid": False,
                    "error": f"Trade {escrow_id} not found",
                    "critical": True
                }
            
            issues = []
            warnings = []
            
            # Check buyer exists
            buyer = session.query(User).filter(User.id == escrow.buyer_id).first()
            if not buyer:
                issues.append(f"Buyer ID {escrow.buyer_id} not found in users table")
            
            # Check seller assignment consistency
            if escrow.seller_id:
                seller = session.query(User).filter(User.id == escrow.seller_id).first()
                if not seller:
                    issues.append(f"Seller ID {escrow.seller_id} not found in users table")
                else:
                    # Check notification preferences for both users
                    for user_id, user_type in [(escrow.buyer_id, "buyer"), (escrow.seller_id, "seller")]:
                        prefs = session.query(NotificationPreference).filter(
                            NotificationPreference.user_id == user_id
                        ).first()
                        if not prefs:
                            warnings.append(f"Missing notification preferences for {user_type} (ID: {user_id})")
            
            # Check for required fields based on status
            if escrow.status == "active" and not escrow.seller_id:
                issues.append("Active trade missing seller_id")
            
            # Check payment method consistency
            if escrow.payment_confirmed_at and not escrow.payment_method:
                warnings.append("Payment confirmed but payment method not recorded")
            
            return {
                "valid": len(issues) == 0,
                "escrow_id": escrow_id,
                "status": escrow.status,
                "issues": issues,
                "warnings": warnings,
                "critical": len(issues) > 0
            }
            
        except Exception as e:
            logger.error(f"❌ Error validating trade consistency for {escrow_id}: {e}")
            return {
                "valid": False,
                "error": f"Validation failed: {str(e)}",
                "critical": True
            }
        finally:
            session.close()
    
    @staticmethod
    def scan_all_trades_for_integrity_issues() -> Dict[str, Any]:
        """
        Scan all active/recent trades for data integrity issues
        Returns summary of found issues
        """
        session = SessionLocal()
        try:
            # Get all active and recent trades
            active_trades = session.query(Escrow).filter(
                Escrow.status.in_(["active", "payment_confirmed", "payment_pending"])
            ).all()
            
            results = {
                "total_scanned": len(active_trades),
                "issues_found": 0,
                "trades_with_issues": [],
                "missing_notification_prefs": 0,
                "seller_id_mismatches": 0
            }
            
            for trade in active_trades:
                validation = TradeIntegrityValidator.validate_trade_consistency(trade.escrow_id)
                if not validation["valid"] or validation.get("warnings"):
                    results["issues_found"] += 1
                    results["trades_with_issues"].append({
                        "escrow_id": trade.escrow_id,
                        "issues": validation.get("issues", []),
                        "warnings": validation.get("warnings", [])
                    })
                
                # Count specific issue types
                if validation.get("warnings"):
                    for warning in validation["warnings"]:
                        if "notification preferences" in warning:
                            results["missing_notification_prefs"] += 1
                
                if validation.get("issues"):
                    for issue in validation["issues"]:
                        if "seller_id" in issue.lower():
                            results["seller_id_mismatches"] += 1
            
            logger.info(f"🔍 Trade integrity scan completed: {results['issues_found']} issues found in {results['total_scanned']} trades")
            return results
            
        except Exception as e:
            logger.error(f"❌ Error scanning trades for integrity issues: {e}")
            return {
                "error": str(e),
                "total_scanned": 0,
                "issues_found": 0
            }
        finally:
            session.close()