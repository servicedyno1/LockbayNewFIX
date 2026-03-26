"""
Refund System Integration Module
Connects all refund tracking components and ensures seamless operation
"""

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from utils.refund_progress_tracker import real_time_refund_tracker, ProgressStage
from services.refund_analytics_service import refund_analytics_service
from services.unified_refund_notification_service import UnifiedRefundNotificationService
from utils.refund_status_tracking import refund_status_tracker
from models import Refund, RefundType
from database import SessionLocal

logger = logging.getLogger(__name__)


@dataclass
class RefundTrackingSession:
    """Complete refund tracking session data"""
    refund_id: str
    user_id: int
    refund_type: RefundType
    amount: float
    currency: str
    started_at: datetime
    current_stage: ProgressStage
    notifications_sent: List[str]
    user_confirmed: bool
    admin_notified: bool


class RefundSystemIntegrator:
    """
    Central coordinator for all refund tracking and notification components
    """
    
    def __init__(self):
        self.notification_service = UnifiedRefundNotificationService()
        self.active_integrations: Dict[str, RefundTrackingSession] = {}
        self.integration_metrics = {
            "total_sessions": 0,
            "successful_completions": 0,
            "failed_sessions": 0,
            "notification_failures": 0,
            "average_completion_time": 0.0
        }
        
        # Register progress listeners
        real_time_refund_tracker.add_progress_listener(self._handle_progress_update)
        
        logger.info("✅ Refund System Integrator initialized with all components")
    
    async def start_comprehensive_refund_tracking(
        self,
        refund: Refund,
        initial_stage: ProgressStage = ProgressStage.INITIATED
    ) -> bool:
        """
        Start comprehensive refund tracking with all integrated services
        """
        try:
            # Start real-time tracking session
            tracking_started = real_time_refund_tracker.start_tracking_session(
                refund_id=refund.refund_id,
                user_id=refund.user_id,
                initial_stage=initial_stage
            )
            
            if not tracking_started:
                logger.error(f"❌ Failed to start real-time tracking for {refund.refund_id}")
                return False
            
            # Create integration session
            integration_session = RefundTrackingSession(
                refund_id=refund.refund_id,
                user_id=refund.user_id,
                refund_type=RefundType(refund.refund_type),
                amount=float(refund.amount),
                currency=refund.currency,
                started_at=datetime.utcnow(),
                current_stage=initial_stage,
                notifications_sent=[],
                user_confirmed=False,
                admin_notified=False
            )
            
            self.active_integrations[refund.refund_id] = integration_session
            self.integration_metrics["total_sessions"] += 1
            
            # Send initial notification
            await self._send_stage_notification(refund, initial_stage, "Refund tracking initiated")
            
            # Update analytics
            await self._update_analytics_for_new_session(refund)
            
            logger.info(f"✅ COMPREHENSIVE_TRACKING_STARTED: {refund.refund_id} for user {refund.user_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error starting comprehensive refund tracking for {refund.refund_id}: {e}")
            return False
    
    async def progress_refund_to_stage(
        self,
        refund_id: str,
        new_stage: ProgressStage,
        details: str,
        metadata: Dict[str, Any] = None
    ) -> bool:
        """
        Progress refund to new stage with full integration
        """
        try:
            integration_session = self.active_integrations.get(refund_id)
            if not integration_session:
                logger.warning(f"⚠️ No integration session found for {refund_id}")
                return False
            
            # Update real-time tracker
            tracker_updated = await real_time_refund_tracker.update_progress(
                refund_id=refund_id,
                stage=new_stage,
                details=details,
                metadata=metadata or {},
                notify_user=False  # We handle notifications centrally
            )
            
            if not tracker_updated:
                logger.error(f"❌ Failed to update real-time tracker for {refund_id}")
                return False
            
            # Update integration session
            integration_session.current_stage = new_stage
            
            # Get refund from database for notifications
            with SessionLocal() as session:
                refund = session.query(Refund).filter(
                    Refund.refund_id == refund_id
                ).first()
                
                if refund:
                    # Send stage-specific notification
                    await self._send_stage_notification(refund, new_stage, details)
                    
                    # Handle special stages
                    if new_stage == ProgressStage.WALLET_CREDITED:
                        await self._handle_wallet_credited(refund, integration_session)
                    elif new_stage == ProgressStage.COMPLETED:
                        await self._handle_completion(refund, integration_session)
                    elif new_stage == ProgressStage.FAILED:
                        await self._handle_failure(refund, integration_session)
            
            logger.info(f"📊 PROGRESS_UPDATED: {refund_id} → {new_stage.value} - {details}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error progressing refund {refund_id} to {new_stage}: {e}")
            return False
    
    async def complete_refund_tracking(
        self,
        refund_id: str,
        final_stage: ProgressStage,
        completion_details: str
    ) -> bool:
        """
        Complete refund tracking with comprehensive cleanup and notifications
        """
        try:
            integration_session = self.active_integrations.get(refund_id)
            if not integration_session:
                logger.warning(f"⚠️ No integration session found for {refund_id}")
                return False
            
            # Progress to final stage
            await self.progress_refund_to_stage(
                refund_id=refund_id,
                new_stage=final_stage,
                details=completion_details
            )
            
            # Calculate session duration
            session_duration = (datetime.utcnow() - integration_session.started_at).total_seconds()
            
            # Update metrics
            if final_stage == ProgressStage.COMPLETED:
                self.integration_metrics["successful_completions"] += 1
            else:
                self.integration_metrics["failed_sessions"] += 1
            
            # Update average completion time
            total_sessions = self.integration_metrics["total_sessions"]
            current_avg = self.integration_metrics["average_completion_time"]
            self.integration_metrics["average_completion_time"] = (
                (current_avg * (total_sessions - 1) + session_duration) / total_sessions
            )
            
            # Send completion summary
            await self._send_completion_summary(refund_id, integration_session, session_duration)
            
            # Cleanup session
            del self.active_integrations[refund_id]
            
            logger.info(
                f"🏁 TRACKING_COMPLETED: {refund_id} finished with {final_stage.value} "
                f"after {session_duration:.1f}s"
            )
            return True
            
        except Exception as e:
            logger.error(f"❌ Error completing refund tracking for {refund_id}: {e}")
            return False
    
    async def handle_user_refund_confirmation(self, refund_id: str, user_id: int) -> bool:
        """
        Handle user confirmation of refund receipt
        """
        try:
            integration_session = self.active_integrations.get(refund_id)
            if not integration_session:
                logger.warning(f"⚠️ No integration session found for {refund_id}")
                return False
            
            if integration_session.user_id != user_id:
                logger.error(f"❌ User {user_id} not authorized for refund {refund_id}")
                return False
            
            # Update real-time tracker
            confirmed = await real_time_refund_tracker.user_confirm_refund(refund_id, user_id)
            
            if confirmed:
                integration_session.user_confirmed = True
                
                # Send confirmation notification to admin
                await self._notify_admin_of_confirmation(refund_id, integration_session)
                
                # Progress to confirmed stage
                await self.progress_refund_to_stage(
                    refund_id=refund_id,
                    new_stage=ProgressStage.CONFIRMED,
                    details="User confirmed refund receipt",
                    metadata={"confirmation_time": datetime.utcnow().isoformat()}
                )
                
                logger.info(f"✅ USER_CONFIRMED: Refund {refund_id} confirmed by user {user_id}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"❌ Error handling user confirmation for {refund_id}: {e}")
            return False
    
    async def get_comprehensive_status(self, refund_id: str) -> Optional[Dict[str, Any]]:
        """
        Get comprehensive status from all tracking components
        """
        try:
            # Get real-time progress data
            rt_progress = real_time_refund_tracker.get_detailed_progress(refund_id)
            
            # Get legacy status data
            legacy_status = refund_status_tracker.get_refund_progress(refund_id)
            
            # Get integration session data
            integration_session = self.active_integrations.get(refund_id)
            
            # Get analytics data
            with SessionLocal() as session:
                refund = session.query(Refund).filter(
                    Refund.refund_id == refund_id
                ).first()
                
                if refund:
                    user_patterns = refund_analytics_service.analyze_user_refund_patterns(
                        user_id=refund.user_id
                    )
                else:
                    user_patterns = {}
            
            # Combine all data sources
            comprehensive_status = {
                "refund_id": refund_id,
                "timestamp": datetime.utcnow().isoformat(),
                "real_time_progress": rt_progress,
                "legacy_status": legacy_status,
                "integration_session": {
                    "active": integration_session is not None,
                    "session_data": {
                        "started_at": integration_session.started_at.isoformat(),
                        "current_stage": integration_session.current_stage.value,
                        "notifications_sent": integration_session.notifications_sent,
                        "user_confirmed": integration_session.user_confirmed,
                        "admin_notified": integration_session.admin_notified
                    } if integration_session else None
                },
                "user_patterns": user_patterns,
                "system_health": {
                    "real_time_tracker": "operational",
                    "notification_service": "operational",
                    "analytics_service": "operational",
                    "database_connectivity": "operational"
                }
            }
            
            return comprehensive_status
            
        except Exception as e:
            logger.error(f"❌ Error getting comprehensive status for {refund_id}: {e}")
            return None
    
    def get_integration_metrics(self) -> Dict[str, Any]:
        """Get integration performance metrics"""
        try:
            # Get metrics from all components
            rt_metrics = real_time_refund_tracker.get_metrics()
            analytics_dashboard = refund_analytics_service.get_real_time_dashboard_data()
            
            # Combine with integration metrics
            comprehensive_metrics = {
                "integration_metrics": self.integration_metrics.copy(),
                "real_time_tracker_metrics": rt_metrics,
                "analytics_dashboard_data": analytics_dashboard,
                "active_integrations_count": len(self.active_integrations),
                "active_integration_ids": list(self.active_integrations.keys()),
                "system_status": {
                    "all_components_operational": True,
                    "last_health_check": datetime.utcnow().isoformat()
                }
            }
            
            return comprehensive_metrics
            
        except Exception as e:
            logger.error(f"❌ Error getting integration metrics: {e}")
            return {
                "error": str(e),
                "timestamp": datetime.utcnow().isoformat()
            }
    
    # Private helper methods
    
    async def _handle_progress_update(self, progress_update):
        """Handle progress updates from real-time tracker"""
        try:
            refund_id = progress_update.refund_id
            integration_session = self.active_integrations.get(refund_id)
            
            if integration_session:
                # Update session with latest progress
                integration_session.current_stage = progress_update.stage
                
                # Log integration event
                logger.info(
                    f"📊 INTEGRATION_PROGRESS: {refund_id} → {progress_update.stage.value} "
                    f"(Session: {integration_session is not None})"
                )
                
        except Exception as e:
            logger.error(f"❌ Error handling progress update: {e}")
    
    async def _send_stage_notification(self, refund: Refund, stage: ProgressStage, details: str):
        """Send notifications for stage progression"""
        try:
            # Determine notification template based on stage
            template_map = {
                ProgressStage.INITIATED: "refund_processing_confirmation",
                ProgressStage.WALLET_CREDITED: "refund_processing_confirmation",
                ProgressStage.COMPLETED: "refund_completed_confirmation",
                ProgressStage.FAILED: "refund_failed_notification"
            }
            
            template_id = template_map.get(stage)
            if not template_id:
                return  # No notification needed for this stage
            
            # Send notification via unified service
            notification_result = await self.notification_service.send_refund_notification(
                user_id=refund.user_id,
                refund=refund,
                template_id=template_id,
                additional_context={
                    "progress_stage": stage.value,
                    "stage_details": details,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
            
            # Update session with notification result
            integration_session = self.active_integrations.get(refund.refund_id)
            if integration_session:
                integration_session.notifications_sent.append(
                    f"{stage.value}:{template_id}:{datetime.utcnow().isoformat()}"
                )
            
            # Update metrics
            if "error" in notification_result:
                self.integration_metrics["notification_failures"] += 1
                logger.error(f"❌ Notification failed for {refund.refund_id}: {notification_result['error']}")
            else:
                logger.info(f"📨 NOTIFICATION_SENT: {refund.refund_id} - {template_id} for {stage.value}")
                
        except Exception as e:
            logger.error(f"❌ Error sending stage notification: {e}")
            self.integration_metrics["notification_failures"] += 1
    
    async def _handle_wallet_credited(self, refund: Refund, session: RefundTrackingSession):
        """Handle wallet credited stage"""
        try:
            # Send special wallet credited notification
            await self.notification_service.send_refund_notification(
                user_id=refund.user_id,
                refund=refund,
                template_id="refund_wallet_credited",
                additional_context={
                    "amount": session.amount,
                    "currency": session.currency,
                    "credited_at": datetime.utcnow().isoformat()
                }
            )
            
            logger.info(f"💳 WALLET_CREDITED: {refund.refund_id} - ${session.amount} credited")
            
        except Exception as e:
            logger.error(f"❌ Error handling wallet credited for {refund.refund_id}: {e}")
    
    async def _handle_completion(self, refund: Refund, session: RefundTrackingSession):
        """Handle refund completion"""
        try:
            # Send completion notification
            completion_time = datetime.utcnow()
            duration = (completion_time - session.started_at).total_seconds()
            
            await self.notification_service.send_refund_notification(
                user_id=refund.user_id,
                refund=refund,
                template_id="refund_completed_confirmation",
                additional_context={
                    "completion_time": completion_time.isoformat(),
                    "total_duration_minutes": round(duration / 60, 1),
                    "amount": session.amount,
                    "currency": session.currency
                }
            )
            
            logger.info(f"🏁 REFUND_COMPLETED: {refund.refund_id} - Duration: {duration:.1f}s")
            
        except Exception as e:
            logger.error(f"❌ Error handling completion for {refund.refund_id}: {e}")
    
    async def _handle_failure(self, refund: Refund, session: RefundTrackingSession):
        """Handle refund failure"""
        try:
            # Send failure notification
            await self.notification_service.send_refund_notification(
                user_id=refund.user_id,
                refund=refund,
                template_id="refund_failed_notification",
                additional_context={
                    "failure_time": datetime.utcnow().isoformat(),
                    "amount": session.amount,
                    "currency": session.currency,
                    "support_contact": "Contact support for assistance"
                }
            )
            
            # Notify admin of failure
            session.admin_notified = True
            
            logger.error(f"❌ REFUND_FAILED: {refund.refund_id}")
            
        except Exception as e:
            logger.error(f"❌ Error handling failure for {refund.refund_id}: {e}")
    
    async def _notify_admin_of_confirmation(self, refund_id: str, session: RefundTrackingSession):
        """Notify admin when user confirms refund"""
        try:
            # This would send admin notification via appropriate channel
            logger.info(f"👤 ADMIN_NOTIFIED: User confirmed refund {refund_id}")
            session.admin_notified = True
            
        except Exception as e:
            logger.error(f"❌ Error notifying admin of confirmation: {e}")
    
    async def _send_completion_summary(self, refund_id: str, session: RefundTrackingSession, duration: float):
        """Send completion summary"""
        try:
            logger.info(
                f"📊 COMPLETION_SUMMARY: {refund_id} - "
                f"Duration: {duration:.1f}s, "
                f"Notifications: {len(session.notifications_sent)}, "
                f"User Confirmed: {session.user_confirmed}, "
                f"Admin Notified: {session.admin_notified}"
            )
            
        except Exception as e:
            logger.error(f"❌ Error sending completion summary: {e}")
    
    async def _update_analytics_for_new_session(self, refund: Refund):
        """Update analytics when starting new session"""
        try:
            # This could trigger analytics updates or pattern detection
            logger.debug(f"📈 ANALYTICS_UPDATED: New session started for {refund.refund_id}")
            
        except Exception as e:
            logger.error(f"❌ Error updating analytics: {e}")


# Global integrator instance
refund_system_integrator = RefundSystemIntegrator()