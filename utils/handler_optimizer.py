"""
Handler Registration Optimizer
Streamlines handler setup process and reduces initialization time
"""

import logging
import time
from typing import List
from telegram.ext import Application, CallbackQueryHandler, MessageHandler

logger = logging.getLogger(__name__)

class HandlerOptimizer:
    """Optimize handler registration for faster startup"""
    
    def __init__(self, application: Application):
        self.application = application
        self.registration_times = {}
        self.batch_size = 10  # Register handlers in batches
    
    def batch_register_handlers(self, handlers: List[tuple]):
        """Register handlers in optimized batches"""
        start_time = time.time()
        
        # Sort handlers by priority (critical handlers first)
        priority_handlers = []
        normal_handlers = []
        
        for handler_data in handlers:
            if len(handler_data) > 2 and handler_data[2].get('priority', 0) > 0:
                priority_handlers.append(handler_data)
            else:
                normal_handlers.append(handler_data)
        
        # Register priority handlers first
        for handler, pattern, kwargs in priority_handlers:
            self.application.add_handler(handler)
        
        # Register normal handlers in batches
        for i in range(0, len(normal_handlers), self.batch_size):
            batch = normal_handlers[i:i + self.batch_size]
            for handler, pattern, kwargs in batch:
                self.application.add_handler(handler)
        
        registration_time = time.time() - start_time
        logger.info(f"✅ Batch registered {len(handlers)} handlers in {registration_time:.3f}s")
        
        return registration_time
    
    def optimize_callback_patterns(self):
        """Pre-compile regex patterns for better performance"""
        import re
        
        # Common patterns used in the bot
        patterns = [
            r"^admin_.*",
            r"^menu_.*", 
            r"^wallet_.*",
            r"^view_trade_.*",
            r"^dispute_.*",
            r"^message_.*",
            r"^confirm_.*",
            r"^cancel_.*"
        ]
        
        start_time = time.time()
        compiled_patterns = {}
        
        for pattern in patterns:
            compiled_patterns[pattern] = re.compile(pattern)
        
        optimization_time = time.time() - start_time
        logger.info(f"✅ Pre-compiled {len(patterns)} callback patterns in {optimization_time:.3f}s")
        
        return compiled_patterns

class FastHandlerRegistry:
    """Fast handler registration system"""
    
    def __init__(self):
        self.handlers = []
        self.conversation_handlers = []
        self.callback_handlers = []
        self.message_handlers = []
    
    def add_callback_handler(self, handler, pattern, group=0, priority=0):
        """Add callback handler with metadata"""
        self.callback_handlers.append({
            'handler': CallbackQueryHandler(handler, pattern=pattern),
            'group': group,
            'priority': priority,
            'type': 'callback'
        })
    
    def add_message_handler(self, handler, filters, group=0, priority=0):
        """Add message handler with metadata"""
        self.message_handlers.append({
            'handler': MessageHandler(filters, handler),
            'group': group, 
            'priority': priority,
            'type': 'message'
        })
    
    def register_all(self, application: Application):
        """Register all handlers optimally"""
        start_time = time.time()
        
        # Sort by priority and group
        all_handlers = (
            self.callback_handlers + 
            self.message_handlers + 
            self.conversation_handlers
        )
        
        # Sort by priority (high to low), then by group (low to high)
        all_handlers.sort(key=lambda x: (-x.get('priority', 0), x.get('group', 0)))
        
        # Register in optimized order
        for handler_data in all_handlers:
            application.add_handler(
                handler_data['handler'], 
                group=handler_data.get('group', 0)
            )
        
        registration_time = time.time() - start_time
        logger.info(f"🚀 Fast registered {len(all_handlers)} handlers in {registration_time:.3f}s")
        
        return registration_time

def create_optimized_handler_system(application: Application) -> HandlerOptimizer:
    """Create optimized handler registration system"""
    optimizer = HandlerOptimizer(application)
    optimizer.optimize_callback_patterns()
    
    logger.info("⚡ Handler optimization system ready")
    return optimizer