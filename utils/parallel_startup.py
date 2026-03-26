"""
Parallel Startup Operations
Runs independent startup operations concurrently to reduce startup time
"""

import asyncio
import logging
import time
from typing import List, Dict, Any, Callable
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class ParallelStartupManager:
    """Manages parallel execution of startup operations"""
    
    def __init__(self):
        self.operations = []
        self.results = {}
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="startup")
        
    def register_operation(self, name: str, operation_func: Callable, dependencies: List[str] = None, critical: bool = True):
        """Register an operation for parallel execution"""
        self.operations.append({
            'name': name,
            'func': operation_func,
            'dependencies': dependencies or [],
            'critical': critical,
            'completed': False,
            'result': None,
            'error': None
        })
        logger.debug(f"📝 Registered startup operation: {name} (critical: {critical})")
    
    async def execute_parallel_startup(self) -> Dict[str, Any]:
        """Execute all operations in parallel, respecting dependencies"""
        logger.info(f"🚀 Starting parallel execution of {len(self.operations)} operations...")
        start_time = time.time()
        
        # Group operations by dependency level
        dependency_levels = self._calculate_dependency_levels()
        
        # Execute each level in parallel
        for level, ops in dependency_levels.items():
            if ops:
                logger.info(f"🔄 Executing level {level} operations: {[op['name'] for op in ops]}")
                await self._execute_operation_batch(ops)
        
        total_time = time.time() - start_time
        
        # Summarize results
        critical_failures = [op for op in self.operations if op['critical'] and op['error']]
        non_critical_failures = [op for op in self.operations if not op['critical'] and op['error']]
        successes = [op for op in self.operations if not op['error']]
        
        logger.info(f"✅ Parallel startup completed in {total_time:.2f}s")
        logger.info(f"   Successes: {len(successes)}, Critical failures: {len(critical_failures)}, Non-critical failures: {len(non_critical_failures)}")
        
        if critical_failures:
            logger.error(f"❌ Critical startup failures: {[op['name'] for op in critical_failures]}")
        
        return {
            'total_time': total_time,
            'successes': len(successes),
            'critical_failures': len(critical_failures),
            'non_critical_failures': len(non_critical_failures),
            'results': {op['name']: op['result'] for op in self.operations}
        }
    
    def _calculate_dependency_levels(self) -> Dict[int, List[Dict]]:
        """Calculate execution levels based on dependencies"""
        levels = {}
        op_by_name = {op['name']: op for op in self.operations}
        
        def get_level(op_name, visited=None):
            if visited is None:
                visited = set()
            
            if op_name in visited:
                logger.warning(f"Circular dependency detected involving {op_name}")
                return 0
            
            visited.add(op_name)
            op = op_by_name[op_name]
            
            if not op['dependencies']:
                return 0
            
            max_dep_level = max(get_level(dep, visited.copy()) for dep in op['dependencies'] if dep in op_by_name)
            return max_dep_level + 1
        
        for op in self.operations:
            level = get_level(op['name'])
            if level not in levels:
                levels[level] = []
            levels[level].append(op)
        
        return levels
    
    async def _execute_operation_batch(self, operations: List[Dict]):
        """Execute a batch of operations in parallel"""
        tasks = []
        
        for op in operations:
            task = asyncio.create_task(self._execute_single_operation(op))
            tasks.append(task)
        
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def _execute_single_operation(self, operation: Dict):
        """Execute a single operation with error handling"""
        name = operation['name']
        func = operation['func']
        
        start_time = time.time()
        
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func()
            else:
                # Run CPU-bound operations in thread pool
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(self.executor, func)
            
            elapsed = time.time() - start_time
            operation['result'] = result
            operation['completed'] = True
            
            logger.info(f"✅ '{name}' completed in {elapsed:.2f}s")
            
        except Exception as e:
            elapsed = time.time() - start_time
            operation['error'] = str(e)
            operation['completed'] = True
            
            if operation['critical']:
                logger.error(f"❌ Critical operation '{name}' failed in {elapsed:.2f}s: {e}")
            else:
                logger.warning(f"⚠️ Non-critical operation '{name}' failed in {elapsed:.2f}s: {e}")
    
    def shutdown(self):
        """Shutdown the thread pool executor"""
        self.executor.shutdown(wait=True)


class DeferredOperationManager:
    """Manages operations that should run after the bot is fully started"""
    
    def __init__(self):
        self.deferred_operations = []
        self.running = False
        
    def defer_operation(self, name: str, operation_func: Callable, delay: float = 5.0, retry_count: int = 3):
        """Defer an operation to run after bot startup"""
        self.deferred_operations.append({
            'name': name,
            'func': operation_func,
            'delay': delay,
            'retry_count': retry_count,
            'attempts': 0
        })
        logger.debug(f"⏰ Deferred operation: {name} (delay: {delay}s)")
    
    async def start_deferred_operations(self):
        """Start all deferred operations"""
        if self.running:
            return
            
        self.running = True
        logger.info(f"🕐 Starting {len(self.deferred_operations)} deferred operations...")
        
        for op in self.deferred_operations:
            asyncio.create_task(self._run_deferred_operation(op))
    
    async def _run_deferred_operation(self, operation: Dict):
        """Run a single deferred operation with retry logic"""
        name = operation['name']
        delay = operation['delay']
        
        # Wait for initial delay
        await asyncio.sleep(delay)
        
        while operation['attempts'] < operation['retry_count']:
            operation['attempts'] += 1
            
            try:
                start_time = time.time()
                
                if asyncio.iscoroutinefunction(operation['func']):
                    await operation['func']()
                else:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, operation['func'])
                
                elapsed = time.time() - start_time
                logger.info(f"✅ Deferred operation '{name}' completed in {elapsed:.2f}s")
                return
                
            except Exception as e:
                logger.warning(f"⚠️ Deferred operation '{name}' failed (attempt {operation['attempts']}): {e}")
                
                if operation['attempts'] < operation['retry_count']:
                    await asyncio.sleep(2 ** operation['attempts'])  # Exponential backoff
        
        logger.error(f"❌ Deferred operation '{name}' failed after {operation['retry_count']} attempts")


# Global instances
startup_manager = ParallelStartupManager()
deferred_manager = DeferredOperationManager()


def parallel_operation(name: str, dependencies: List[str] = None, critical: bool = True):
    """Decorator to register functions as parallel startup operations"""
    def decorator(func):
        startup_manager.register_operation(name, func, dependencies, critical)
        return func
    return decorator


def deferred_operation(name: str, delay: float = 5.0, retry_count: int = 3):
    """Decorator to register functions as deferred operations"""
    def decorator(func):
        deferred_manager.defer_operation(name, func, delay, retry_count)
        return func
    return decorator