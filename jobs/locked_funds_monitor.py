"""
Locked Funds Monitoring Job
Integrates with the scheduler to automatically detect and alert on locked funds issues
"""

import logging
from typing import Dict, Any
from services.locked_funds_management_service import LockedFundsManagementService
from services.admin_email_alerts import admin_email_alerts

logger = logging.getLogger(__name__)


async def monitor_locked_funds() -> Dict[str, Any]:
    """
    Scheduled job to monitor and alert on locked funds issues
    Runs comprehensive detection and sends alerts to administrators
    """
    try:
        logger.info("🔍 LOCKED_FUNDS_MONITOR: Starting comprehensive scan")
        
        # Run comprehensive locked funds detection
        detection_results = await LockedFundsManagementService.detect_locked_funds_issues()
        
        # Process results and send alerts if necessary
        if detection_results['issues_found']:
            alert_sent = await _send_locked_funds_alert(detection_results)
            
            # Log results for monitoring dashboard
            logger.warning(
                f"🚨 LOCKED_FUNDS_DETECTED: {len(detection_results['issues_found'])} issues, "
                f"severity: {detection_results['severity']}, "
                f"users_affected: {detection_results['users_affected']}, "
                f"total_amount: ${detection_results['total_locked_amount']}"
            )
            
            return {
                'status': 'issues_detected',
                'issues_count': len(detection_results['issues_found']),
                'severity': detection_results['severity'],
                'users_affected': detection_results['users_affected'],
                'total_locked_amount': float(detection_results['total_locked_amount']),
                'alert_sent': alert_sent,
                'timestamp': detection_results['scan_timestamp']
            }
        else:
            logger.info("✅ LOCKED_FUNDS_MONITOR: No issues detected")
            return {
                'status': 'healthy',
                'issues_count': 0,
                'timestamp': detection_results['scan_timestamp']
            }
            
    except Exception as e:
        logger.error(f"❌ LOCKED_FUNDS_MONITOR_FAILED: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'timestamp': detection_results.get('scan_timestamp') if 'detection_results' in locals() else None
        }


async def cleanup_stale_locked_funds() -> Dict[str, Any]:
    """
    Scheduled job to automatically clean up obvious stale locked funds issues
    Only processes low-risk, clear-cut cases automatically
    """
    try:
        logger.info("🧹 STALE_FUNDS_CLEANUP: Starting automatic cleanup")
        
        # First, detect issues to see what we're working with
        detection_results = await LockedFundsManagementService.detect_locked_funds_issues()
        
        if not detection_results['issues_found']:
            return {
                'status': 'no_issues',
                'message': 'No locked funds issues to clean up'
            }
        
        # Filter for issues that are safe to auto-cleanup
        safe_to_cleanup = []
        for issue in detection_results['issues_found']:
            if _is_safe_for_auto_cleanup(issue):
                safe_to_cleanup.append(issue)
        
        if not safe_to_cleanup:
            logger.info(f"🛡️ AUTO_CLEANUP_SKIPPED: {len(detection_results['issues_found'])} issues found but none safe for auto-cleanup")
            return {
                'status': 'manual_review_required',
                'total_issues': len(detection_results['issues_found']),
                'safe_for_auto_cleanup': 0,
                'message': 'All issues require manual review'
            }
        
        # Process safe cleanup cases
        cleanup_results = []
        for issue in safe_to_cleanup:
            if issue['type'] == 'stale_cashout' and issue.get('amount', 0) <= 10.0:  # Only very small amounts
                user_id = issue['user_id']
                result = await LockedFundsManagementService.cleanup_user_locked_funds(
                    user_id=user_id,
                    dry_run=False  # Actually perform cleanup for safe cases
                )
                cleanup_results.append(result)
        
        successful_cleanups = sum(1 for r in cleanup_results if r.get('success', False))
        
        logger.info(f"🧹 AUTO_CLEANUP_COMPLETE: {successful_cleanups}/{len(safe_to_cleanup)} cleanups successful")
        
        return {
            'status': 'completed',
            'total_issues_found': len(detection_results['issues_found']),
            'safe_for_auto_cleanup': len(safe_to_cleanup),
            'successful_cleanups': successful_cleanups,
            'cleanup_results': cleanup_results
        }
        
    except Exception as e:
        logger.error(f"❌ STALE_FUNDS_CLEANUP_FAILED: {e}")
        return {
            'status': 'error',
            'error': str(e)
        }


