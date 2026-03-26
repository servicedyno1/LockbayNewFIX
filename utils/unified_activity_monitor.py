"""
Unified Real-Time Activity Monitor
Consolidates all user activities, errors, and system events into a single dashboard
"""

import logging
import asyncio
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from collections import deque, defaultdict
from enum import Enum
import uuid

logger = logging.getLogger(__name__)

def json_safe(obj):
    """Recursively convert objects to JSON-serializable format"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, Enum):
        return obj.value
    elif isinstance(obj, (set, tuple, deque)):
        return [json_safe(item) for item in obj]
    elif isinstance(obj, dict):
        return {json_safe_key(k): json_safe(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [json_safe(item) for item in obj]
    elif hasattr(obj, '__dict__'):
        # Handle objects with __dict__ (but not built-in types)
        return str(obj)
    else:
        try:
            # Test if it's already JSON serializable
            json.dumps(obj)
            return obj
        except (TypeError, ValueError):
            return str(obj)

def json_safe_key(key):
    """Convert dictionary keys to JSON-safe strings"""
    if isinstance(key, datetime):
        return key.isoformat()
    elif isinstance(key, Enum):
        return key.value
    else:
        return str(key)

class ActivityType(Enum):
    """Types of activities to monitor"""
    USER_INTERACTION = "user_interaction"
    ERROR_EVENT = "error_event"
    ADMIN_ACTION = "admin_action"
    SYSTEM_EVENT = "system_event"
    FINANCIAL_TRANSACTION = "financial_transaction"
    PERFORMANCE_ALERT = "performance_alert"

class ErrorSeverity(Enum):
    """Error severity levels"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

@dataclass
class ActivityEvent:
    """Unified activity event structure"""
    id: str
    timestamp: datetime
    activity_type: ActivityType
    user_id: Optional[int]
    username: Optional[str]
    is_admin: bool
    title: str
    description: str
    details: Dict[str, Any]
    correlation_id: Optional[str] = None
    trace_id: Optional[str] = None
    error_severity: Optional[ErrorSeverity] = None
    auto_resolved: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        result = asdict(self)
        result['timestamp'] = self.timestamp.isoformat()
        result['activity_type'] = self.activity_type.value
        if self.error_severity:
            result['error_severity'] = self.error_severity.value
        # Ensure details are JSON-safe (handles datetime keys/values)
        result['details'] = json_safe(self.details)
        return result

@dataclass
class ErrorCorrelation:
    """Correlate user-facing errors with backend errors"""
    correlation_id: str
    user_message: str
    backend_error: str
    user_id: Optional[int]
    timestamp: datetime
    handler_name: str
    callback_data: Optional[str] = None
    resolved: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        result = asdict(self)
        result['timestamp'] = self.timestamp.isoformat()
        return result

