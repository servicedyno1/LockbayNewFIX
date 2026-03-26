"""
Replit Key-Value Store Redis-Compatible Implementation
Key-Value Store based Redis-like functionality using Replit's built-in database
Provides similar API to redis-py with full functionality for state management
"""

import json
import time
import asyncio
import logging
from typing import Any, Dict, Optional, Set

# Import Replit Key-Value Store
try:
    from replit import db
    KV_AVAILABLE = True
except ImportError:
    KV_AVAILABLE = False
    db = None

logger = logging.getLogger(__name__)


class ReplitKVError(Exception):
    """Base exception for Replit Key-Value Store operations"""
    pass


class ReplitKVRedisCompat:
    """
    Replit Key-Value Store Redis-compatible implementation
    Provides Redis-like functionality using Replit's built-in Key-Value Store
    """
    
    def __init__(self):
        self.kv_store = db
        self._lock = asyncio.Lock()
        self._connected = KV_AVAILABLE
        self._stats = {
            'commands_processed': 0,
            'connections_received': 0,
            'keys_expired': 0
        }
        self.key_prefix = "redis_compat:"
        
        if not KV_AVAILABLE:
            logger.error("❌ Replit Key-Value Store not available")
            self._connected = False
        else:
            # Start background cleanup task
            self._cleanup_task = asyncio.create_task(self._cleanup_expired_keys())
            logger.info("🔄 Replit Key-Value Store Redis-compatible interface initialized")
    
    async def _cleanup_expired_keys(self):
        """Background task to clean up expired keys"""
        while self._connected:
            try:
                current_time = time.time()
                expired_keys = []
                
                async with self._lock:
                    # Get all keys with our prefix
                    for key in list(await asyncio.to_thread(list, self.kv_store.keys())):
                        if key.startswith(self.key_prefix):
                            try:
                                data = await asyncio.to_thread(self.kv_store.get, key)
                                if data:
                                    entry = json.loads(data)
                                    if "expires_at" in entry and current_time >= entry["expires_at"]:
                                        expired_keys.append(key)
                            except (json.JSONDecodeError, KeyError):
                                continue
                    
                    # Remove expired keys
                    for key in expired_keys:
                        await asyncio.to_thread(self.kv_store.__delitem__, key)
                        self._stats['keys_expired'] += 1
                
                if expired_keys:
                    logger.debug(f"🧹 Cleaned up {len(expired_keys)} expired keys")
                
                await asyncio.sleep(60)  # Check every minute
                
            except Exception as e:
                logger.error(f"❌ Error in cleanup task: {e}")
                await asyncio.sleep(60)
    
    def _generate_key(self, key: str) -> str:
        """Generate prefixed key"""
        return f"{self.key_prefix}{key}"
    
    async def _is_expired(self, key: str) -> bool:
        """Check if key is expired"""
        try:
            kv_key = self._generate_key(key)
            data = await asyncio.to_thread(self.kv_store.get, kv_key)
            if data:
                entry = json.loads(data)
                if "expires_at" in entry and time.time() >= entry["expires_at"]:
                    await asyncio.to_thread(self.kv_store.__delitem__, kv_key)
                    self._stats['keys_expired'] += 1
                    return True
            return False
        except Exception:
            return False
    
    async def ping(self) -> str:
        """Redis PING command"""
        if not self._connected:
            raise ReplitKVError("Not connected to Key-Value Store")
        
        self._stats['commands_processed'] += 1
        return "PONG"
    
    async def set(self, key: str, value: Any, ex: Optional[int] = None, nx: bool = False) -> bool:
        """Redis SET command"""
        if not self._connected:
            return False
        
        self._stats['commands_processed'] += 1
        
        async with self._lock:
            kv_key = self._generate_key(key)
            
            # Check if key exists for nx (only set if not exists)
            if nx:
                existing_data = await asyncio.to_thread(self.kv_store.get, kv_key)
                if existing_data:
                    try:
                        entry = json.loads(existing_data)
                        if "expires_at" not in entry or time.time() < entry["expires_at"]:
                            return False
                    except (json.JSONDecodeError, KeyError):
                        pass
            
            # Prepare entry
            entry = {
                "value": str(value) if not isinstance(value, str) else value,
                "created_at": time.time()
            }
            
            # Set expiry if provided
            if ex:
                entry["expires_at"] = time.time() + ex
            
            await asyncio.to_thread(self.kv_store.__setitem__, kv_key, json.dumps(entry))
            return True
    
    async def get(self, key: str) -> Optional[str]:
        """Redis GET command"""
        if not self._connected:
            return None
        
        self._stats['commands_processed'] += 1
        
        async with self._lock:
            if await self._is_expired(key):
                return None
            
            kv_key = self._generate_key(key)
            data = await asyncio.to_thread(self.kv_store.get, kv_key)
            if data:
                try:
                    entry = json.loads(data)
                    return entry.get("value")
                except (json.JSONDecodeError, KeyError):
                    return None
            return None
    
    async def delete(self, *keys: str) -> int:
        """Redis DEL command"""
        if not self._connected:
            return 0
        
        self._stats['commands_processed'] += 1
        
        async with self._lock:
            deleted_count = 0
            for key in keys:
                kv_key = self._generate_key(key)
                if await asyncio.to_thread(self.kv_store.__contains__, kv_key):
                    await asyncio.to_thread(self.kv_store.__delitem__, kv_key)
                    deleted_count += 1
            return deleted_count
    
    async def exists(self, key: str) -> int:
        """Redis EXISTS command"""
        if not self._connected:
            return 0
        
        self._stats['commands_processed'] += 1
        
        async with self._lock:
            if await self._is_expired(key):
                return 0
            
            kv_key = self._generate_key(key)
            if await asyncio.to_thread(self.kv_store.__contains__, kv_key):
                return 1
            return 0
    
    async def expire(self, key: str, seconds: int) -> bool:
        """Redis EXPIRE command"""
        if not self._connected:
            return False
        
        self._stats['commands_processed'] += 1
        
        async with self._lock:
            kv_key = self._generate_key(key)
            data = await asyncio.to_thread(self.kv_store.get, kv_key)
            if data:
                try:
                    entry = json.loads(data)
                    entry["expires_at"] = time.time() + seconds
                    await asyncio.to_thread(self.kv_store.__setitem__, kv_key, json.dumps(entry))
                    return True
                except (json.JSONDecodeError, KeyError):
                    return False
            return False
    
    async def ttl(self, key: str) -> int:
        """Redis TTL command"""
        if not self._connected:
            return -2
        
        self._stats['commands_processed'] += 1
        
        async with self._lock:
            if await self._is_expired(key):
                return -2  # Key doesn't exist
            
            kv_key = self._generate_key(key)
            data = await asyncio.to_thread(self.kv_store.get, kv_key)
            if not data:
                return -2  # Key doesn't exist
            
            try:
                entry = json.loads(data)
                if "expires_at" not in entry:
                    return -1  # Key exists but has no expiry
                
                remaining = entry["expires_at"] - time.time()
                return int(remaining) if remaining > 0 else -2
            except (json.JSONDecodeError, KeyError):
                return -2
    
    # Hash operations
    async def hset(self, key: str, field: str, value: str, *args) -> int:
        """Redis HSET command"""
        if not self._connected:
            return 0
        
        self._stats['commands_processed'] += 1
        
        async with self._lock:
            kv_key = self._generate_key(key)
            
            # Get existing hash or create new one
            data = await asyncio.to_thread(self.kv_store.get, kv_key)
            if data:
                try:
                    entry = json.loads(data)
                    hash_data = entry.get("hash_data", {})
                except (json.JSONDecodeError, KeyError):
                    hash_data = {}
                    entry = {"created_at": time.time()}
            else:
                hash_data = {}
                entry = {"created_at": time.time()}
            
            # Handle multiple field-value pairs
            fields_values = [field, value] + list(args)
            if len(fields_values) % 2 != 0:
                raise ReplitKVError("Wrong number of arguments for HSET")
            
            created_count = 0
            for i in range(0, len(fields_values), 2):
                f, v = fields_values[i], fields_values[i + 1]
                if f not in hash_data:
                    created_count += 1
                hash_data[f] = str(v)
            
            entry["hash_data"] = hash_data
            await asyncio.to_thread(self.kv_store.__setitem__, kv_key, json.dumps(entry))
            
            return created_count
    
    async def hget(self, key: str, field: str) -> Optional[str]:
        """Redis HGET command"""
        if not self._connected:
            return None
        
        self._stats['commands_processed'] += 1
        
        async with self._lock:
            if await self._is_expired(key):
                return None
            
            kv_key = self._generate_key(key)
            data = await asyncio.to_thread(self.kv_store.get, kv_key)
            if data:
                try:
                    entry = json.loads(data)
                    hash_data = entry.get("hash_data", {})
                    return hash_data.get(field)
                except (json.JSONDecodeError, KeyError):
                    return None
            return None
    
    async def hgetall(self, key: str) -> Dict[str, str]:
        """Redis HGETALL command"""
        if not self._connected:
            return {}
        
        self._stats['commands_processed'] += 1
        
        async with self._lock:
            if await self._is_expired(key):
                return {}
            
            kv_key = self._generate_key(key)
            data = await asyncio.to_thread(self.kv_store.get, kv_key)
            if data:
                try:
                    entry = json.loads(data)
                    return entry.get("hash_data", {})
                except (json.JSONDecodeError, KeyError):
                    return {}
            return {}
    
    async def hdel(self, key: str, *fields: str) -> int:
        """Redis HDEL command"""
        if not self._connected:
            return 0
        
        self._stats['commands_processed'] += 1
        
        async with self._lock:
            if await self._is_expired(key):
                return 0
            
            kv_key = self._generate_key(key)
            data = await asyncio.to_thread(self.kv_store.get, kv_key)
            if not data:
                return 0
            
            try:
                entry = json.loads(data)
                hash_data = entry.get("hash_data", {})
                
                deleted_count = 0
                for field in fields:
                    if field in hash_data:
                        del hash_data[field]
                        deleted_count += 1
                
                # Update or clean up
                if hash_data:
                    entry["hash_data"] = hash_data
                    await asyncio.to_thread(self.kv_store.__setitem__, kv_key, json.dumps(entry))
                else:
                    await asyncio.to_thread(self.kv_store.__delitem__, kv_key)
                
                return deleted_count
            except (json.JSONDecodeError, KeyError):
                return 0
    
    # Set operations
    async def sadd(self, key: str, *values: str) -> int:
        """Redis SADD command"""
        if not self._connected:
            return 0
        
        self._stats['commands_processed'] += 1
        
        async with self._lock:
            kv_key = self._generate_key(key)
            
            # Get existing set or create new one
            data = await asyncio.to_thread(self.kv_store.get, kv_key)
            if data:
                try:
                    entry = json.loads(data)
                    set_data = set(entry.get("set_data", []))
                except (json.JSONDecodeError, KeyError):
                    set_data = set()
                    entry = {"created_at": time.time()}
            else:
                set_data = set()
                entry = {"created_at": time.time()}
            
            added_count = 0
            for value in values:
                if value not in set_data:
                    set_data.add(value)
                    added_count += 1
            
            entry["set_data"] = list(set_data)
            await asyncio.to_thread(self.kv_store.__setitem__, kv_key, json.dumps(entry))
            
            return added_count
    
    async def srem(self, key: str, *values: str) -> int:
        """Redis SREM command"""
        if not self._connected:
            return 0
        
        self._stats['commands_processed'] += 1
        
        async with self._lock:
            if await self._is_expired(key):
                return 0
            
            kv_key = self._generate_key(key)
            data = await asyncio.to_thread(self.kv_store.get, kv_key)
            if not data:
                return 0
            
            try:
                entry = json.loads(data)
                set_data = set(entry.get("set_data", []))
                
                removed_count = 0
                for value in values:
                    if value in set_data:
                        set_data.remove(value)
                        removed_count += 1
                
                # Update or clean up
                if set_data:
                    entry["set_data"] = list(set_data)
                    await asyncio.to_thread(self.kv_store.__setitem__, kv_key, json.dumps(entry))
                else:
                    await asyncio.to_thread(self.kv_store.__delitem__, kv_key)
                
                return removed_count
            except (json.JSONDecodeError, KeyError):
                return 0
    
    async def smembers(self, key: str) -> Set[str]:
        """Redis SMEMBERS command"""
        if not self._connected:
            return set()
        
        self._stats['commands_processed'] += 1
        
        async with self._lock:
            if await self._is_expired(key):
                return set()
            
            kv_key = self._generate_key(key)
            data = await asyncio.to_thread(self.kv_store.get, kv_key)
            if data:
                try:
                    entry = json.loads(data)
                    return set(entry.get("set_data", []))
                except (json.JSONDecodeError, KeyError):
                    return set()
            return set()
    
    # Lua script support (basic)
    async def eval(self, script: str, numkeys: int, *args) -> Any:
        """Basic Lua script evaluation (limited support)"""
        if not self._connected:
            return None
        
        self._stats['commands_processed'] += 1
        
        # Simple script patterns we support
        if "redis.call('hset'" in script and "redis.call('expire'" in script:
            # This is likely our state setting script
            key, value, metadata, ttl = args[0], args[1], args[2], args[3]
            await self.hset(key, 'value', value, 'metadata', metadata)
            if ttl != '0':
                await self.expire(key, int(ttl))
            return 1
        
        elif "redis.call('get'" in script and "redis.call('del'" in script:
            # This is likely our lock release script
            key, token = args[0], args[1]
            current_token = await self.get(key)
            if current_token == token:
                await self.delete(key)
                return 1
            return 0
        
        elif "redis.call('get'" in script and "redis.call('expire'" in script:
            # This is likely our lock extend script
            key, token, new_ttl = args[0], args[1], args[2]
            current_token = await self.get(key)
            if current_token == token:
                await self.expire(key, int(new_ttl))
                return 1
            return 0
        
        else:
            logger.warning("⚠️ Unsupported Lua script in Key-Value Store mode")
            return None
    
    async def close(self):
        """Close the Key-Value Store implementation"""
        self._connected = False
        if hasattr(self, '_cleanup_task'):
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
        
        logger.info("🔌 Replit Key-Value Store Redis-compatible interface closed")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get Key-Value Store statistics"""
        try:
            # Count keys with our prefix
            total_keys = len([k for k in self.kv_store.keys() if k.startswith(self.key_prefix)])
            
            return {
                **self._stats,
                'total_keys': total_keys,
                'backend': 'replit_key_value_store',
                'connected': self._connected
            }
        except Exception as e:
            logger.error(f"Failed to get Key-Value Store stats: {e}")
            return {
                **self._stats,
                'total_keys': 0,
                'backend': 'replit_key_value_store',
                'connected': self._connected
            }


class ReplitKVConnectionPool:
    """Mock connection pool for Replit Key-Value Store"""
    
    def __init__(self, *args, **kwargs):
        self.kv_client = ReplitKVRedisCompat()
    
    @classmethod
    def from_url(cls, url: str, **kwargs):
        return cls()
    
    async def disconnect(self):
        await self.kv_client.close()


class ReplitKVClient:
    """Redis client wrapper using Replit Key-Value Store implementation"""
    
    def __init__(self, connection_pool=None, **kwargs):
        if connection_pool:
            self.kv_compat = connection_pool.kv_client
        else:
            self.kv_compat = ReplitKVRedisCompat()
        self.decode_responses = kwargs.get('decode_responses', True)
    
    async def ping(self):
        return await self.kv_compat.ping()
    
    async def set(self, key: str, value: Any, ex: Optional[int] = None, nx: bool = False):
        return await self.kv_compat.set(key, value, ex=ex, nx=nx)
    
    async def get(self, key: str):
        return await self.kv_compat.get(key)
    
    async def delete(self, *keys):
        return await self.kv_compat.delete(*keys)
    
    async def exists(self, key: str):
        return await self.kv_compat.exists(key)
    
    async def expire(self, key: str, seconds: int):
        return await self.kv_compat.expire(key, seconds)
    
    async def ttl(self, key: str):
        return await self.kv_compat.ttl(key)
    
    async def hset(self, key: str, field: str, value: str, *args):
        return await self.kv_compat.hset(key, field, value, *args)
    
    async def hget(self, key: str, field: str):
        return await self.kv_compat.hget(key, field)
    
    async def hgetall(self, key: str):
        return await self.kv_compat.hgetall(key)
    
    async def hdel(self, key: str, *fields):
        return await self.kv_compat.hdel(key, *fields)
    
    async def sadd(self, key: str, *values):
        return await self.kv_compat.sadd(key, *values)
    
    async def srem(self, key: str, *values):
        return await self.kv_compat.srem(key, *values)
    
    async def smembers(self, key: str):
        return await self.kv_compat.smembers(key)
    
    async def eval(self, script: str, numkeys: int, *args):
        return await self.kv_compat.eval(script, numkeys, *args)
    
    async def close(self):
        await self.kv_compat.close()
    
    def get_stats(self):
        return self.kv_compat.get_stats()


# Backward compatibility aliases
RedisFallback = ReplitKVRedisCompat
RedisFallbackConnectionPool = ReplitKVConnectionPool  
RedisFallbackClient = ReplitKVClient