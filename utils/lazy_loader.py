"""
Lazy Loading System for Bot Handlers
Reduces startup time by loading handlers only when needed
"""

import logging
import asyncio
import importlib
from typing import Dict, List, Callable, Any
import time

logger = logging.getLogger(__name__)


class LazyHandlerLoader:
    """Lazy loading system for bot handlers to improve startup performance"""
    
    def __init__(self):
        self._handler_registry = {}
        self._loaded_handlers = set()
        self._import_cache = {}
        self._loading_locks = {}
        self.stats = {
            'handlers_registered': 0,
            'handlers_loaded': 0,
            'cache_hits': 0,
            'load_times': {}
        }
    
    def register_handler_group(self, group_name: str, module_path: str, handler_configs: List[Dict[str, Any]], priority: int = 0):
        """Register a group of handlers for lazy loading"""
        self._handler_registry[group_name] = {
            'module_path': module_path,
            'handlers': handler_configs,
            'priority': priority,
            'loaded': False
        }
        self.stats['handlers_registered'] += len(handler_configs)
        logger.debug(f"🔄 Registered lazy handler group: {group_name} ({len(handler_configs)} handlers)")
    
    async def load_handler_group(self, group_name: str, application) -> bool:
        """Load a specific handler group on-demand"""
        if group_name in self._loaded_handlers:
            self.stats['cache_hits'] += 1
            return True
        
        if group_name not in self._handler_registry:
            logger.warning(f"Unknown handler group: {group_name}")
            return False
        
        # Prevent concurrent loading of same group
        if group_name not in self._loading_locks:
            self._loading_locks[group_name] = asyncio.Lock()
        
        async with self._loading_locks[group_name]:
            if group_name in self._loaded_handlers:
                return True
            
            start_time = time.time()
            group_config = self._handler_registry[group_name]
            
            try:
                # Import module dynamically
                module = await self._import_module_async(group_config['module_path'])
                
                # Register handlers from the module
                for handler_config in group_config['handlers']:
                    await self._register_single_handler(application, module, handler_config)
                
                self._loaded_handlers.add(group_name)
                self.stats['handlers_loaded'] += len(group_config['handlers'])
                
                load_time = time.time() - start_time
                self.stats['load_times'][group_name] = load_time
                
                logger.info(f"✅ Loaded handler group '{group_name}' in {load_time:.3f}s ({len(group_config['handlers'])} handlers)")
                return True
                
            except Exception as e:
                logger.error(f"❌ Failed to load handler group '{group_name}': {e}")
                return False
    
    async def _import_module_async(self, module_path: str):
        """Async import with caching"""
        if module_path in self._import_cache:
            return self._import_cache[module_path]
        
        # Run import in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        module = await loop.run_in_executor(None, importlib.import_module, module_path)
        self._import_cache[module_path] = module
        return module
    
    async def _register_single_handler(self, application, module, handler_config):
        """Register a single handler from config"""
        handler_type = handler_config['type']
        handler_name = handler_config['name']
        
        # Check if handler exists in module
        if not hasattr(module, handler_name):
            logger.warning(f"Handler '{handler_name}' not found in module, skipping")
            return
        
        # Get handler function from module
        handler_func = getattr(module, handler_name)
        
        try:
            if handler_type == 'callback_query':
                from telegram.ext import CallbackQueryHandler
                pattern = handler_config.get('pattern')
                group = handler_config.get('group', 0)
                application.add_handler(CallbackQueryHandler(handler_func, pattern=pattern), group=group)
                
            elif handler_type == 'command':
                from telegram.ext import CommandHandler
                command = handler_config.get('command')
                application.add_handler(CommandHandler(command, handler_func))
                
            elif handler_type == 'conversation':
                # Handler function returns a conversation handler
                conversation_handler = handler_func() if callable(handler_func) else handler_func
                try:
                    from telegram.ext import ConversationHandler
                    if isinstance(conversation_handler, ConversationHandler):
                        application.add_handler(conversation_handler)
                    else:
                        logger.error(f"Handler '{handler_name}' is not a valid ConversationHandler instance (got {type(conversation_handler)})")
                except Exception as e:
                    logger.error(f"Failed to add conversation handler '{handler_name}': {e}")
                
            elif handler_type == 'message':
                from telegram.ext import MessageHandler, filters
                filter_type = handler_config.get('filters', 'TEXT')
                message_filter = getattr(filters, filter_type)
                application.add_handler(MessageHandler(message_filter, handler_func))
                
            elif handler_type == 'handlers_list':
                # Special case for handlers that are exported as a list
                if isinstance(handler_func, list):
                    for handler in handler_func:
                        application.add_handler(handler)
                else:
                    logger.warning(f"Expected list for handlers_list type, got {type(handler_func)}")
                    
            elif handler_type == 'special':
                # Special case for refund command registration
                if handler_name == 'register_all_refund_commands':
                    # Import and call the RefundCommandRegistry
                    from handlers.refund_command_registry import RefundCommandRegistry
                    success = RefundCommandRegistry.register_all_commands(application)
                    if success:
                        logger.info("✅ RefundCommandRegistry registered all commands successfully via lazy loader")
                    else:
                        logger.error("❌ RefundCommandRegistry registration failed via lazy loader")
                else:
                    logger.warning(f"Unknown special handler type: {handler_name}")
                    
        except Exception as e:
            logger.error(f"Failed to register handler '{handler_name}': {e}")
    
    async def load_critical_handlers(self, application):
        """Load only critical handlers needed for basic functionality"""
        critical_groups = [
            'core_commands',
            'basic_menu',
            'error_handlers'
        ]
        
        tasks = [self.load_handler_group(group, application) for group in critical_groups]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        success_count = sum(1 for r in results if r is True)
        logger.info(f"✅ Loaded {success_count}/{len(critical_groups)} critical handler groups")
        return success_count == len(critical_groups)
    
    async def load_on_demand(self, required_groups: List[str], application):
        """Load specific handler groups on demand"""
        tasks = [self.load_handler_group(group, application) for group in required_groups]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        success_count = sum(1 for r in results if r is True)
        logger.info(f"✅ Loaded {success_count}/{len(required_groups)} on-demand handler groups")
        return success_count
    
    def get_stats(self) -> Dict[str, Any]:
        """Get loading statistics"""
        return {
            **self.stats,
            'groups_registered': len(self._handler_registry),
            'groups_loaded': len(self._loaded_handlers),
            'cache_size': len(self._import_cache)
        }


