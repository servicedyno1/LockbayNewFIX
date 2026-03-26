"""
Admin Service - Compatibility shims for testing infrastructure

Minimal service stubs required by admin tests without production coupling.
"""

from typing import Dict, Any, List, Optional
from models import AdminUser, AdminPermission


class AdminService:
    """Admin service for user management operations"""
    
    async def get_admin_user(self, admin_id: str) -> Optional[AdminUser]:
        """Get admin user by ID"""
        return None
    
    async def create_admin_user(self, data: Dict[str, Any]) -> AdminUser:
        """Create admin user"""
        # Minimal stub for testing
        return AdminUser()
    
    async def update_admin_permissions(self, admin_id: str, permissions: List[AdminPermission]) -> bool:
        """Update admin permissions"""
        return True


class AdminAuthService:
    """Admin authentication service"""
    
    async def authenticate_admin(self, telegram_id: int) -> Optional[AdminUser]:
        """Authenticate admin user"""
        return None
    
    async def create_admin_session(self, admin_id: str) -> str:
        """Create admin session"""
        return "test_session_id"
    
    async def validate_admin_session(self, session_id: str) -> Optional[AdminUser]:
        """Validate admin session"""
        return None


class AdminBroadcastService:
    """Admin broadcast service"""
    
    async def create_broadcast(self, data: Dict[str, Any]) -> str:
        """Create broadcast message"""
        return "test_broadcast_id"
    
    async def send_broadcast(self, broadcast_id: str) -> Dict[str, Any]:
        """Send broadcast message"""
        return {"status": "sent", "delivered": 0, "failed": 0}
    
    async def get_broadcast_stats(self, broadcast_id: str) -> Dict[str, Any]:
        """Get broadcast statistics"""
        return {"delivered": 0, "failed": 0, "pending": 0}


class EmergencyControlService:
    """Emergency control service"""
    
    async def activate_emergency_control(self, control_type: str, admin_id: str) -> bool:
        """Activate emergency control"""
        return True
    
    async def deactivate_emergency_control(self, control_type: str, admin_id: str) -> bool:
        """Deactivate emergency control"""
        return True
    
    async def get_active_controls(self) -> List[Dict[str, Any]]:
        """Get active emergency controls"""
        return []