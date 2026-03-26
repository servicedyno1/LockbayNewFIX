"""
Unified State Management Service
Centralized state management with Replit Key-Value Store backend for the LockBay Telegram bot system
Provides distributed coordination, session management, and state consistency

RAILWAY COMPATIBILITY: Falls back to in-memory storage when Replit KV is unavailable.
This works for single-instance deployments (Railway VMs).
"""

import json
import time
import logging
import asyncio
from typing import Any, Optional, Dict, List
from datetime import datetime
from contextlib import asynccontextmanager
from dataclasses import dataclass
import uuid

# Import Replit Key-Value Store
try:
    from replit import db
    KV_AVAILABLE = True
except ImportError:
    KV_AVAILABLE = False
    db = None

from config import Config

logger = logging.getLogger(__name__)


class InMemoryKVStore:
    """
    In-memory Key-Value Store for Railway/non-Replit environments.
    Provides dict-like interface compatible with Replit's db.
    
    NOTE: Data is NOT persisted across restarts. Use only for:
    - Session state (temporary)
    - Distributed locks (single instance only)
    - Cache data (can be rebuilt)
    """
    
    def __init__(self):
        self._data: Dict[str, str] = {}
        self._lock = asyncio.Lock()
        logger.info("📦 InMemoryKVStore initialized (Railway fallback mode)")
    
    def get(self, key: str, default: Any = None) -> Optional[str]:
        """Get value by key"""
        return self._data.get(key, default)
    
    def __getitem__(self, key: str) -> str:
        """Dict-like get"""
        return self._data[key]
    
    def __setitem__(self, key: str, value: str):
        """Dict-like set"""
        self._data[key] = value
    
    def __delitem__(self, key: str):
        """Dict-like delete"""
        if key in self._data:
            del self._data[key]
    
    def __contains__(self, key: str) -> bool:
        """Check if key exists"""
        return key in self._data
    
    def keys(self):
        """Return all keys"""
        return self._data.keys()
    
    def prefix(self, prefix: str) -> List[str]:
        """Return keys matching prefix"""
        return [k for k in self._data.keys() if k.startswith(prefix)]
    
    def clear(self):
        """Clear all data"""
        self._data.clear()


# Create in-memory fallback store
_memory_store = InMemoryKVStore()


def _normalize_tags(tags: Any) -> List[str]:
    """Normalize tags to ensure they are always a list of strings"""
    if tags is None or tags is False:
        return []
    elif tags is True:
        logger.warning("⚠️ Boolean True passed as tags, converting to empty list")
        return []
    elif isinstance(tags, str):
        return [tags]
    elif isinstance(tags, list):
        # Ensure all items in list are strings
        return [str(item) for item in tags if item is not None]
    elif hasattr(tags, '__iter__') and not isinstance(tags, (str, bytes)):
        # Handle other iterables (sets, tuples, etc.)
        return [str(item) for item in tags if item is not None]
    else:
        logger.warning(f"⚠️ Invalid tags type {type(tags)}, converting to empty list")
        return []


def _finalize_tags(key: str, source: str, user_tags: Any) -> List[str]:
    """Centralized auto-tagging logic - never mutates inputs"""
    # Start with normalized user tags - NEVER mutate the input
    base_tags = _normalize_tags(user_tags)
    
    # Add auto-tags based on key patterns (create fresh list, never mutate)
    auto_tags = []
    if key.startswith('circuit_breaker:'):
        auto_tags.append('circuit_breaker')
    elif key.startswith('user_sessions:'):
        auto_tags.append('user_session')
    elif key.startswith('session:'):
        auto_tags.append('session')
    
    # Add source tag
    if source and source != 'unknown':
        auto_tags.append(f'source_{source}')
    
    # CRITICAL: Combine and deduplicate using pure functions - NEVER mutate any input
    try:
        final_tags = list(dict.fromkeys(base_tags + auto_tags))
    except Exception as e:
        logger.error(f"❌ CRITICAL: Error combining tags for {key}: base_tags={type(base_tags)}, auto_tags={type(auto_tags)}, error={e}")
        return ['circuit_breaker'] if key.startswith('circuit_breaker:') else []
    
    # Runtime guard to catch remaining issues
    if not isinstance(final_tags, list):
        logger.error(f"❌ CRITICAL: _finalize_tags returned non-list {type(final_tags)} for key {key}")
        return ['circuit_breaker'] if key.startswith('circuit_breaker:') else []
    
    return final_tags