class BackgroundTaskManager:
    """Manages heavy operations as background tasks after startup"""
    
    def __init__(self):
        self.tasks = []
        self.completed_tasks = set()
        
    def register_background_task(self, name: str, task_func: Callable, delay: float = 0):
        """Register a task to run in background after startup"""
        self.tasks.append({
            'name': name,
            'func': task_func,
            'delay': delay
        })
        logger.debug(f"🕐 Registered background task: {name} (delay: {delay}s)")
    
    async def start_background_tasks(self):
        """Start all registered background tasks"""
        logger.info(f"🚀 Starting {len(self.tasks)} background tasks...")
        
        for task_config in self.tasks:
            asyncio.create_task(self._run_background_task(task_config))
        
        logger.info("✅ All background tasks started")
    
    async def _run_background_task(self, task_config):
        """Run a single background task with error handling"""
        name = task_config['name']
        delay = task_config['delay']
        
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            
            start_time = time.time()
            
            if asyncio.iscoroutinefunction(task_config['func']):
                await task_config['func']()
            else:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, task_config['func'])
            
            elapsed = time.time() - start_time
            self.completed_tasks.add(name)
            logger.info(f"✅ Background task '{name}' completed in {elapsed:.2f}s")
            
        except Exception as e:
            logger.error(f"❌ Background task '{name}' failed: {e}")


# Global instances
lazy_loader = LazyHandlerLoader()
background_manager = BackgroundTaskManager()


def lazy_handler_group(group_name: str, module_path: str, priority: int = 0):
    """Decorator to register handler groups for lazy loading"""
    def decorator(handler_configs):
        lazy_loader.register_handler_group(group_name, module_path, handler_configs, priority)
        return handler_configs
    return decorator


def background_task(name: str, delay: float = 0):
    """Decorator to register functions as background tasks"""
    def decorator(func):
        background_manager.register_background_task(name, func, delay)
        return func
    return decorator