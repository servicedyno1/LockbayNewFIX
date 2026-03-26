"""Admin controls and configuration for withdrawal monitoring system"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from database import get_session
from models import Cashout, CashoutStatus, User
from services.withdrawal_status_monitor import withdrawal_monitor
from services.withdrawal_error_handler import error_handler
from config import Config

logger = logging.getLogger(__name__)


class WithdrawalAdminControls:
    """Admin controls for withdrawal monitoring and transaction hash system"""
    
    def __init__(self):
        self.monitoring_enabled = True
        self.monitoring_interval_minutes = 5
        self.notification_enabled = True
        self.max_monitoring_days = 7
    
    async def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status for admin dashboard"""
        try:
            with get_session() as session:
                # Get monitoring statistics
                stats = await withdrawal_monitor.get_monitoring_stats()
                
                # Get circuit breaker status
                circuit_status = error_handler.get_circuit_breaker_status()
                
                # Get rate limiting status
                rate_status = error_handler.get_rate_limiting_status()
                
                # Get recent activity
                recent_activity = await self._get_recent_activity(session)
                
                # Get pending notifications
                pending_notifications = await self._get_pending_notifications(session)
                
                return {
                    'system_status': 'healthy',
                    'monitoring_enabled': self.monitoring_enabled,
                    'notification_enabled': self.notification_enabled,
                    'monitoring_stats': stats,
                    'circuit_breakers': circuit_status,
                    'rate_limiting': rate_status,
                    'recent_activity': recent_activity,
                    'pending_notifications': pending_notifications,
                    'config': {
                        'monitoring_interval_minutes': self.monitoring_interval_minutes,
                        'max_monitoring_days': self.max_monitoring_days,
                        'streamlined_processing_enabled': True,
                        'admin_notifications_enabled': Config.CASHOUT_ADMIN_NOTIFICATIONS
                    }
                }
                
        except Exception as e:
            logger.error(f"Error getting system status: {str(e)}")
            return {'error': str(e)}
    
    async def _get_recent_activity(self, session: Session) -> List[Dict]:
        """Get recent withdrawal monitoring activity"""
        try:
            # Get cashouts updated in the last 24 hours with blockchain hashes
            recent_updates = session.query(Cashout).filter(
                and_(
                    Cashout.blockchain_tx_hash.isnot(None),
                    Cashout.updated_at >= datetime.utcnow() - timedelta(hours=24)
                )
            ).order_by(Cashout.updated_at.desc()).limit(20).all()
            
            activity = []
            for cashout in recent_updates:
                activity.append({
                    'cashout_id': cashout.cashout_id,
                    'user_id': cashout.user_id,
                    'amount': float(cashout.amount),
                    'currency': cashout.currency,
                    'status': cashout.status,
                    'blockchain_hash': cashout.blockchain_tx_hash[:8] + '...' if cashout.blockchain_tx_hash else None,
                    'notification_sent': cashout.tx_hash_notification_sent,
                    'updated_at': cashout.updated_at.isoformat(),
                    'completed_at': cashout.completed_at.isoformat() if cashout.completed_at else None
                })
            
            return activity
            
        except Exception as e:
            logger.error(f"Error getting recent activity: {str(e)}")
            return []
    
    async def _get_pending_notifications(self, session: Session) -> List[Dict]:
        """Get cashouts with blockchain hashes but no notifications sent"""
        try:
            pending = session.query(Cashout).filter(
                and_(
                    Cashout.blockchain_tx_hash.isnot(None),
                    Cashout.tx_hash_notification_sent == False
                )
            ).order_by(Cashout.completed_at.desc()).limit(10).all()
            
            notifications = []
            for cashout in pending:
                user = session.query(User).filter(User.id == cashout.user_id).first()
                notifications.append({
                    'cashout_id': cashout.cashout_id,
                    'user_id': cashout.user_id,
                    'user_telegram_id': user.telegram_id if user else None,
                    'amount': float(cashout.amount),
                    'currency': cashout.currency,
                    'blockchain_hash': cashout.blockchain_tx_hash[:8] + '...' if cashout.blockchain_tx_hash else None,
                    'completed_at': cashout.completed_at.isoformat() if cashout.completed_at else None
                })
            
            return notifications
            
        except Exception as e:
            logger.error(f"Error getting pending notifications: {str(e)}")
            return []
    
    async def toggle_monitoring(self, enabled: bool) -> Dict[str, Any]:
        """Enable or disable withdrawal monitoring"""
        try:
            self.monitoring_enabled = enabled
            
            if enabled:
                # Restart monitoring
                await withdrawal_monitor.initialize_monitoring()
                message = "Withdrawal monitoring enabled"
            else:
                # Stop monitoring (implementation depends on job service)
                message = "Withdrawal monitoring disabled"
            
            logger.info(f"Admin action: {message}")
            
            return {
                'success': True,
                'message': message,
                'monitoring_enabled': self.monitoring_enabled
            }
            
        except Exception as e:
            logger.error(f"Error toggling monitoring: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    async def toggle_notifications(self, enabled: bool) -> Dict[str, Any]:
        """Enable or disable customer notifications"""
        try:
            self.notification_enabled = enabled
            
            message = f"Customer notifications {'enabled' if enabled else 'disabled'}"
            logger.info(f"Admin action: {message}")
            
            return {
                'success': True,
                'message': message,
                'notification_enabled': self.notification_enabled
            }
            
        except Exception as e:
            logger.error(f"Error toggling notifications: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    async def check_specific_withdrawal(self, cashout_id: str) -> Dict[str, Any]:
        """Manually check status of specific withdrawal"""
        try:
            result = await withdrawal_monitor.check_specific_withdrawal(cashout_id)
            logger.info(f"Admin manual check for withdrawal {cashout_id}")
            return result
            
        except Exception as e:
            logger.error(f"Error checking specific withdrawal {cashout_id}: {str(e)}")
            return {'error': str(e)}
    
    async def resend_notification(self, cashout_id: str) -> Dict[str, Any]:
        """Resend notification for a specific cashout"""
        try:
            with get_session() as session:
                cashout = session.query(Cashout).filter(
                    Cashout.cashout_id == cashout_id
                ).first()
                
                if not cashout:
                    return {'error': f'Cashout {cashout_id} not found'}
                
                if not cashout.blockchain_tx_hash:
                    return {'error': f'Cashout {cashout_id} has no blockchain hash yet'}
                
                user = session.query(User).filter(User.id == cashout.user_id).first()
                if not user:
                    return {'error': f'User not found for cashout {cashout_id}'}
                
                # Send notification
                from services.withdrawal_notification_service import withdrawal_notification
                success = await withdrawal_notification.send_withdrawal_completion_notification(
                    user_id=user.telegram_id,
                    cashout_id=cashout.cashout_id,
                    amount=float(cashout.amount),
                    currency=cashout.currency,
                    blockchain_hash=cashout.blockchain_tx_hash,
                    user_email=user.email if user.email else None
                )
                
                if success:
                    # Mark as notified
                    cashout.tx_hash_notification_sent = True
                    session.commit()
                    
                    logger.info(f"Admin resent notification for cashout {cashout_id}")
                    return {
                        'success': True,
                        'message': f'Notification resent for {cashout_id}'
                    }
                else:
                    return {'error': f'Failed to send notification for {cashout_id}'}
                
        except Exception as e:
            logger.error(f"Error resending notification for {cashout_id}: {str(e)}")
            return {'error': str(e)}
    
    async def get_withdrawal_queue(self) -> Dict[str, Any]:
        """Get current withdrawal monitoring queue"""
        try:
            with get_session() as session:
                # Get cashouts being monitored
                cutoff_date = datetime.utcnow() - timedelta(days=self.max_monitoring_days)
                
                monitoring_queue = session.query(Cashout).filter(
                    and_(
                        Cashout.kraken_withdrawal_id.isnot(None),
                        Cashout.blockchain_tx_hash.is_(None),
                        Cashout.tx_hash_notification_sent == False,
                        or_(
                            Cashout.status == CashoutStatus.EXECUTING.value,
                            Cashout.status == CashoutStatus.COMPLETED.value
                        ),
                        Cashout.created_at >= cutoff_date
                    )
                ).order_by(Cashout.created_at.desc()).all()
                
                queue_items = []
                for cashout in monitoring_queue:
                    user = session.query(User).filter(User.id == cashout.user_id).first()
                    queue_items.append({
                        'cashout_id': cashout.cashout_id,
                        'user_id': cashout.user_id,
                        'user_telegram_id': user.telegram_id if user else None,
                        'amount': float(cashout.amount),
                        'currency': cashout.currency,
                        'status': cashout.status,
                        'kraken_withdrawal_id': cashout.kraken_withdrawal_id,
                        'created_at': cashout.created_at.isoformat(),
                        'hours_since_creation': (datetime.utcnow() - cashout.created_at).total_seconds() / 3600
                    })
                
                return {
                    'queue_size': len(queue_items),
                    'queue_items': queue_items,
                    'monitoring_enabled': self.monitoring_enabled
                }
                
        except Exception as e:
            logger.error(f"Error getting withdrawal queue: {str(e)}")
            return {'error': str(e)}
    
    async def reset_circuit_breaker(self, service_name: str) -> Dict[str, Any]:
        """Reset circuit breaker for a specific service"""
        try:
            success = error_handler.reset_circuit_breaker(service_name)
            
            if success:
                logger.info(f"Admin reset circuit breaker for {service_name}")
                return {
                    'success': True,
                    'message': f'Circuit breaker reset for {service_name}'
                }
            else:
                return {'error': f'Circuit breaker {service_name} not found'}
                
        except Exception as e:
            logger.error(f"Error resetting circuit breaker {service_name}: {str(e)}")
            return {'error': str(e)}
    
    async def update_monitoring_config(
        self,
        interval_minutes: Optional[int] = None,
        max_days: Optional[int] = None
    ) -> Dict[str, Any]:
        """Update monitoring configuration"""
        try:
            updates = {}
            
            if interval_minutes is not None:
                self.monitoring_interval_minutes = interval_minutes
                updates['monitoring_interval_minutes'] = interval_minutes
                
            if max_days is not None:
                self.max_monitoring_days = max_days
                updates['max_monitoring_days'] = max_days
            
            if updates:
                logger.info(f"Admin updated monitoring config: {updates}")
                
                # Restart monitoring with new config
                if self.monitoring_enabled:
                    await withdrawal_monitor.initialize_monitoring()
            
            return {
                'success': True,
                'message': 'Monitoring configuration updated',
                'updates': updates,
                'current_config': {
                    'monitoring_interval_minutes': self.monitoring_interval_minutes,
                    'max_monitoring_days': self.max_monitoring_days
                }
            }
            
        except Exception as e:
            logger.error(f"Error updating monitoring config: {str(e)}")
            return {'error': str(e)}
    
    async def force_monitoring_cycle(self) -> Dict[str, Any]:
        """Manually trigger a monitoring cycle"""
        try:
            logger.info("Admin triggered manual monitoring cycle")
            result = await withdrawal_monitor.check_pending_withdrawals()
            
            return {
                'success': True,
                'message': 'Manual monitoring cycle completed',
                'result': result
            }
            
        except Exception as e:
            logger.error(f"Error in manual monitoring cycle: {str(e)}")
            return {'error': str(e)}
    
    async def get_system_health(self) -> Dict[str, Any]:
        """Get detailed system health information"""
        try:
            # Check database connectivity
            db_healthy = True
            try:
                with get_session() as session:
                    session.execute("SELECT 1")
            except Exception:
                db_healthy = False
            
            # Check external service status
            circuit_status = error_handler.get_circuit_breaker_status()
            external_services_healthy = all(
                status['state'] != 'open' for status in circuit_status.values()
            )
            
            # Check monitoring status
            stats = await withdrawal_monitor.get_monitoring_stats()
            
            overall_health = (
                db_healthy and 
                external_services_healthy and 
                self.monitoring_enabled
            )
            
            return {
                'overall_health': 'healthy' if overall_health else 'degraded',
                'database_healthy': db_healthy,
                'external_services_healthy': external_services_healthy,
                'monitoring_enabled': self.monitoring_enabled,
                'notification_enabled': self.notification_enabled,
                'circuit_breakers': circuit_status,
                'monitoring_stats': stats,
                'timestamp': datetime.utcnow().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error getting system health: {str(e)}")
            return {
                'overall_health': 'error',
                'error': str(e),
                'timestamp': datetime.utcnow().isoformat()
            }


# Global instance for use across the application
admin_controls = WithdrawalAdminControls()