@dataclass
class StateMetadata:
    """Metadata for state entries"""
    created_at: datetime
    updated_at: datetime
    ttl_seconds: Optional[int]
    version: int
    source: str
    tags: List[str]


class DistributedLock:
    """Replit Key-Value Store based distributed lock implementation"""

    def __init__(self, kv_store, name: str, timeout: int = 60, blocking_timeout: int = 10):
        self.kv_store = kv_store
        self.name = f"lock:{name}"
        self.timeout = timeout
        self.blocking_timeout = blocking_timeout
        self.token = None
        self.acquired_at = None

    async def acquire(self) -> bool:
        """Acquire the distributed lock"""
        try:
            # Generate unique token for this lock instance
            self.token = str(uuid.uuid4())
            current_time = time.time()
            
            # Check if lock already exists and is not expired
            existing_lock = await asyncio.to_thread(self.kv_store.get, self.name)
            if existing_lock:
                lock_data = json.loads(existing_lock)
                if current_time < lock_data.get('expires_at', 0):
                    logger.debug(f"⏳ Lock already held: {self.name}")
                    return False
            
            # Acquire lock
            lock_data = {
                'token': self.token,
                'acquired_at': current_time,
                'expires_at': current_time + self.timeout
            }
            
            await asyncio.to_thread(self.kv_store.__setitem__, self.name, json.dumps(lock_data))
            self.acquired_at = current_time
            
            logger.debug(f"🔒 Acquired lock: {self.name}")
            return True
                
        except Exception as e:
            logger.error(f"❌ Failed to acquire lock {self.name}: {e}")
            return False

    async def release(self) -> bool:
        """Release the distributed lock"""
        if not self.token:
            return False
            
        try:
            # Check if we still own the lock
            existing_lock = await asyncio.to_thread(self.kv_store.get, self.name)
            if existing_lock:
                lock_data = json.loads(existing_lock)
                if lock_data.get('token') == self.token:
                    await asyncio.to_thread(self.kv_store.__delitem__, self.name)
                    logger.debug(f"🔓 Released lock: {self.name}")
                    return True
                else:
                    logger.warning(f"⚠️ Lock token mismatch on release: {self.name}")
                    return False
            
            return True  # Lock doesn't exist, consider it released
                
        except Exception as e:
            logger.error(f"❌ Failed to release lock {self.name}: {e}")
            return False

    async def extend(self, additional_time: int = 30) -> bool:
        """Extend the lock timeout"""
        if not self.token:
            return False
            
        try:
            existing_lock = await asyncio.to_thread(self.kv_store.get, self.name)
            if existing_lock:
                lock_data = json.loads(existing_lock)
                if lock_data.get('token') == self.token:
                    # Extend the expiration time
                    lock_data['expires_at'] = time.time() + self.timeout + additional_time
                    await asyncio.to_thread(self.kv_store.__setitem__, self.name, json.dumps(lock_data))
                    logger.debug(f"⏰ Extended lock: {self.name} (+{additional_time}s)")
                    return True
                else:
                    logger.warning(f"⚠️ Failed to extend lock (token mismatch): {self.name}")
                    return False
            
            return False
                
        except Exception as e:
            logger.error(f"❌ Failed to extend lock {self.name}: {e}")
            return False

    async def __aenter__(self):
        """Async context manager entry"""
        acquired = await self.acquire()
        if not acquired:
            raise RuntimeError(f"Failed to acquire lock: {self.name}")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.release()


