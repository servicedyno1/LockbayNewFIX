"""
Duplicate Transaction Monitor Service
Monitors and alerts on potential duplicate transaction records that could cause refund issues
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any
from database import SessionLocal
from models import Transaction, Cashout, TransactionType
from services.consolidated_notification_service import consolidated_notification_service

logger = logging.getLogger(__name__)


class DuplicateTransactionMonitor:
    """Monitor for duplicate transaction records that could impact refunds"""
    
    @staticmethod
    async def scan_for_duplicate_transactions() -> Dict[str, Any]:
        """
        Scan for potential duplicate transactions and alert administrators
        Returns comprehensive analysis of duplicate transaction patterns
        """
        results = {
            "duplicate_groups_found": 0,
            "total_duplicates": 0,
            "potential_refund_issues": 0,
            "by_transaction_type": {},
            "by_time_window": {},
            "critical_issues": [],
            "duplicate_groups": []
        }
        
        session = None
        try:
            session = SessionLocal()
            
            # Look for duplicates in the last 7 days to focus on recent issues
            since_date = datetime.utcnow() - timedelta(days=7)
            
            logger.info(f"🔍 DUPLICATE_SCAN: Scanning for duplicate transactions since {since_date}")
            
            # SCAN 1: Find potential duplicate cashout debits (same user, amount, time window)
            cashout_duplicates = await DuplicateTransactionMonitor._find_duplicate_cashout_debits(
                session, since_date
            )
            
            if cashout_duplicates:
                results["duplicate_groups"].extend(cashout_duplicates)
                results["duplicate_groups_found"] += len(cashout_duplicates)
                for group in cashout_duplicates:
                    results["total_duplicates"] += len(group["transactions"])
            
            # SCAN 2: Find potential duplicate refunds (same user, amount, description pattern)
            refund_duplicates = await DuplicateTransactionMonitor._find_duplicate_refunds(
                session, since_date
            )
            
            if refund_duplicates:
                results["duplicate_groups"].extend(refund_duplicates)
                results["duplicate_groups_found"] += len(refund_duplicates)
                for group in refund_duplicates:
                    results["total_duplicates"] += len(group["transactions"])
            
            # SCAN 3: Find transaction patterns that could cause refund matching issues
            matching_issues = await DuplicateTransactionMonitor._find_refund_matching_issues(
                session, since_date
            )
            
            if matching_issues:
                results["potential_refund_issues"] = len(matching_issues)
                results["critical_issues"].extend(matching_issues)
            
            # Analyze patterns by transaction type
            results["by_transaction_type"] = DuplicateTransactionMonitor._analyze_by_transaction_type(
                results["duplicate_groups"]
            )
            
            # ALERT: Send notification if significant issues found
            if (results["duplicate_groups_found"] > 0 or 
                results["potential_refund_issues"] > 0):
                await DuplicateTransactionMonitor._send_duplicate_alert(results)
            
            logger.info(
                f"✅ DUPLICATE_SCAN_COMPLETE: "
                f"{results['duplicate_groups_found']} duplicate groups, "
                f"{results['total_duplicates']} total duplicates, "
                f"{results['potential_refund_issues']} refund issues identified"
            )
            
            return results
            
        except Exception as e:
            logger.error(f"❌ DUPLICATE_SCAN_ERROR: {e}")
            results["scan_error"] = str(e)
            return results
        finally:
            if session:
                session.close()
    
    @staticmethod
    async def _find_duplicate_cashout_debits(session, since_date: datetime) -> List[Dict[str, Any]]:
        """Find potential duplicate cashout debit transactions"""
        try:
            # Query for cashout debits grouped by user, amount, and close time proximity
            cashout_debits = session.query(Transaction).filter(
                Transaction.transaction_type == "cashout",
                Transaction.currency == "USD",
                Transaction.amount < 0,  # Debits
                Transaction.created_at >= since_date
            ).order_by(Transaction.user_id, Transaction.amount, Transaction.created_at).all()
            
            duplicate_groups = []
            
            # Group by user_id and amount, then check for time proximity
            user_amount_groups = {}
            for transaction in cashout_debits:
                key = (transaction.user_id, str(transaction.amount))
                if key not in user_amount_groups:
                    user_amount_groups[key] = []
                user_amount_groups[key].append(transaction)
            
            # Find groups with potential duplicates
            for (user_id, amount), transactions in user_amount_groups.items():
                if len(transactions) > 1:
                    # Check if any transactions are within 30 minutes of each other
                    for i in range(len(transactions)):
                        for j in range(i + 1, len(transactions)):
                            time_diff = abs((transactions[i].created_at - transactions[j].created_at).total_seconds())
                            if time_diff <= 1800:  # 30 minutes
                                duplicate_groups.append({
                                    "type": "cashout_debit_duplicates",
                                    "user_id": user_id,
                                    "amount": amount,
                                    "time_difference_seconds": time_diff,
                                    "transactions": [
                                        {
                                            "transaction_id": t.transaction_id,
                                            "created_at": t.created_at.isoformat(),
                                            "description": t.description
                                        } for t in [transactions[i], transactions[j]]
                                    ]
                                })
                                break
            
            if duplicate_groups:
                logger.warning(f"⚠️ Found {len(duplicate_groups)} potential duplicate cashout debit groups")
            
            return duplicate_groups
            
        except Exception as e:
            logger.error(f"❌ Error finding duplicate cashout debits: {e}")
            return []
    
    @staticmethod
    async def _find_duplicate_refunds(session, since_date: datetime) -> List[Dict[str, Any]]:
        """Find potential duplicate refund transactions"""
        try:
            # Query for refund transactions grouped by user and amount
            refund_transactions = session.query(Transaction).filter(
                Transaction.transaction_type == TransactionType.REFUND.value,
                Transaction.currency == "USD",
                Transaction.amount > 0,  # Credits (refunds)
                Transaction.created_at >= since_date
            ).order_by(Transaction.user_id, Transaction.amount, Transaction.created_at).all()
            
            duplicate_groups = []
            
            # Group by user_id and amount
            user_amount_groups = {}
            for transaction in refund_transactions:
                key = (transaction.user_id, str(transaction.amount))
                if key not in user_amount_groups:
                    user_amount_groups[key] = []
                user_amount_groups[key].append(transaction)
            
            # Find groups with potential duplicates
            for (user_id, amount), transactions in user_amount_groups.items():
                if len(transactions) > 1:
                    # Check for similar descriptions (potential same cashout refunds)
                    descriptions = [t.description for t in transactions]
                    if any("cashout" in desc.lower() for desc in descriptions):
                        duplicate_groups.append({
                            "type": "refund_duplicates",
                            "user_id": user_id,
                            "amount": amount,
                            "count": len(transactions),
                            "transactions": [
                                {
                                    "transaction_id": t.transaction_id,
                                    "created_at": t.created_at.isoformat(),
                                    "description": t.description
                                } for t in transactions
                            ]
                        })
            
            if duplicate_groups:
                logger.warning(f"⚠️ Found {len(duplicate_groups)} potential duplicate refund groups")
            
            return duplicate_groups
            
        except Exception as e:
            logger.error(f"❌ Error finding duplicate refunds: {e}")
            return []
    
    @staticmethod
    async def _find_refund_matching_issues(session, since_date: datetime) -> List[Dict[str, Any]]:
        """Find patterns that could cause refund matching issues"""
        try:
            issues = []
            
            # Find failed cashouts that have multiple potential matching debits
            failed_cashouts = session.query(Cashout).filter(
                Cashout.status == "failed",
                Cashout.updated_at >= since_date
            ).all()
            
            for cashout in failed_cashouts[:10]:  # Limit to prevent performance issues
                # Count potential matching debit transactions
                time_window_start = cashout.created_at - timedelta(minutes=15)
                time_window_end = cashout.created_at + timedelta(minutes=15)
                
                matching_debits = session.query(Transaction).filter(
                    Transaction.user_id == cashout.user_id,
                    Transaction.transaction_type == "cashout",
                    Transaction.currency == "USD",
                    Transaction.amount < 0,
                    Transaction.created_at >= time_window_start,
                    Transaction.created_at <= time_window_end
                ).count()
                
                if matching_debits > 1:
                    issues.append({
                        "type": "multiple_matching_debits",
                        "cashout_id": cashout.cashout_id,
                        "user_id": cashout.user_id,
                        "matching_debit_count": matching_debits,
                        "cashout_created": cashout.created_at.isoformat(),
                        "potential_for_confusion": True
                    })
            
            if issues:
                logger.warning(f"⚠️ Found {len(issues)} refund matching issues")
            
            return issues
            
        except Exception as e:
            logger.error(f"❌ Error finding refund matching issues: {e}")
            return []
    
    @staticmethod
    def _analyze_by_transaction_type(duplicate_groups: List[Dict[str, Any]]) -> Dict[str, int]:
        """Analyze duplicate patterns by transaction type"""
        analysis = {}
        for group in duplicate_groups:
            group_type = group.get("type", "unknown")
            analysis[group_type] = analysis.get(group_type, 0) + 1
        return analysis
    
    @staticmethod
    async def _send_duplicate_alert(results: Dict[str, Any]):
        """Send admin alert about duplicate transaction issues"""
        try:
            alert_message = (
                f"🚨 DUPLICATE_TRANSACTION_ALERT\n\n"
                f"📊 Summary:\n"
                f"• Duplicate groups found: {results['duplicate_groups_found']}\n"
                f"• Total duplicate transactions: {results['total_duplicates']}\n"
                f"• Potential refund issues: {results['potential_refund_issues']}\n\n"
            )
            
            if results.get("by_transaction_type"):
                alert_message += "📈 By type:\n"
                for tx_type, count in results["by_transaction_type"].items():
                    alert_message += f"• {tx_type}: {count} groups\n"
                alert_message += "\n"
            
            if results.get("critical_issues"):
                alert_message += "🔥 Critical Issues:\n"
                for issue in results["critical_issues"][:3]:  # Show first 3
                    if issue["type"] == "multiple_matching_debits":
                        alert_message += (
                            f"• Cashout {issue['cashout_id']}: "
                            f"{issue['matching_debit_count']} matching debits\n"
                        )
                alert_message += "\n"
            
            alert_message += "⚠️ This may impact refund processing accuracy. Monitor closely."
            
            await consolidated_notification_service.send_admin_alert(alert_message)
            
        except Exception as e:
            logger.error(f"❌ Failed to send duplicate transaction alert: {e}")
    
    @staticmethod
    async def _log_potential_data_issue(cashout, debits: List) -> None:
        """Log potential data integrity issues for monitoring"""
        try:
            issue_description = (
                f"DATA_INTEGRITY_ALERT: Cashout {cashout.cashout_id} (user: {cashout.user_id}) "
                f"has no matching debit transaction despite {len(debits)} total debits existing. "
                f"This may indicate transaction creation timing issues or duplicate records."
            )
            
            logger.critical(issue_description)
            
            # Send admin alert for critical data integrity issues
            await consolidated_notification_service.send_admin_alert(
                f"🚨 DATA INTEGRITY ISSUE\n\n{issue_description}"
            )
            
        except Exception as e:
            logger.error(f"❌ Failed to log data integrity issue: {e}")


# Background job function for scheduler
async def scan_duplicate_transactions():
    """Background job to scan for duplicate transactions"""
    monitor = DuplicateTransactionMonitor()
    return await monitor.scan_for_duplicate_transactions()