"""
Universal Auto-Refresh Manager
Automatically refreshes UI components without manual refresh buttons
"""

import logging
from typing import Dict, Any, Callable
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


class AutoRefreshManager:
    """Manages automatic refreshing of UI components across the system"""
    
    def __init__(self):
        self.active_refreshes: Dict[str, Dict[str, Any]] = {}
        self.refresh_intervals = {
            'support_chat': 30,        # Support chats refresh every 30 seconds
            'admin_dashboard': 60,     # Admin dashboards refresh every 60 seconds
            'transactions': 45,        # Transaction lists refresh every 45 seconds
            'wallet_balance': 60,      # Wallet balances refresh every 60 seconds
            'trade_status': 30,        # Trade status refresh every 30 seconds
            'cashout_pending': 30,     # Pending cashouts refresh every 30 seconds
            'session_monitor': 120,    # Session monitoring refresh every 2 minutes
        }
        
    def schedule_auto_refresh(
        self, 
        context: ContextTypes.DEFAULT_TYPE,
        refresh_type: str,
        update: Update,
        refresh_callback: Callable,
        user_id: int,
        component_id: str = None,
        max_refreshes: int = 20,
        custom_interval: int = None
    ) -> None:
        """Schedule automatic refresh for a UI component"""
        try:
            if not hasattr(context, 'job_queue') or not context.job_queue:
                logger.warning("Job queue not available for auto-refresh")
                return
                
            # Create unique refresh key
            refresh_key = f"{refresh_type}_{user_id}"
            if component_id:
                refresh_key += f"_{component_id}"
                
            # Stop existing refresh for this component
            self.stop_auto_refresh(context, refresh_key)
            
            # Get refresh interval
            interval = custom_interval or self.refresh_intervals.get(refresh_type, 60)
            
            # Store refresh info
            self.active_refreshes[refresh_key] = {
                'type': refresh_type,
                'user_id': user_id,
                'component_id': component_id,
                'interval': interval,
                'max_refreshes': max_refreshes,
                'refresh_count': 0,
                'last_refresh': datetime.utcnow(),
                'callback': refresh_callback,
                'update': update
            }
            
            # Schedule first refresh
            context.job_queue.run_once(
                lambda c: self._execute_refresh(c, refresh_key),
                interval,
                name=refresh_key
            )
            
            logger.info(f"✅ Auto-refresh scheduled: {refresh_key} (every {interval}s)")
            
        except Exception as e:
            logger.error(f"Error scheduling auto-refresh: {e}")
    
    def stop_auto_refresh(self, context: ContextTypes.DEFAULT_TYPE, refresh_key: str) -> None:
        """Stop auto-refresh for a specific component"""
        try:
            # Remove from active refreshes
            if refresh_key in self.active_refreshes:
                del self.active_refreshes[refresh_key]
                
            # Remove scheduled job
            if hasattr(context, 'job_queue') and context.job_queue:
                current_jobs = context.job_queue.get_jobs_by_name(refresh_key)
                for job in current_jobs:
                    job.schedule_removal()
                    
            logger.info(f"🛑 Auto-refresh stopped: {refresh_key}")
            
        except Exception as e:
            logger.error(f"Error stopping auto-refresh: {e}")
    
    def stop_user_refreshes(self, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
        """Stop all auto-refreshes for a specific user"""
        try:
            keys_to_remove = []
            for refresh_key, refresh_info in self.active_refreshes.items():
                if refresh_info['user_id'] == user_id:
                    keys_to_remove.append(refresh_key)
                    
            for key in keys_to_remove:
                self.stop_auto_refresh(context, key)
                
            logger.info(f"🛑 All auto-refreshes stopped for user {user_id}")
            
        except Exception as e:
            logger.error(f"Error stopping user refreshes: {e}")
    
    async def _execute_refresh(self, context: ContextTypes.DEFAULT_TYPE, refresh_key: str) -> None:
        """Execute the actual refresh"""
        try:
            if refresh_key not in self.active_refreshes:
                return  # Refresh was cancelled
                
            refresh_info = self.active_refreshes[refresh_key]
            
            # Check if we've exceeded max refreshes
            if refresh_info['refresh_count'] >= refresh_info['max_refreshes']:
                logger.info(f"🔄 Max refreshes reached for {refresh_key}")
                self.stop_auto_refresh(context, refresh_key)
                return
            
            # Execute the refresh callback
            success = await refresh_info['callback'](refresh_info['update'], context)
            
            # Update refresh info
            refresh_info['refresh_count'] += 1
            refresh_info['last_refresh'] = datetime.utcnow()
            
            # Schedule next refresh if successful and not stopped
            if success and refresh_key in self.active_refreshes:
                context.job_queue.run_once(
                    lambda c: self._execute_refresh(c, refresh_key),
                    refresh_info['interval'],
                    name=f"{refresh_key}_{refresh_info['refresh_count']}"
                )
            else:
                # Stop refreshing if callback returned False or refresh was removed
                self.stop_auto_refresh(context, refresh_key)
                
        except Exception as e:
            logger.error(f"Error executing refresh for {refresh_key}: {e}")
            # Don't stop refresh on error, just log it
            
    def get_active_refreshes(self) -> Dict[str, Dict[str, Any]]:
        """Get information about all active refreshes"""
        return self.active_refreshes.copy()
    
    def cleanup_expired_refreshes(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Clean up refreshes that haven't been active for a while"""
        try:
            now = datetime.utcnow()
            expired_keys = []
            
            for refresh_key, refresh_info in self.active_refreshes.items():
                time_since_refresh = now - refresh_info['last_refresh']
                if time_since_refresh > timedelta(minutes=30):  # 30 minutes timeout
                    expired_keys.append(refresh_key)
                    
            for key in expired_keys:
                self.stop_auto_refresh(context, key)
                
            if expired_keys:
                logger.info(f"🧹 Cleaned up {len(expired_keys)} expired auto-refreshes")
                
        except Exception as e:
            logger.error(f"Error cleaning up expired refreshes: {e}")


# Global instance
auto_refresh_manager = AutoRefreshManager()


# Convenience functions for common refresh types
async def schedule_support_chat_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE, ticket_id: int) -> None:
    """Schedule auto-refresh for support chat"""
    from handlers.support_chat import auto_refresh_support_chat
    
    auto_refresh_manager.schedule_auto_refresh(
        context=context,
        refresh_type='support_chat',
        update=update,
        refresh_callback=lambda u, c: auto_refresh_support_chat(u, c, ticket_id),
        user_id=update.effective_user.id,
        component_id=str(ticket_id)
    )


async def schedule_admin_dashboard_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Schedule auto-refresh for admin dashboard"""
    from handlers.admin_support import admin_support_dashboard
    
    auto_refresh_manager.schedule_auto_refresh(
        context=context,
        refresh_type='admin_dashboard',
        update=update,
        refresh_callback=admin_support_dashboard,
        user_id=update.effective_user.id
    )


async def schedule_transaction_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE, transaction_type: str) -> None:
    """Schedule auto-refresh for transaction lists"""
    # This would be implemented for specific transaction handlers
    pass


def stop_all_user_refreshes(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Stop all auto-refreshes for a user (call when user navigates away)"""
    auto_refresh_manager.stop_user_refreshes(context, user_id)