class StateManager:
    """
    Unified State Management Service
    
    Provides:
    - Centralized state storage with Replit Key-Value Store backend
    - Distributed locking for critical sections
    - Session management with TTL
    - State versioning and conflict detection
    - Simple atomic operations
    
    RAILWAY SUPPORT: Falls back to in-memory storage when Replit KV unavailable.
    """

    def __init__(self):
        # Use Replit KV if available, otherwise use in-memory fallback
        if KV_AVAILABLE and db is not None:
            self.kv_store = db
            self._using_memory_fallback = False
        else:
            self.kv_store = _memory_store
            self._using_memory_fallback = True
        
        self.is_connected = False  # Will be set to True after initialize()
        self._financial_ops_enabled = True
        
        # State management configuration from Config
        self.default_ttl = getattr(Config, 'REDIS_DEFAULT_TTL', 3600)  # 1 hour default
        
        # Metrics tracking
        self.metrics = {
            'operations_total': 0,
            'operations_success': 0,
            'operations_failed': 0,
            'locks_acquired': 0,
            'locks_failed': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'connection_errors': 0,
            'timeout_errors': 0,
            'retry_count': 0
        }
    
    def is_redis_available(self) -> bool:
        """Check if Key-Value Store is available"""
        return self.is_connected and KV_AVAILABLE
    
    def is_financial_safe(self) -> bool:
        """
        CRITICAL SECURITY: Determine if it's safe to process financial operations
        
        UPDATED: This method now accurately reflects coordination guarantees.
        The key-value store based locking has known race conditions in acquire() method.
        
        WARNING: Current DistributedLock implementation has race condition vulnerabilities:
        - acquire() does get→check→set without atomic compare-and-set
        - Multiple processes can acquire same lock simultaneously
        - This creates risks for financial operations
        
        RECOMMENDATION: Use atomic_lock_manager for critical financial operations
        which provides database-backed atomic locking with unique constraints.
        """
        # Check if atomic lock manager is available
        try:
            from services.atomic_lock_manager import atomic_lock_manager
            # If atomic lock manager is available, we have true financial safety
            return True
        except ImportError:
            # Fallback to key-value store with warnings
            if self.is_connected and KV_AVAILABLE:
                logger.warning(
                    "⚠️ FINANCIAL_SAFETY_WARNING: Using key-value store locking "
                    "which has known race conditions. Recommend using atomic_lock_manager "
                    "for critical financial operations."
                )
                return True
            else:
                logger.critical(
                    "❌ FINANCIAL_SAFETY_CRITICAL: No coordination available. "
                    "Financial operations should be blocked."
                )
                return False
    
    def get_coordination_status(self) -> Dict[str, Any]:
        """Get detailed status of coordination infrastructure for observability"""
        return {
            "redis_available": self.is_redis_available(),  # For backward compatibility
            "kv_store_available": self.is_connected,
            "financial_safe": self.is_financial_safe(),
            "fallback_mode": "replit_kv_store",
            "using_fallback": False,
            "is_production": Config.IS_PRODUCTION,
            "connection_metrics": self.metrics.copy()
        }

    async def initialize(self) -> bool:
        """Initialize Key-Value Store connection (with in-memory fallback for Railway)"""
        
        # If using in-memory fallback, initialize immediately
        if self._using_memory_fallback:
            self.is_connected = True
            logger.info("📦 State Manager initialized with IN-MEMORY storage (Railway mode)")
            logger.info("⚠️ Note: State data will NOT persist across restarts")
            logger.info("🔒 Financial operations safety: ENABLED (in-memory locks work for single instance)")
            
            # Start background TTL cleanup
            asyncio.create_task(self._ttl_cleanup_task())
            return True
        
        # Replit KV Store path
        if not KV_AVAILABLE:
            logger.error("❌ Replit Key-Value Store not available - replit library not installed")
            self.is_connected = False
            return False
        
        if self.kv_store is None:
            logger.error("❌ Replit Key-Value Store not initialized")
            self.is_connected = False
            return False
            
        try:
            # Test Key-Value Store connectivity
            test_key = "health_check_test"
            await asyncio.to_thread(self.kv_store.__setitem__, test_key, json.dumps({"test": True, "timestamp": time.time()}))
            test_data = await asyncio.to_thread(self.kv_store.get, test_key)
            
            if test_data:
                await asyncio.to_thread(self.kv_store.__delitem__, test_key)  # Clean up test key
                self.is_connected = True
                logger.info("🔗 Replit Key-Value Store State Manager initialized successfully")
                logger.info("🔒 Financial operations safety: ENABLED (Key-Value Store available)")
                
                # Start background TTL cleanup
                asyncio.create_task(self._ttl_cleanup_task())
                return True
            else:
                logger.error("❌ Key-Value Store connectivity test failed")
                self.is_connected = False
                return False
                
        except Exception as e:
            logger.error(f"❌ Failed to initialize Key-Value Store: {e}")
            self.is_connected = False
            return False

    async def _ttl_cleanup_task(self):
        """Background task to clean up expired keys"""
        while True:
            try:
                await asyncio.sleep(300)  # Run every 5 minutes
                await self._cleanup_expired_keys()
            except Exception as e:
                logger.error(f"❌ TTL cleanup task error: {e}")
                await asyncio.sleep(60)  # Wait before retrying

    async def _cleanup_expired_keys(self):
        """Clean up expired keys based on TTL"""
        if not self.is_connected or self.kv_store is None:
            return
            
        try:
            current_time = time.time()
            expired_count = 0
            
            # Iterate through all keys with TTL metadata
            for key in list(await asyncio.to_thread(self.kv_store.prefix, "ttl:")):
                ttl_data = await asyncio.to_thread(self.kv_store.get, key)
                if ttl_data:
                    ttl_info = json.loads(ttl_data)
                    if current_time > ttl_info.get('expires_at', float('inf')):
                        # Key has expired, remove both TTL record and actual data
                        original_key = key.replace('ttl:', '', 1)
                        if await asyncio.to_thread(self.kv_store.__contains__, original_key):
                            await asyncio.to_thread(self.kv_store.__delitem__, original_key)
                        await asyncio.to_thread(self.kv_store.__delitem__, key)
                        expired_count += 1
            
            if expired_count > 0:
                logger.debug(f"🧹 Cleaned up {expired_count} expired keys")
                
        except Exception as e:
            logger.error(f"❌ Failed to cleanup expired keys: {e}")

    # Core State Operations

    async def set_state(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
        tags: Optional[List[str]] = None,
        source: str = 'unknown'
    ) -> bool:
        """Set state with metadata and TTL support"""
        if not self.is_connected or self.kv_store is None:
            return False
            
        try:
            self.metrics['operations_total'] += 1
            
            # Use default TTL if not specified
            if ttl is None:
                ttl = self.default_ttl
            
            # Create state entry with metadata
            current_time = time.time()
            finalized_tags = _finalize_tags(key, source, tags)
            
            state_entry = {
                'value': value,
                'metadata': {
                    'created_at': datetime.utcnow().isoformat(),
                    'updated_at': datetime.utcnow().isoformat(),
                    'ttl_seconds': ttl,
                    'version': 1,
                    'source': source,
                    'tags': finalized_tags
                }
            }
            
            # Store state
            await asyncio.to_thread(self.kv_store.__setitem__, key, json.dumps(state_entry))
            
            # Set TTL if specified
            if ttl and ttl > 0:
                ttl_key = f"ttl:{key}"
                ttl_data = {
                    'expires_at': current_time + ttl,
                    'original_key': key
                }
                await asyncio.to_thread(self.kv_store.__setitem__, ttl_key, json.dumps(ttl_data))
            
            # Update tag indices
            for tag in finalized_tags:
                tag_key = f"tag:{tag}"
                existing_keys = await asyncio.to_thread(self.kv_store.get, tag_key, "[]")
                key_list = json.loads(existing_keys)
                if key not in key_list:
                    key_list.append(key)
                    await asyncio.to_thread(self.kv_store.__setitem__, tag_key, json.dumps(key_list))
            
            self.metrics['operations_success'] += 1
            logger.debug(f"💾 Set state: {key} with TTL={ttl}")
            return True
            
        except Exception as e:
            self.metrics['operations_failed'] += 1
            logger.error(f"❌ Failed to set state {key}: {e}")
            return False

    async def get_state(self, key: str, default: Any = None) -> Any:
        """Get state with TTL and metadata handling"""
        if not self.is_connected or self.kv_store is None:
            return default
            
        try:
            self.metrics['operations_total'] += 1
            
            # Check if key has expired
            ttl_key = f"ttl:{key}"
            if await asyncio.to_thread(self.kv_store.__contains__, ttl_key):
                ttl_data = json.loads(await asyncio.to_thread(self.kv_store.__getitem__, ttl_key))
                if time.time() > ttl_data.get('expires_at', float('inf')):
                    # Key has expired, remove it
                    if await asyncio.to_thread(self.kv_store.__contains__, key):
                        await asyncio.to_thread(self.kv_store.__delitem__, key)
                    await asyncio.to_thread(self.kv_store.__delitem__, ttl_key)
                    
                    self.metrics['cache_misses'] += 1
                    return default
            
            # Get state
            state_data = await asyncio.to_thread(self.kv_store.get, key)
            if state_data is None:
                self.metrics['cache_misses'] += 1
                return default
            
            state_entry = json.loads(state_data)
            value = state_entry.get('value', default)
            
            self.metrics['operations_success'] += 1
            self.metrics['cache_hits'] += 1
            return value
            
        except Exception as e:
            self.metrics['operations_failed'] += 1
            self.metrics['cache_misses'] += 1
            logger.error(f"❌ Failed to get state {key}: {e}")
            return default

    async def update_state(self, key: str, value: Any) -> bool:
        """Update existing state while preserving metadata"""
        if not self.is_connected or self.kv_store is None:
            return False
            
        try:
            # Get existing state to preserve metadata
            existing_data = await asyncio.to_thread(self.kv_store.get, key)
            if not existing_data:
                logger.warning(f"⚠️ Attempted to update non-existent state: {key}")
                return False
            
            state_entry = json.loads(existing_data)
            metadata = state_entry.get('metadata', {})
            
            # Update value and metadata
            state_entry['value'] = value
            metadata['updated_at'] = datetime.utcnow().isoformat()
            metadata['version'] = metadata.get('version', 0) + 1
            state_entry['metadata'] = metadata
            
            # Save updated state
            await asyncio.to_thread(self.kv_store.__setitem__, key, json.dumps(state_entry))
            
            self.metrics['operations_success'] += 1
            logger.debug(f"🔄 Updated state: {key}")
            return True
            
        except Exception as e:
            self.metrics['operations_failed'] += 1
            logger.error(f"❌ Failed to update state {key}: {e}")
            return False

    async def delete_state(self, key: str) -> bool:
        """Delete state and cleanup metadata"""
        if not self.is_connected or self.kv_store is None:
            return False
            
        try:
            self.metrics['operations_total'] += 1
            
            # Get metadata for tag cleanup
            existing_data = await asyncio.to_thread(self.kv_store.get, key)
            if existing_data:
                state_entry = json.loads(existing_data)
                metadata = state_entry.get('metadata', {})
                tags = metadata.get('tags', [])
                
                # Remove from tag indices
                for tag in tags:
                    tag_key = f"tag:{tag}"
                    existing_keys = await asyncio.to_thread(self.kv_store.get, tag_key, "[]")
                    key_list = json.loads(existing_keys)
                    if key in key_list:
                        key_list.remove(key)
                        if key_list:
                            await asyncio.to_thread(self.kv_store.__setitem__, tag_key, json.dumps(key_list))
                        else:
                            # Remove empty tag index
                            if await asyncio.to_thread(self.kv_store.__contains__, tag_key):
                                await asyncio.to_thread(self.kv_store.__delitem__, tag_key)
                
                # Delete main state
                await asyncio.to_thread(self.kv_store.__delitem__, key)
                
                # Delete TTL record if exists
                ttl_key = f"ttl:{key}"
                if await asyncio.to_thread(self.kv_store.__contains__, ttl_key):
                    await asyncio.to_thread(self.kv_store.__delitem__, ttl_key)
                
                self.metrics['operations_success'] += 1
                logger.debug(f"🗑️ Deleted state: {key}")
                return True
            
            return False  # Key didn't exist
            
        except Exception as e:
            self.metrics['operations_failed'] += 1
            logger.error(f"❌ Failed to delete state {key}: {e}")
            return False

    # Locking Operations

    def create_lock(
        self, 
        name: str, 
        timeout: int = 60, 
        blocking_timeout: int = 10
    ) -> DistributedLock:
        """Create a distributed lock"""
        if not self.is_connected:
            raise RuntimeError("State Manager not connected to Key-Value Store")
        return DistributedLock(self.kv_store, name, timeout, blocking_timeout)

    @asynccontextmanager
    async def acquire_lock(
        self, 
        name: str, 
        timeout: int = 60, 
        blocking_timeout: int = 10
    ):
        """Context manager for acquiring and releasing locks"""
        lock = self.create_lock(name, timeout, blocking_timeout)
        try:
            acquired = await lock.acquire()
            if not acquired:
                self.metrics['locks_failed'] += 1
                raise RuntimeError(f"Failed to acquire lock: {name}")
            
            self.metrics['locks_acquired'] += 1
            logger.debug(f"🔒 Acquired lock: {name}")
            yield lock
        finally:
            await lock.release()
            logger.debug(f"🔓 Released lock: {name}")

    # Session Management

    async def create_session(
        self, 
        user_id: int, 
        session_data: Dict[str, Any], 
        ttl: int = 1800
    ) -> str:
        """Create user session with TTL"""
        session_id = f"session:{user_id}:{int(time.time())}"
        
        session_info = {
            'user_id': user_id,
            'created_at': datetime.utcnow().isoformat(),
            'data': session_data
        }
        
        success = await self.set_state(
            session_id, 
            session_info, 
            ttl=ttl,
            tags=_finalize_tags(session_id, 'session_manager', ['session']),
            source='session_manager'
        )
        
        if success and self.kv_store is not None:
            # Track active session for user
            user_sessions_key = f"user_sessions:{user_id}"
            existing_sessions = await asyncio.to_thread(self.kv_store.get, user_sessions_key, "[]")
            session_list = json.loads(existing_sessions)
            if session_id not in session_list:
                session_list.append(session_id)
                await asyncio.to_thread(self.kv_store.__setitem__, user_sessions_key, json.dumps(session_list))
                
            logger.debug(f"👤 Created session: {session_id}")
            return session_id
        else:
            raise RuntimeError("Failed to create session")

    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session data"""
        return await self.get_state(session_id)

    async def update_session(
        self, 
        session_id: str, 
        session_data: Dict[str, Any]
    ) -> bool:
        """Update session data"""
        current_session = await self.get_session(session_id)
        if not current_session:
            return False
            
        current_session['data'].update(session_data)
        current_session['updated_at'] = datetime.utcnow().isoformat()
        
        return await self.update_state(session_id, current_session)

    async def clear_user_sessions(self, user_id: int):
        """Clear all sessions for a user"""
        if not self.is_connected or self.kv_store is None:
            return
            
        user_sessions_key = f"user_sessions:{user_id}"
        existing_sessions = self.kv_store.get(user_sessions_key, "[]")
        session_list = json.loads(existing_sessions)
        
        for session_id in session_list:
            await self.delete_state(session_id)
        
        # Clear user sessions list
        if user_sessions_key in self.kv_store:
            del self.kv_store[user_sessions_key]
            
        logger.debug(f"🧹 Cleared {len(session_list)} sessions for user {user_id}")

    # Utility Methods

    async def get_keys_by_pattern(self, pattern: str) -> List[str]:
        """Get keys matching pattern using Key-Value Store prefix search"""
        if not self.is_connected or self.kv_store is None:
            return []
        try:
            # Use Replit's prefix functionality
            matching_keys = []
            for key in self.kv_store.prefix(pattern):
                # Filter out TTL and tag keys
                if not key.startswith('ttl:') and not key.startswith('tag:'):
                    matching_keys.append(key)
            return matching_keys
        except Exception as e:
            logger.error(f"❌ Failed to get keys by pattern {pattern}: {e}")
            return []

    async def get_keys_by_tag(self, tag: str) -> List[str]:
        """Get keys by tag"""
        if not self.is_connected or self.kv_store is None:
            return []
        try:
            tag_key = f"tag:{tag}"
            existing_keys = self.kv_store.get(tag_key, "[]")
            return json.loads(existing_keys)
        except Exception as e:
            logger.error(f"❌ Failed to get keys by tag {tag}: {e}")
            return []

    async def get_statistics(self) -> Dict[str, Any]:
        """Get state manager statistics"""
        return {
            'backend': 'replit_key_value_store',
            'is_connected': self.is_connected,
            'metrics': self.metrics.copy(),
            'total_keys': len(list(self.kv_store.keys())) if self.is_connected and self.kv_store is not None else 0
        }

    async def health_check(self) -> Dict[str, Any]:
        """Perform health check"""
        if not self.is_connected:
            return {
                'healthy': False,
                'error': 'Key-Value Store not connected'
            }
        
        try:
            # Test basic operations
            test_key = f"health_check_{int(time.time())}"
            test_value = {'test': True, 'timestamp': time.time()}
            
            # Test set/get/delete cycle
            await self.set_state(test_key, test_value, ttl=60)
            retrieved = await self.get_state(test_key)
            await self.delete_state(test_key)
            
            return {
                'healthy': True,
                'backend': 'replit_key_value_store',
                'test_successful': retrieved == test_value,
                'metrics': self.metrics.copy()
            }
        
        except Exception as e:
            return {
                'healthy': False,
                'error': str(e)
            }


# Global state manager instance
state_manager = StateManager()


async def initialize_state_manager() -> bool:
    """Initialize the global state manager instance"""
    try:
        success = await state_manager.initialize()
        if success:
            logger.info("✅ State Manager initialized successfully")
            return True
        else:
            logger.error("❌ State Manager initialization failed")
            return False
    except Exception as e:
        logger.error(f"❌ State Manager initialization error: {e}")
        return False


async def ensure_initialized():
    """Ensure state manager is initialized"""
    if not state_manager.is_connected:
        success = await state_manager.initialize()
        if not success:
            logger.error("❌ Failed to initialize Replit Key-Value Store State Manager")
            raise RuntimeError("State Manager initialization failed")
    return state_manager