async def _send_locked_funds_alert(detection_results: Dict[str, Any]) -> bool:
    """Send alert to administrators about locked funds issues"""
    try:
        severity = detection_results['severity']
        issues_count = len(detection_results['issues_found'])
        users_affected = detection_results['users_affected']
        total_amount = detection_results['total_locked_amount']
        
        # Create alert message based on severity
        if severity == 'critical':
            subject = f"🚨 CRITICAL: {issues_count} Locked Funds Issues Detected - ${total_amount}"
            priority = "high"
        elif severity == 'high':
            subject = f"⚠️ HIGH: {issues_count} Locked Funds Issues Detected - ${total_amount}"
            priority = "high"
        else:
            subject = f"ℹ️ MEDIUM: {issues_count} Locked Funds Issues Detected - ${total_amount}"
            priority = "normal"
        
        # Build detailed alert message
        alert_message = f"""
🔍 LOCKED FUNDS MONITORING ALERT

📊 SUMMARY:
• Issues Found: {issues_count}
• Users Affected: {users_affected}
• Total Locked Amount: ${total_amount}
• Severity Level: {severity.upper()}
• Scan Time: {detection_results['scan_timestamp']}

🔧 RECOMMENDED ACTIONS:
"""
        
        # Add specific recommendations
        for recommendation in detection_results.get('cleanup_recommendations', []):
            alert_message += f"• {recommendation}\n"
        
        # Add issue breakdown
        alert_message += "\n📋 ISSUE BREAKDOWN:\n"
        issue_types = {}
        for issue in detection_results['issues_found']:
            issue_type = issue['type']
            if issue_type not in issue_types:
                issue_types[issue_type] = 0
            issue_types[issue_type] += 1
        
        for issue_type, count in issue_types.items():
            alert_message += f"• {issue_type.replace('_', ' ').title()}: {count}\n"
        
        alert_message += "\n🎯 ACTION REQUIRED: Review locked funds issues in admin dashboard or use locked funds management tools."
        
        # Send alert via admin email system
        await admin_email_alerts.send_system_alert(
            subject=subject,
            message=alert_message,
            priority=priority
        )
        
        logger.info(f"📧 LOCKED_FUNDS_ALERT_SENT: {severity} priority alert sent to administrators")
        return True
        
    except Exception as e:
        logger.error(f"❌ Failed to send locked funds alert: {e}")
        return False


def _is_safe_for_auto_cleanup(issue: Dict[str, Any]) -> bool:
    """
    Determine if an issue is safe for automatic cleanup
    Conservative approach - only cleanup obvious, low-risk cases
    """
    try:
        # Only cleanup stale cashouts for now (safest case)
        if issue['type'] != 'stale_cashout':
            return False
        
        # Only cleanup small amounts automatically (safety limit)
        amount = issue.get('amount', 0)
        if amount > 10.0:  # Very conservative limit for auto-cleanup
            return False
        
        # Only cleanup if stuck for a reasonable amount of time
        minutes_stuck = issue.get('minutes_stuck', 0)
        if minutes_stuck < 60:  # Conservative - must be stuck for 1+ hours
            return False
        
        # Don't cleanup if there are concerning patterns
        if issue.get('severity') == 'high':
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"Error determining cleanup safety for issue: {e}")
        return False  # Default to safe (no cleanup) on error


# Export main functions for scheduler
__all__ = [
    'monitor_locked_funds',
    'cleanup_stale_locked_funds'
]