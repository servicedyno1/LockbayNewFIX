"""
Advanced Button Debouncing System
Prevents rapid duplicate button presses and improves user experience
"""

import logging
import asyncio
from typing import Dict, Set, Optional
from datetime import datetime, timedelta
from dataclasses import dataclass
from collections import defaultdict

logger = logging.getLogger(__name__)

@dataclass
class ButtonPress:
    """Represents a button press event"""
    user_id: int
    callback_data: str
    timestamp: datetime
    query_id: str

class AdvancedDebouncer:
    """
    Advanced debouncing system that prevents rapid duplicate button presses
    while maintaining responsive UI
    """
    
    def __init__(self):
        # Track recent button presses per user
        self._user_button_presses: Dict[int, Dict[str, datetime]] = defaultdict(dict)
        
        # Track processing callbacks to prevent duplicates
        self._processing_callbacks: Set[str] = set()
        
        # Track user interaction patterns
        self._user_click_patterns: Dict[int, list] = defaultdict(list)
        
        # Configuration
        self.debounce_window = timedelta(seconds=0.5)  # 500ms debounce window
        self.rapid_click_threshold = 5  # clicks in rapid succession
        self.rapid_click_window = timedelta(seconds=3)  # 3 second window
        self.processing_timeout = timedelta(seconds=10)  # Max processing time
        
        # Start cleanup task
        asyncio.create_task(self._cleanup_task())
    
    async def should_process_callback(self, user_id: int, callback_data: str, query_id: str) -> bool:
        """
        Determine if a callback should be processed based on debouncing rules
        
        Args:
            user_id: User ID making the request
            callback_data: The callback data from the button
            query_id: Unique query ID from Telegram
            
        Returns:
            True if callback should be processed, False if it should be debounced
        """
        current_time = datetime.now()
        
        # Check if this exact query is already being processed
        if query_id in self._processing_callbacks:
            logger.debug(f"Callback {query_id} already processing - debouncing")
            return False
        
        # Check for rapid duplicate button presses
        user_buttons = self._user_button_presses[user_id]
        button_key = f"{callback_data}"
        
        if button_key in user_buttons:
            time_since_last = current_time - user_buttons[button_key]
            if time_since_last < self.debounce_window:
                logger.info(f"Debouncing rapid button press for user {user_id}: {callback_data}")
                return False
        
        # Check for rapid clicking patterns
        if self._is_rapid_clicking(user_id, current_time):
            logger.warning(f"Rapid clicking detected for user {user_id} - applying extended debounce")
            return False
        
        # Update tracking data
        user_buttons[button_key] = current_time
        self._user_click_patterns[user_id].append(current_time)
        self._processing_callbacks.add(query_id)
        
        return True
    
    def mark_callback_complete(self, query_id: str):
        """Mark a callback as completed processing"""
        self._processing_callbacks.discard(query_id)
    
    def _is_rapid_clicking(self, user_id: int, current_time: datetime) -> bool:
        """Check if user is rapidly clicking buttons"""
        clicks = self._user_click_patterns[user_id]
        
        # Remove old clicks outside the window
        cutoff_time = current_time - self.rapid_click_window
        recent_clicks = [click for click in clicks if click > cutoff_time]
        self._user_click_patterns[user_id] = recent_clicks
        
        # Check if user exceeds rapid click threshold
        return len(recent_clicks) >= self.rapid_click_threshold
    
    async def _cleanup_task(self):
        """Background task to clean up old tracking data"""
        while True:
            try:
                await asyncio.sleep(300)  # Run every 5 minutes
                await self._cleanup_old_data()
            except Exception as e:
                logger.error(f"Error in debouncer cleanup task: {e}")
    
    async def _cleanup_old_data(self):
        """Remove old tracking data to prevent memory leaks"""
        current_time = datetime.now()
        cleanup_cutoff = current_time - timedelta(minutes=10)
        
        # Clean up user button presses
        for user_id in list(self._user_button_presses.keys()):
            user_buttons = self._user_button_presses[user_id]
            # Remove old button presses
            self._user_button_presses[user_id] = {
                button: timestamp for button, timestamp in user_buttons.items()
                if timestamp > cleanup_cutoff
            }
            # Remove empty user entries
            if not self._user_button_presses[user_id]:
                del self._user_button_presses[user_id]
        
        # Clean up click patterns
        for user_id in list(self._user_click_patterns.keys()):
            recent_clicks = [
                click for click in self._user_click_patterns[user_id]
                if click > cleanup_cutoff
            ]
            if recent_clicks:
                self._user_click_patterns[user_id] = recent_clicks
            else:
                del self._user_click_patterns[user_id]
        
        # Clean up stuck processing callbacks
        current_time - self.processing_timeout
        {
            query_id for query_id in self._processing_callbacks
            # Note: We can't easily get timestamp for query_id, so we'll rely on periodic cleanup
        }
        
        logger.debug(f"Cleanup completed - tracking {len(self._user_button_presses)} users")

# Global debouncer instance
advanced_debouncer = AdvancedDebouncer()

async def debounced_callback_handler(callback_func):
    """
    Decorator for callback handlers to add debouncing
    
    Usage:
        @debounced_callback_handler
        async def my_callback_handler(update, context):
            # Your callback logic here
            pass
    """
    async def wrapper(update, context):
        if not update.callback_query:
            return await callback_func(update, context)
        
        query = update.callback_query
        user_id = query.from_user.id if query.from_user else 0
        callback_data = query.data or ""
        query_id = query.id
        
        # Check if callback should be processed
        if not await advanced_debouncer.should_process_callback(user_id, callback_data, query_id):
            # Silently ignore debounced callbacks
            try:
                await query.answer()  # Just acknowledge without text
            except Exception:
                pass  # Ignore if already answered
            return
        
        try:
            # Process the callback
            result = await callback_func(update, context)
            return result
        finally:
            # Mark callback as complete
            advanced_debouncer.mark_callback_complete(query_id)
    
    return wrapper

async def safe_debounced_answer_callback(query, text: Optional[str] = None, show_alert: bool = False):
    """
    Enhanced version of safe_answer_callback_query with advanced debouncing
    
    Args:
        query: The callback query to answer
        text: Optional text to show user
        show_alert: Whether to show as alert popup
    """
    if not query:
        return
    
    query_id = query.id
    
    try:
        # Answer the callback
        await query.answer(text=text, show_alert=show_alert)
        
        # Mark as complete in debouncer
        advanced_debouncer.mark_callback_complete(query_id)
        
    except Exception as e:
        logger.debug(f"Callback answer failed (likely already answered): {e}")
        # Still mark as complete to prevent stuck processing
        advanced_debouncer.mark_callback_complete(query_id)