class UnifiedActivityMonitor:
    """Centralized activity monitoring system"""
    
    def __init__(self):
        self.activities = deque(maxlen=1000)  # Keep last 1000 activities
        self.error_correlations = deque(maxlen=500)  # Keep last 500 error correlations
        self.active_users = {}  # Track currently active users
        self.admin_subscribers = set()  # WebSocket connections for admin dashboard
        self.is_running = False
        
        # Load recent activities from database on startup
        self._load_recent_activities_from_database()
        
        # Performance tracking
        self.user_activity_count = defaultdict(int)
        self.error_patterns = defaultdict(list)
        self.system_health = {
            'status': 'healthy',
            'last_check': datetime.now().isoformat(),
            'active_users': 0,
            'errors_per_hour': 0,
            'response_time': 0,
            'memory_usage_mb': 0.0,
            'cpu_usage_percent': 0.0
        }
        
        # Start real-time metrics collection
        self._start_system_metrics_collection()
        
        logger.info("🔧 Unified Activity Monitor initialized")
    
    def _load_recent_activities_from_database(self):
        """Load recent activities from database to maintain continuity across restarts"""
        try:
            from database import SessionLocal
            from models import User
            from datetime import datetime, timedelta
            
            # Load activities from last 2 hours to maintain context
            cutoff_time = datetime.now() - timedelta(hours=2)
            
            session = SessionLocal()
            
            # Get recent user activities from audit logs (simplified)
            recent_users = session.query(User).filter(
                User.last_activity >= cutoff_time
            ).order_by(User.last_activity.desc()).limit(50).all()
            
            # Add recent user activities as system events to show in dashboard
            # Note: The query already filters by cutoff_time, so no need for redundant check
            for user in recent_users:
                # Extract values to help type checker understand these are not Column objects
                last_activity: datetime = user.last_activity  # type: ignore
                telegram_id: int = user.telegram_id  # type: ignore
                username_val: str = user.username or f"User{telegram_id}"  # type: ignore
                
                activity = ActivityEvent(
                    id=str(uuid.uuid4()),
                    timestamp=last_activity,
                    activity_type=ActivityType.USER_INTERACTION,
                    user_id=telegram_id,
                    username=username_val,
                    is_admin=False,
                    title="Recent User Activity",
                    description="User was active recently (loaded from DB)",
                    details={
                        "loaded_from_db": True,
                        "last_activity": last_activity.isoformat(),
                        "user_id": user.id
                    }
                )
                self.activities.appendleft(activity)
            
            logger.info(f"✅ Loaded {len(recent_users)} recent user activities from database")
            
            session.close()
                
        except Exception as e:
            logger.warning(f"Could not load previous activities from database: {e}")
            # Continue without previous data - not critical
    
    def _start_system_metrics_collection(self):
        """Start background system metrics collection"""
        try:
            # Check if event loop is running
            loop = asyncio.get_running_loop()
            loop.create_task(self._collect_system_metrics())
        except RuntimeError:
            # Event loop not running yet, schedule for later
            # This is expected during module import - the task will be started
            # when the async event loop begins running
            pass
    
    async def _collect_system_metrics(self):
        """Collect real system metrics every 30 seconds"""
        while True:
            try:
                # Use the shared CPU monitor to get real performance data
                from utils.shared_cpu_monitor import get_memory_usage, get_cpu_usage
                
                # Get actual memory usage
                memory_info = await get_memory_usage()
                memory_mb = memory_info['process_memory_mb']
                
                # Get actual CPU usage
                cpu_reading = await get_cpu_usage()
                cpu_percent = cpu_reading.process_cpu
                
                # ANOMALY FIX: Calculate active users with 30-minute timeout (same as dashboard)
                now = datetime.now()
                active_cutoff = now - timedelta(minutes=30)
                current_active_users = {
                    user_id: data for user_id, data in self.active_users.items()
                    if data['last_activity'] > active_cutoff
                }
                
                # ANOMALY FIX: Clean up inactive users from memory to prevent unlimited growth
                self.active_users = current_active_users
                
                # Update system health with real data
                self.system_health.update({
                    'status': 'healthy',
                    'last_check': now.isoformat(),
                    'active_users': len(current_active_users),
                    'errors_per_hour': self._calculate_errors_per_hour(),
                    'response_time': 0,  # TODO: Implement if needed
                    'memory_usage_mb': memory_mb,
                    'cpu_usage_percent': cpu_percent
                })
                
                logger.info(f"📊 Dashboard Metrics Updated: Memory={memory_mb:.1f}MB, CPU={cpu_percent:.1f}%, Users={len(current_active_users)}")
                
            except Exception as e:
                logger.error(f"Error collecting system metrics: {e}")
                # Fallback to basic metrics
                import psutil
                process = psutil.Process()
                memory_mb = process.memory_info().rss / 1024 / 1024
                cpu_percent = process.cpu_percent()
                
                # ANOMALY FIX: Apply same active user filtering in fallback
                now = datetime.now()
                active_cutoff = now - timedelta(minutes=30)
                current_active_users = {
                    user_id: data for user_id, data in self.active_users.items()
                    if data['last_activity'] > active_cutoff
                }
                self.active_users = current_active_users
                
                self.system_health.update({
                    'memory_usage_mb': memory_mb,
                    'cpu_usage_percent': cpu_percent,
                    'active_users': len(current_active_users),
                    'last_check': now.isoformat()
                })
            
            await asyncio.sleep(30)  # Update every 30 seconds
    
    def _calculate_errors_per_hour(self) -> int:
        """Calculate error rate for the last hour"""
        try:
            one_hour_ago = datetime.now() - timedelta(hours=1)
            error_count = sum(1 for activity in self.activities 
                            if activity.activity_type == ActivityType.ERROR_EVENT 
                            and activity.timestamp > one_hour_ago)
            return error_count
        except Exception:
            return 0

    def track_user_interaction(self, user_id: int, username: Optional[str] = None, action: str = "", 
                             details: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
        """Track user interaction activity"""
        try:
            event = ActivityEvent(
                id=str(uuid.uuid4()),
                timestamp=datetime.now(),
                activity_type=ActivityType.USER_INTERACTION,
                user_id=user_id,
                username=username or f"user_{user_id}",
                is_admin=self._is_admin(user_id),
                title=f"User {action}" if action else "User Activity",
                description=f"User {username or user_id} performed: {action}",
                details=details or {},
                trace_id=trace_id
            )
            
            self.activities.append(event)
            self.user_activity_count[user_id] += 1
            
            # Update active users
            self.active_users[user_id] = {
                'username': username,
                'last_activity': datetime.now(),
                'action': action
            }
            
            # Real-time notification to admin dashboard
            asyncio.create_task(self._notify_admin_dashboard(event))
            
            logger.info(f"👤 User Activity: {username or user_id} - {action}")
            
        except Exception as e:
            logger.error(f"Error tracking user interaction: {e}")
    
    def track_error_with_correlation(self, user_id: Optional[int], user_message: str, 
                                   backend_error: str, handler_name: str, 
                                   callback_data: Optional[str] = None, trace_id: Optional[str] = None):
        """Track correlated errors between user-facing and backend"""
        try:
            correlation_id = str(uuid.uuid4())
            
            # Create error correlation record
            correlation = ErrorCorrelation(
                correlation_id=correlation_id,
                user_message=user_message,
                backend_error=backend_error,
                user_id=user_id,
                timestamp=datetime.now(),
                handler_name=handler_name,
                callback_data=callback_data
            )
            
            self.error_correlations.append(correlation)
            
            # Create activity event
            severity = self._determine_error_severity(user_message, backend_error)
            
            event = ActivityEvent(
                id=str(uuid.uuid4()),
                timestamp=datetime.now(),
                activity_type=ActivityType.ERROR_EVENT,
                user_id=user_id,
                username=self.active_users.get(user_id, {}).get('username', f"user_{user_id}") if user_id else "unknown",
                is_admin=self._is_admin(user_id) if user_id else False,
                title=f"Error: {user_message}",
                description=f"Handler: {handler_name} | Backend: {backend_error}",
                details={
                    'user_message': user_message,
                    'backend_error': backend_error,
                    'handler_name': handler_name,
                    'callback_data': callback_data,
                    'correlation_id': correlation_id
                },
                correlation_id=correlation_id,
                trace_id=trace_id,
                error_severity=severity
            )
            
            self.activities.append(event)
            self.error_patterns[handler_name].append(datetime.now())
            
            # Real-time notification to admin dashboard
            asyncio.create_task(self._notify_admin_dashboard(event))
            
            logger.error(f"🔗 Error Correlation [{correlation_id[:8]}]: User='{user_message}' | Backend='{backend_error}'")
            
            return correlation_id
            
        except Exception as e:
            logger.error(f"Error tracking error correlation: {e}")
            return None
    
    def track_admin_action(self, admin_id: int, action: str, target_user_id: Optional[int] = None, 
                          details: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
        """Track admin actions"""
        try:
            event = ActivityEvent(
                id=str(uuid.uuid4()),
                timestamp=datetime.now(),
                activity_type=ActivityType.ADMIN_ACTION,
                user_id=admin_id,
                username=f"admin_{admin_id}",
                is_admin=True,
                title=f"Admin: {action}",
                description=f"Admin {admin_id} performed: {action}" + (f" on user {target_user_id}" if target_user_id else ""),
                details={**(details or {}), 'target_user_id': target_user_id},
                trace_id=trace_id
            )
            
            self.activities.append(event)
            
            # Real-time notification to admin dashboard
            asyncio.create_task(self._notify_admin_dashboard(event))
            
            logger.info(f"👮 Admin Action: {admin_id} - {action}")
            
        except Exception as e:
            logger.error(f"Error tracking admin action: {e}")
    
    def track_system_event(self, event_title: str, description: str, 
                          details: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
        """Track system events"""
        try:
            event = ActivityEvent(
                id=str(uuid.uuid4()),
                timestamp=datetime.now(),
                activity_type=ActivityType.SYSTEM_EVENT,
                user_id=None,
                username="system",
                is_admin=False,
                title=event_title,
                description=description,
                details=details or {},
                trace_id=trace_id
            )
            
            self.activities.append(event)
            
            # Real-time notification to admin dashboard
            asyncio.create_task(self._notify_admin_dashboard(event))
            
            logger.info(f"⚙️ System Event: {event_title}")
            
        except Exception as e:
            logger.error(f"Error tracking system event: {e}")
    
    def get_recent_activities(self, limit: int = 50, activity_type: Optional[ActivityType] = None, 
                            user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get recent activities with filtering"""
        try:
            activities = list(self.activities)
            
            # Filter by activity type
            if activity_type:
                activities = [a for a in activities if a.activity_type == activity_type]
            
            # Filter by user ID
            if user_id:
                activities = [a for a in activities if a.user_id == user_id]
            
            # Sort by timestamp (newest first) and limit
            activities.sort(key=lambda x: x.timestamp, reverse=True)
            recent = activities[:limit]
            
            return [activity.to_dict() for activity in recent]
            
        except Exception as e:
            logger.error(f"Error getting recent activities: {e}")
            return []
    
    def get_error_correlations(self, limit: int = 50, unresolved_only: bool = False) -> List[Dict[str, Any]]:
        """Get error correlations with filtering"""
        try:
            correlations = list(self.error_correlations)
            
            # Filter unresolved only
            if unresolved_only:
                correlations = [c for c in correlations if not c.resolved]
            
            # Sort by timestamp (newest first) and limit
            correlations.sort(key=lambda x: x.timestamp, reverse=True)
            recent = correlations[:limit]
            
            return [correlation.to_dict() for correlation in recent]
            
        except Exception as e:
            logger.error(f"Error getting error correlations: {e}")
            return []
    
    def get_live_dashboard_data(self) -> Dict[str, Any]:
        """Get comprehensive dashboard data"""
        try:
            now = datetime.now()
            hour_ago = now - timedelta(hours=1)
            day_ago = now - timedelta(days=1)
            
            # Recent activities
            recent_activities = self.get_recent_activities(20)
            
            # Active users (last 30 minutes)
            active_cutoff = now - timedelta(minutes=30)
            active_users = {
                user_id: data for user_id, data in self.active_users.items()
                if data['last_activity'] > active_cutoff
            }
            
            # Error statistics
            recent_errors = [a for a in self.activities if 
                           a.activity_type == ActivityType.ERROR_EVENT and 
                           a.timestamp > hour_ago]
            
            # Error patterns by handler
            error_by_handler = defaultdict(int)
            for error in recent_errors:
                handler = error.details.get('handler_name', 'unknown')
                error_by_handler[handler] += 1
            
            # System health will be updated below with proper serialization
            
            # Update system health with proper serialization
            self.system_health.update({
                'last_check': now.isoformat(),
                'active_users': len(active_users),
                'errors_per_hour': len(recent_errors),
                'total_activities_today': len([a for a in self.activities if a.timestamp > day_ago])
            })
            
            # Build the final response payload
            payload = {
                'timestamp': now.isoformat(),
                'system_health': self.system_health,
                'recent_activities': recent_activities,
                'active_users': [
                    {'user_id': uid, **{k: v if k != 'last_activity' else v.isoformat() for k, v in data.items()}}
                    for uid, data in active_users.items()
                ],
                'error_correlations': self.get_error_correlations(10, unresolved_only=True),
                'error_patterns': dict(error_by_handler),
                'activity_stats': {
                    'total_activities': len(self.activities),
                    'user_interactions': len([a for a in self.activities if a.activity_type == ActivityType.USER_INTERACTION]),
                    'errors': len([a for a in self.activities if a.activity_type == ActivityType.ERROR_EVENT]),
                    'admin_actions': len([a for a in self.activities if a.activity_type == ActivityType.ADMIN_ACTION]),
                    'system_events': len([a for a in self.activities if a.activity_type == ActivityType.SYSTEM_EVENT])
                }
            }
            
            # Ensure everything is JSON-safe (handles nested datetime keys/values)
            return json_safe(payload)  # type: ignore[return-value]
            
        except Exception as e:
            logger.error(f"Error getting dashboard data: {e}")
            return {'error': str(e), 'timestamp': datetime.now().isoformat()}
    
    async def _notify_admin_dashboard(self, event: ActivityEvent):
        """Notify admin dashboard of new activity (WebSocket placeholder)"""
        try:
            # This would send WebSocket notifications to connected admin dashboards
            # For now, we'll just log it for real-time visibility
            if event.activity_type == ActivityType.ERROR_EVENT:
                logger.warning(f"🔴 REAL-TIME ERROR: {event.title} | {event.description}")
            elif event.activity_type == ActivityType.USER_INTERACTION:
                logger.info(f"🟢 LIVE ACTIVITY: {event.title} | {event.description}")
            elif event.activity_type == ActivityType.ADMIN_ACTION:
                logger.info(f"🟡 ADMIN ACTION: {event.title} | {event.description}")
            else:
                logger.info(f"🔵 SYSTEM EVENT: {event.title} | {event.description}")
                
        except Exception as e:
            logger.error(f"Error notifying admin dashboard: {e}")
    
    def _is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        try:
            from config import Config
            return user_id in Config.ADMIN_IDS
        except Exception as e:
            logger.debug(f"Could not check admin status: {e}")
            return False
    
    def _determine_error_severity(self, user_message: str, backend_error: str) -> ErrorSeverity:
        """Determine error severity based on error content"""
        user_lower = user_message.lower()
        backend_error.lower()
        
        # Critical errors
        if any(keyword in user_lower for keyword in ['database', 'payment', 'funds', 'money']):
            return ErrorSeverity.CRITICAL
        
        # High severity
        if any(keyword in user_lower for keyword in ['error', 'failed', 'cannot', 'unable']):
            return ErrorSeverity.HIGH
        
        # Medium severity
        if any(keyword in user_lower for keyword in ['invalid', 'not found', 'expired']):
            return ErrorSeverity.MEDIUM
        
        # Default to low
        return ErrorSeverity.LOW

# Global instance
unified_monitor = UnifiedActivityMonitor()

# Convenience functions
def track_user_activity(user_id: int, action: str, username: Optional[str] = None, 
                       details: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
    """Track user activity"""
    unified_monitor.track_user_interaction(user_id, username, action, details, trace_id)

def track_correlated_error(user_id: Optional[int], user_message: str, backend_error: str, 
                         handler_name: str, callback_data: Optional[str] = None, trace_id: Optional[str] = None):
    """Track correlated error"""
    return unified_monitor.track_error_with_correlation(
        user_id, user_message, backend_error, handler_name, callback_data, trace_id
    )

def track_admin_action(admin_id: int, action: str, target_user_id: Optional[int] = None, 
                      details: Optional[Dict[str, Any]] = None, trace_id: Optional[str] = None):
    """Track admin action"""
    unified_monitor.track_admin_action(admin_id, action, target_user_id, details, trace_id)

def get_dashboard_data():
    """Get live dashboard data"""
    return unified_monitor.get_live_dashboard_data()