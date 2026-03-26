"""
Real-Time Refund Progress Tracker with WebSocket Support
Enhanced tracking system for comprehensive refund status management
"""

import logging
import asyncio
import json
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable
from enum import Enum
from dataclasses import dataclass, asdict
from collections import defaultdict


from models import Refund
from database import SessionLocal
from utils.refund_status_tracking import RefundStatusTracker, RefundProgressStatus
from services.unified_refund_notification_service import UnifiedRefundNotificationService

logger = logging.getLogger(__name__)


class ProgressStage(Enum):
    """Enhanced progress stages for detailed tracking"""
    INITIATED = "initiated"                  # Refund request received
    VALIDATING = "validating"               # Checking eligibility and funds
    PROCESSING = "processing"               # Processing the refund
    WALLET_CREDITING = "wallet_crediting"   # Crediting user's wallet
    WALLET_CREDITED = "wallet_credited"     # Wallet successfully credited
    USER_NOTIFYING = "user_notifying"       # Sending notifications to user
    USER_NOTIFIED = "user_notified"         # User notifications sent
    CONFIRMING = "confirming"               # Waiting for user confirmation
    CONFIRMED = "confirmed"                 # User confirmed receipt
    COMPLETED = "completed"                 # Fully completed
    FAILED = "failed"                       # Failed with error
    CANCELLED = "cancelled"                 # Cancelled by user/admin


@dataclass
class ProgressUpdate:
    """Progress update event data structure"""
    refund_id: str
    user_id: int
    stage: ProgressStage
    timestamp: datetime
    details: str
    metadata: Dict[str, Any]
    estimated_completion: Optional[str] = None
    progress_percent: int = 0


@dataclass
class RefundSession:
    """Active refund session tracking"""
    refund_id: str
    user_id: int
    start_time: datetime
    current_stage: ProgressStage
    progress_history: List[ProgressUpdate]
    websocket_clients: List[Any]  # Connected WebSocket clients
    last_notification: Optional[datetime] = None
    user_confirmed: bool = False
    estimated_completion: Optional[datetime] = None


