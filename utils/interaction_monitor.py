"""
Advanced interaction monitoring to prevent callback timeouts and system overload
"""

import logging
from typing import Dict, List
from datetime import datetime, timedelta
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class InteractionMetrics:
    """Track user interaction patterns"""
    user_id: int
    interaction_timestamps: List[datetime]  # Track actual timestamps for sliding window
    avg_response_time: float
    callback_timeout_count: int
    last_interaction: datetime

class InteractionMonitor:
    """Monitor and optimize user interactions to prevent system overload"""
    
    def __init__(self):
        self.user_metrics: Dict[int, InteractionMetrics] = {}
        self.global_metrics = {
            'total_interactions': 0,
            'total_timeouts': 0,
            'avg_system_response_time': 0.0
        }
        
    def record_interaction(self, user_id: int, response_time: float = 0.0):
        """Record user interaction and update metrics"""
        current_time = datetime.now()
        
        if user_id not in self.user_metrics:
            self.user_metrics[user_id] = InteractionMetrics(
                user_id=user_id,
                interaction_timestamps=[current_time],
                avg_response_time=response_time,
                callback_timeout_count=0,
                last_interaction=current_time
            )
        else:
            metrics = self.user_metrics[user_id]
            
            # Add current interaction timestamp
            metrics.interaction_timestamps.append(current_time)
            
            # Clean up old timestamps (older than 1 minute) - sliding window
            minute_ago = current_time - timedelta(minutes=1)
            metrics.interaction_timestamps = [
                ts for ts in metrics.interaction_timestamps if ts > minute_ago
            ]
                
            # Update average response time
            metrics.avg_response_time = (metrics.avg_response_time + response_time) / 2
            metrics.last_interaction = current_time
            
        self.global_metrics['total_interactions'] += 1
    
    def record_callback_timeout(self, user_id: int):
        """Record callback timeout for user"""
        if user_id in self.user_metrics:
            self.user_metrics[user_id].callback_timeout_count += 1
        self.global_metrics['total_timeouts'] += 1
        
        logger.warning(f"Callback timeout recorded for user {user_id}")
    
    def should_rate_limit_user(self, user_id: int) -> bool:
        """Check if user should be rate limited"""
        if user_id not in self.user_metrics:
            return False
            
        metrics = self.user_metrics[user_id]
        current_time = datetime.now()
        
        # Clean old timestamps before checking
        minute_ago = current_time - timedelta(minutes=1)
        metrics.interaction_timestamps = [
            ts for ts in metrics.interaction_timestamps if ts > minute_ago
        ]
        
        # Rate limit if too many interactions in last minute (increased threshold)
        # Normal users clicking buttons shouldn't hit this - only spam/abuse
        if len(metrics.interaction_timestamps) > 50:  # Increased from 25 to 50
            logger.warning(f"User {user_id} has {len(metrics.interaction_timestamps)} interactions in last minute")
            return True
                
        # Rate limit if too many timeouts (more lenient)
        if metrics.callback_timeout_count > 5:  # Increased from 3 to 5
            return True
            
        return False
    
    def get_recommended_delay(self, user_id: int) -> float:
        """Get recommended delay before processing user request"""
        if user_id not in self.user_metrics:
            return 0.0
            
        metrics = self.user_metrics[user_id]
        current_time = datetime.now()
        
        # Clean old timestamps
        minute_ago = current_time - timedelta(minutes=1)
        metrics.interaction_timestamps = [
            ts for ts in metrics.interaction_timestamps if ts > minute_ago
        ]
        
        # Base delay on interaction rate and timeout history
        base_delay = 0.0
        
        # Only add delay for truly rapid interactions (more lenient)
        if len(metrics.interaction_timestamps) > 40:
            base_delay += 0.3  # Reduced from 0.5s to 0.3s
            
        if metrics.callback_timeout_count > 3:  # More lenient threshold
            base_delay += 0.5  # Reduced from 1.0s to 0.5s
            
        if metrics.avg_response_time > 5.0:  # More lenient threshold
            base_delay += 0.3  # Reduced from 0.5s to 0.3s
            
        return min(base_delay, 2.0)  # Reduced max delay from 3s to 2s
    
    def cleanup_old_metrics(self):
        """Clean up old user metrics (older than 1 hour)"""
        current_time = datetime.now()
        hour_ago = current_time - timedelta(hours=1)
        
        users_to_remove = []
        for user_id, metrics in self.user_metrics.items():
            if metrics.last_interaction < hour_ago:
                users_to_remove.append(user_id)
                
        for user_id in users_to_remove:
            del self.user_metrics[user_id]
            
        if users_to_remove:
            logger.info(f"Cleaned up metrics for {len(users_to_remove)} inactive users")

# Global instance
interaction_monitor = InteractionMonitor()