class RealTimeRefundProgressTracker:
    """
    Real-time refund progress tracking with WebSocket support and live updates
    """
    
    def __init__(self):
        self.active_sessions: Dict[str, RefundSession] = {}
        self.websocket_clients: Dict[str, List[Any]] = defaultdict(list)
        self.progress_listeners: List[Callable] = []
        self.notification_service = UnifiedRefundNotificationService()
        self.status_tracker = RefundStatusTracker()
        
        # Performance monitoring
        self.metrics = {
            "total_updates": 0,
            "websocket_messages_sent": 0,
            "notification_delivery_rate": 0.0,
            "average_processing_time": 0.0,
            "active_sessions_count": 0
        }
        
        # Progress stage configurations
        self.stage_config = {
            ProgressStage.INITIATED: {"progress": 5, "estimated_seconds": 10},
            ProgressStage.VALIDATING: {"progress": 15, "estimated_seconds": 20},
            ProgressStage.PROCESSING: {"progress": 35, "estimated_seconds": 30},
            ProgressStage.WALLET_CREDITING: {"progress": 65, "estimated_seconds": 15},
            ProgressStage.WALLET_CREDITED: {"progress": 75, "estimated_seconds": 10},
            ProgressStage.USER_NOTIFYING: {"progress": 85, "estimated_seconds": 10},
            ProgressStage.USER_NOTIFIED: {"progress": 90, "estimated_seconds": 5},
            ProgressStage.CONFIRMING: {"progress": 95, "estimated_seconds": 300},  # 5 minutes for user response
            ProgressStage.CONFIRMED: {"progress": 98, "estimated_seconds": 2},
            ProgressStage.COMPLETED: {"progress": 100, "estimated_seconds": 0},
            ProgressStage.FAILED: {"progress": 0, "estimated_seconds": 0},
            ProgressStage.CANCELLED: {"progress": 0, "estimated_seconds": 0}
        }
        
        logger.info("✅ Real-Time Refund Progress Tracker initialized")
    
    def start_tracking_session(self, refund_id: str, user_id: int, initial_stage: ProgressStage = ProgressStage.INITIATED) -> bool:
        """Start tracking a new refund session"""
        try:
            if refund_id in self.active_sessions:
                logger.warning(f"📊 Refund session {refund_id} already being tracked")
                return True
            
            # Create new session
            session = RefundSession(
                refund_id=refund_id,
                user_id=user_id,
                start_time=datetime.utcnow(),
                current_stage=initial_stage,
                progress_history=[],
                websocket_clients=[],
                estimated_completion=self._calculate_estimated_completion(initial_stage)
            )
            
            self.active_sessions[refund_id] = session
            self.metrics["active_sessions_count"] = len(self.active_sessions)
            
            # Record initial progress update
            asyncio.create_task(self.update_progress(
                refund_id=refund_id,
                stage=initial_stage,
                details="Refund tracking session started",
                metadata={"session_started": True}
            ))
            
            logger.info(f"📊 TRACKING_STARTED: Refund {refund_id} for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error starting tracking session for refund {refund_id}: {e}")
            return False
    
    async def update_progress(
        self,
        refund_id: str,
        stage: ProgressStage,
        details: str,
        metadata: Dict[str, Any] = None,
        notify_user: bool = True
    ) -> bool:
        """Update refund progress and notify all subscribers"""
        try:
            session = self.active_sessions.get(refund_id)
            if not session:
                logger.error(f"❌ No active session found for refund {refund_id}")
                return False
            
            # Create progress update
            progress_update = ProgressUpdate(
                refund_id=refund_id,
                user_id=session.user_id,
                stage=stage,
                timestamp=datetime.utcnow(),
                details=details,
                metadata=metadata or {},
                estimated_completion=self._get_estimated_completion_text(stage),
                progress_percent=self.stage_config[stage]["progress"]
            )
            
            # Update session
            session.current_stage = stage
            session.progress_history.append(progress_update)
            session.estimated_completion = self._calculate_estimated_completion(stage)
            
            # Update metrics
            self.metrics["total_updates"] += 1
            
            # Update RefundStatusTracker for database persistence
            legacy_status = self._map_stage_to_legacy_status(stage)
            self.status_tracker.update_refund_status(refund_id, legacy_status, details)
            
            # Broadcast to WebSocket clients
            await self._broadcast_to_websockets(refund_id, progress_update)
            
            # Send user notifications for key stages
            if notify_user and self._should_notify_user(stage):
                await self._send_progress_notification(session, progress_update)
            
            # Call progress listeners
            for listener in self.progress_listeners:
                try:
                    if asyncio.iscoroutinefunction(listener):
                        await listener(progress_update)
                    else:
                        listener(progress_update)
                except Exception as listener_error:
                    logger.error(f"Error calling progress listener: {listener_error}")
            
            logger.info(
                f"📊 PROGRESS_UPDATE: {refund_id} → {stage.value} "
                f"({progress_update.progress_percent}%) - {details}"
            )
            
            # Auto-complete session if reached final stage
            if stage in [ProgressStage.COMPLETED, ProgressStage.FAILED, ProgressStage.CANCELLED]:
                await self._complete_session(refund_id, stage)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Error updating progress for refund {refund_id}: {e}")
            return False
    
    async def register_websocket_client(self, refund_id: str, websocket_client: Any) -> bool:
        """Register a WebSocket client for real-time updates"""
        try:
            if refund_id in self.active_sessions:
                self.active_sessions[refund_id].websocket_clients.append(websocket_client)
                
                # Send current progress state to new client
                self.active_sessions[refund_id]
                current_progress = self.get_detailed_progress(refund_id)
                
                if current_progress:
                    await self._send_websocket_message(websocket_client, {
                        "type": "initial_state",
                        "data": current_progress
                    })
                
                logger.info(f"📡 WebSocket client registered for refund {refund_id}")
                return True
            else:
                logger.warning(f"❌ Cannot register WebSocket for inactive refund {refund_id}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Error registering WebSocket client: {e}")
            return False
    
    async def unregister_websocket_client(self, refund_id: str, websocket_client: Any) -> bool:
        """Unregister a WebSocket client"""
        try:
            if refund_id in self.active_sessions:
                session = self.active_sessions[refund_id]
                if websocket_client in session.websocket_clients:
                    session.websocket_clients.remove(websocket_client)
                    logger.info(f"📡 WebSocket client unregistered for refund {refund_id}")
                    return True
            return False
            
        except Exception as e:
            logger.error(f"❌ Error unregistering WebSocket client: {e}")
            return False
    
    def get_detailed_progress(self, refund_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed progress information for a refund"""
        try:
            session = self.active_sessions.get(refund_id)
            if not session:
                # Try to get from database via RefundStatusTracker
                return self.status_tracker.get_refund_progress(refund_id)
            
            # Get latest progress update
            latest_update = session.progress_history[-1] if session.progress_history else None
            
            progress_data = {
                "refund_id": refund_id,
                "user_id": session.user_id,
                "current_stage": session.current_stage.value,
                "progress_percent": self.stage_config[session.current_stage]["progress"],
                "start_time": session.start_time.isoformat(),
                "estimated_completion": session.estimated_completion.isoformat() if session.estimated_completion else None,
                "user_confirmed": session.user_confirmed,
                "session_duration_seconds": (datetime.utcnow() - session.start_time).total_seconds(),
                "websocket_clients_count": len(session.websocket_clients),
                "progress_history": [
                    {
                        "stage": update.stage.value,
                        "timestamp": update.timestamp.isoformat(),
                        "details": update.details,
                        "progress_percent": update.progress_percent,
                        "metadata": update.metadata
                    }
                    for update in session.progress_history[-10:]  # Last 10 updates
                ],
                "latest_update": {
                    "stage": latest_update.stage.value,
                    "timestamp": latest_update.timestamp.isoformat(),
                    "details": latest_update.details,
                    "estimated_completion": latest_update.estimated_completion
                } if latest_update else None
            }
            
            return progress_data
            
        except Exception as e:
            logger.error(f"❌ Error getting detailed progress for refund {refund_id}: {e}")
            return None
    
    def get_user_active_refunds(self, user_id: int) -> List[Dict[str, Any]]:
        """Get all active refunds for a user"""
        try:
            user_refunds = []
            for refund_id, session in self.active_sessions.items():
                if session.user_id == user_id:
                    progress_data = self.get_detailed_progress(refund_id)
                    if progress_data:
                        user_refunds.append(progress_data)
            
            return user_refunds
            
        except Exception as e:
            logger.error(f"❌ Error getting active refunds for user {user_id}: {e}")
            return []
    
    def add_progress_listener(self, listener: Callable) -> bool:
        """Add a progress update listener"""
        try:
            self.progress_listeners.append(listener)
            logger.info(f"📡 Progress listener added: {listener.__name__}")
            return True
        except Exception as e:
            logger.error(f"❌ Error adding progress listener: {e}")
            return False
    
    def get_metrics(self) -> Dict[str, Any]:
        """Get performance metrics"""
        return {
            **self.metrics,
            "active_sessions": list(self.active_sessions.keys()),
            "sessions_by_stage": self._get_sessions_by_stage(),
            "average_session_duration": self._calculate_average_session_duration(),
            "last_updated": datetime.utcnow().isoformat()
        }
    
    async def user_confirm_refund(self, refund_id: str, user_id: int) -> bool:
        """User confirms receipt of refund"""
        try:
            session = self.active_sessions.get(refund_id)
            if not session:
                logger.error(f"❌ No active session for refund {refund_id}")
                return False
            
            if session.user_id != user_id:
                logger.error(f"❌ User {user_id} not authorized for refund {refund_id}")
                return False
            
            session.user_confirmed = True
            
            await self.update_progress(
                refund_id=refund_id,
                stage=ProgressStage.CONFIRMED,
                details="User confirmed refund receipt",
                metadata={"confirmation_time": datetime.utcnow().isoformat()},
                notify_user=False  # Don't send notification for user's own action
            )
            
            logger.info(f"✅ User {user_id} confirmed refund {refund_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Error confirming refund {refund_id}: {e}")
            return False
    
    # Private helper methods
    
    def _calculate_estimated_completion(self, current_stage: ProgressStage) -> Optional[datetime]:
        """Calculate estimated completion time based on current stage"""
        try:
            remaining_seconds = 0
            stage_values = list(ProgressStage)
            current_index = stage_values.index(current_stage)
            
            # Sum up estimated seconds for remaining stages
            for stage in stage_values[current_index:]:
                if stage not in [ProgressStage.FAILED, ProgressStage.CANCELLED]:
                    remaining_seconds += self.stage_config[stage]["estimated_seconds"]
            
            return datetime.utcnow() + timedelta(seconds=remaining_seconds)
            
        except Exception as e:
            logger.error(f"❌ Error calculating estimated completion: {e}")
            return None
    
    def _get_estimated_completion_text(self, stage: ProgressStage) -> str:
        """Get user-friendly estimated completion text"""
        estimated_seconds = self.stage_config[stage]["estimated_seconds"]
        
        if estimated_seconds == 0:
            return "Complete"
        elif estimated_seconds < 60:
            return f"{estimated_seconds} seconds"
        elif estimated_seconds < 3600:
            minutes = estimated_seconds // 60
            return f"{minutes} minute{'s' if minutes > 1 else ''}"
        else:
            hours = estimated_seconds // 3600
            return f"{hours} hour{'s' if hours > 1 else ''}"
    
    def _map_stage_to_legacy_status(self, stage: ProgressStage) -> RefundProgressStatus:
        """Map new progress stages to legacy status enum"""
        mapping = {
            ProgressStage.INITIATED: RefundProgressStatus.INITIATED,
            ProgressStage.VALIDATING: RefundProgressStatus.VALIDATING,
            ProgressStage.PROCESSING: RefundProgressStatus.PROCESSING,
            ProgressStage.WALLET_CREDITING: RefundProgressStatus.PROCESSING,
            ProgressStage.WALLET_CREDITED: RefundProgressStatus.COMPLETING,
            ProgressStage.USER_NOTIFYING: RefundProgressStatus.COMPLETING,
            ProgressStage.USER_NOTIFIED: RefundProgressStatus.COMPLETING,
            ProgressStage.CONFIRMING: RefundProgressStatus.COMPLETING,
            ProgressStage.CONFIRMED: RefundProgressStatus.COMPLETED,
            ProgressStage.COMPLETED: RefundProgressStatus.COMPLETED,
            ProgressStage.FAILED: RefundProgressStatus.FAILED,
            ProgressStage.CANCELLED: RefundProgressStatus.CANCELLED
        }
        return mapping.get(stage, RefundProgressStatus.PROCESSING)
    
    def _should_notify_user(self, stage: ProgressStage) -> bool:
        """Determine if user should be notified for this stage"""
        notification_stages = {
            ProgressStage.INITIATED,
            ProgressStage.WALLET_CREDITED,
            ProgressStage.COMPLETED,
            ProgressStage.FAILED
        }
        return stage in notification_stages
    
    async def _send_progress_notification(self, session: RefundSession, progress_update: ProgressUpdate):
        """Send progress notification to user via unified notification service"""
        try:
            # Determine notification template based on stage
            template_map = {
                ProgressStage.INITIATED: "refund_processing_confirmation",
                ProgressStage.WALLET_CREDITED: "refund_processing_confirmation", 
                ProgressStage.COMPLETED: "refund_completed_confirmation",
                ProgressStage.FAILED: "refund_failed_notification"
            }
            
            template_id = template_map.get(progress_update.stage)
            if not template_id:
                return
            
            # Get refund from database for notification context
            with SessionLocal() as db_session:
                refund = db_session.query(Refund).filter(
                    Refund.refund_id == progress_update.refund_id
                ).first()
                
                if refund:
                    await self.notification_service.send_refund_notification(
                        user_id=session.user_id,
                        refund=refund,
                        template_id=template_id,
                        additional_context={
                            "progress_stage": progress_update.stage.value,
                            "progress_percent": progress_update.progress_percent,
                            "estimated_completion": progress_update.estimated_completion,
                            "stage_details": progress_update.details
                        }
                    )
                    
                    session.last_notification = progress_update.timestamp
                    
        except Exception as e:
            logger.error(f"❌ Error sending progress notification: {e}")
    
    async def _broadcast_to_websockets(self, refund_id: str, progress_update: ProgressUpdate):
        """Broadcast progress update to WebSocket clients"""
        try:
            session = self.active_sessions.get(refund_id)
            if not session or not session.websocket_clients:
                return
            
            message = {
                "type": "progress_update",
                "data": asdict(progress_update)
            }
            
            # Convert datetime objects to ISO strings for JSON serialization
            message["data"]["timestamp"] = progress_update.timestamp.isoformat()
            
            # Send to all connected clients
            for client in session.websocket_clients.copy():  # Copy to avoid modification during iteration
                try:
                    await self._send_websocket_message(client, message)
                    self.metrics["websocket_messages_sent"] += 1
                except Exception as client_error:
                    logger.error(f"❌ Error sending to WebSocket client: {client_error}")
                    # Remove failed client
                    try:
                        session.websocket_clients.remove(client)
                    except ValueError:
                        pass  # Client already removed
                        
        except Exception as e:
            logger.error(f"❌ Error broadcasting to WebSockets: {e}")
    
    async def _send_websocket_message(self, client: Any, message: Dict[str, Any]):
        """Send message to individual WebSocket client"""
        try:
            # This would be implemented based on the WebSocket library being used
            # For now, we'll simulate the interface
            message_json = json.dumps(message, default=str)
            # await client.send(message_json)  # Actual implementation would depend on WebSocket library
            logger.debug(f"📡 WebSocket message sent: {message_json[:100]}...")
        except Exception as e:
            logger.error(f"❌ Error sending WebSocket message: {e}")
            raise
    
    async def _complete_session(self, refund_id: str, final_stage: ProgressStage):
        """Complete and cleanup a refund tracking session"""
        try:
            session = self.active_sessions.get(refund_id)
            if not session:
                return
            
            # Calculate final metrics
            session_duration = (datetime.utcnow() - session.start_time).total_seconds()
            
            # Log completion
            logger.info(
                f"🏁 TRACKING_COMPLETED: Refund {refund_id} finished with {final_stage.value} "
                f"after {session_duration:.1f}s"
            )
            
            # Close WebSocket connections
            for client in session.websocket_clients:
                try:
                    await self._send_websocket_message(client, {
                        "type": "session_completed",
                        "data": {
                            "refund_id": refund_id,
                            "final_stage": final_stage.value,
                            "session_duration": session_duration
                        }
                    })
                except Exception as client_error:
                    logger.error(f"❌ Error notifying WebSocket client of completion: {client_error}")
            
            # Archive session (keep for some time for analytics)
            # TODO: Consider moving to session archive instead of immediate deletion
            
            # Remove from active sessions
            del self.active_sessions[refund_id]
            self.metrics["active_sessions_count"] = len(self.active_sessions)
            
        except Exception as e:
            logger.error(f"❌ Error completing session for refund {refund_id}: {e}")
    
    def _get_sessions_by_stage(self) -> Dict[str, int]:
        """Get count of sessions by current stage"""
        stage_counts = defaultdict(int)
        for session in self.active_sessions.values():
            stage_counts[session.current_stage.value] += 1
        return dict(stage_counts)
    
    def _calculate_average_session_duration(self) -> float:
        """Calculate average duration of active sessions"""
        if not self.active_sessions:
            return 0.0
        
        current_time = datetime.utcnow()
        total_duration = sum(
            (current_time - session.start_time).total_seconds()
            for session in self.active_sessions.values()
        )
        
        return total_duration / len(self.active_sessions)


# Global tracker instance
real_time_refund_tracker = RealTimeRefundProgressTracker()