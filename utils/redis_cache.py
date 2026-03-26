"""Replit Key-Value Store based caching system for performance optimization"""

import logging
import json
import pickle
import asyncio
from typing import Any, Optional, Dict
from datetime import datetime
import hashlib
import os
import time
import hmac

# Import Replit Key-Value Store
try:
    from replit import db
    KV_AVAILABLE = True
except ImportError:
    KV_AVAILABLE = False
    db = None

logger = logging.getLogger(__name__)


class CacheConfig:
    """Configuration for Key-Value Store cache"""

    def __init__(self):
        # Keep Redis config for backward compatibility but not used
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.redis_host = os.getenv("REDIS_HOST", "localhost")
        self.redis_port = int(os.getenv("REDIS_PORT", "6379"))
        self.redis_db = int(os.getenv("REDIS_DB", "0"))
        self.redis_password = os.getenv("REDIS_PASSWORD")
        self.default_ttl = int(os.getenv("CACHE_DEFAULT_TTL", "3600"))  # 1 hour
        self.key_prefix = os.getenv("CACHE_KEY_PREFIX", "cache:")


class ReplitCacheManager:
    """Advanced Replit Key-Value Store cache manager with distributed caching support"""

    def __init__(self, config: Optional[CacheConfig] = None):
        self.config = config or CacheConfig()
        self.kv_store = db
        self.is_connected = KV_AVAILABLE
        self.stats = {"hits": 0, "misses": 0, "sets": 0, "deletes": 0, "errors": 0}
        self._hmac_secret = self._get_or_create_hmac_secret()

    async def connect(self):
        """Connect to Replit Key-Value Store"""
        try:
            if not KV_AVAILABLE:
                logger.error("❌ Replit Key-Value Store not available - replit library not installed")
                self.is_connected = False
                return False
            
            if self.kv_store is None:
                logger.error("❌ Replit Key-Value Store not initialized")
                self.is_connected = False
                return False
            
            # Test Key-Value Store connectivity
            test_key = f"{self.config.key_prefix}health_check_test"
            test_data = {"test": True, "timestamp": time.time()}
            await asyncio.to_thread(self.kv_store.__setitem__, test_key, json.dumps(test_data))
            retrieved_data = await asyncio.to_thread(self.kv_store.get, test_key)
            
            if retrieved_data:
                await asyncio.to_thread(self.kv_store.__delitem__, test_key)  # Clean up test key
                self.is_connected = True
                logger.info("✅ Replit Key-Value Store cache connected successfully")
                
                # Start background TTL cleanup
                asyncio.create_task(self._ttl_cleanup_task())
                return True
            else:
                logger.error("❌ Key-Value Store connectivity test failed")
                self.is_connected = False
                return False
                
        except Exception as e:
            logger.error(f"❌ Key-Value Store connection failed: {e}")
            self.is_connected = False
            return False

    async def disconnect(self):
        """Disconnect from Key-Value Store"""
        self.is_connected = False
        logger.info("Key-Value Store cache disconnected")

    async def _ttl_cleanup_task(self):
        """Background task to clean up expired keys"""
        while self.is_connected:
            try:
                await asyncio.sleep(300)  # Run every 5 minutes
                await self._cleanup_expired_keys()
            except Exception as e:
                logger.error(f"❌ TTL cleanup task error: {e}")
                await asyncio.sleep(60)
    
    async def _cleanup_expired_keys(self):
        """Clean up expired keys"""
        try:
            current_time = time.time()
            expired_count = 0
            
            # Get all cache keys with our prefix
            prefix = self.config.key_prefix
            
            # Use prefix iteration (implementation-specific)
            try:
                # Try to iterate with prefix if available
                keys_to_check = []
                for key in list(await asyncio.to_thread(list, self.kv_store.keys())):
                    if key.startswith(prefix):
                        keys_to_check.append(key)
                
                for key in keys_to_check:
                    cache_entry = await asyncio.to_thread(self.kv_store.get, key)
                    if cache_entry:
                        try:
                            entry_data = json.loads(cache_entry)
                            if "expires_at" in entry_data and current_time > entry_data["expires_at"]:
                                await asyncio.to_thread(self.kv_store.__delitem__, key)
                                expired_count += 1
                        except (json.JSONDecodeError, KeyError):
                            continue
                
                if expired_count > 0:
                    logger.debug(f"🧹 Cleaned up {expired_count} expired cache keys")
                    
            except Exception as e:
                logger.debug(f"Cache cleanup iteration failed: {e}")
                
        except Exception as e:
            logger.error(f"❌ Failed to cleanup expired cache keys: {e}")

    def _get_or_create_hmac_secret(self) -> bytes:
        """Get or create HMAC secret for pickle integrity checks"""
        try:
            # Try to get from environment first
            secret_hex = os.getenv("CACHE_HMAC_SECRET")
            if secret_hex:
                return bytes.fromhex(secret_hex)
            
            # Generate a new secret (in production, this should be persisted)
            # For now, use a deterministic secret based on system info
            # In production, store this in secrets management
            system_id = os.getenv("REPL_ID", "default")
            secret = hashlib.sha256(f"cache_hmac_{system_id}".encode()).digest()
            logger.info("🔐 Generated HMAC secret for cache integrity checks")
            return secret
        except Exception as e:
            logger.error(f"Error getting HMAC secret: {e}")
            # Fallback to a default (insecure, but better than nothing)
            return b"default_cache_secret_key_change_me"
    
    def _generate_key(self, key: str, namespace: Optional[str] = None) -> str:
        """Generate cache key with prefix and namespace"""
        parts = [self.config.key_prefix]

        if namespace:
            parts.append(namespace)

        parts.append(key)

        return ":".join(parts)

    def _serialize_value(self, value: Any) -> str:
        """Serialize value for storage with HMAC integrity checks for pickle"""
        try:
            # Try JSON first for simple types (no HMAC needed - JSON is safe)
            if isinstance(value, (str, int, float, bool, list, dict)) or value is None:
                return json.dumps(value)
            else:
                # Use pickle for complex objects with HMAC signature
                import base64
                pickled_data = pickle.dumps(value)
                
                # Add HMAC signature for integrity
                signature = hmac.new(
                    self._hmac_secret,
                    pickled_data,
                    hashlib.sha256
                ).hexdigest()
                
                # Format: HMAC:<signature>:<base64_data>
                signed_data = f"HMAC:{signature}:{base64.b64encode(pickled_data).decode('utf-8')}"
                return signed_data
        except Exception as e:
            logger.warning(f"Serialization failed, using pickle with HMAC: {e}")
            import base64
            pickled_data = pickle.dumps(value)
            signature = hmac.new(
                self._hmac_secret,
                pickled_data,
                hashlib.sha256
            ).hexdigest()
            signed_data = f"HMAC:{signature}:{base64.b64encode(pickled_data).decode('utf-8')}"
            return signed_data

    def _deserialize_value(self, data: str) -> Any:
        """Deserialize value from storage with HMAC verification"""
        try:
            # Try JSON first
            return json.loads(data)
        except json.JSONDecodeError:
            # Check if it's HMAC-signed pickle data
            if data.startswith("HMAC:"):
                try:
                    import base64
                    parts = data.split(":", 2)
                    if len(parts) != 3:
                        logger.error("Invalid HMAC-signed pickle format")
                        return None
                    
                    _, stored_signature, base64_data = parts
                    pickled_data = base64.b64decode(base64_data.encode('utf-8'))
                    
                    # Verify HMAC signature
                    expected_signature = hmac.new(
                        self._hmac_secret,
                        pickled_data,
                        hashlib.sha256
                    ).hexdigest()
                    
                    if not hmac.compare_digest(stored_signature, expected_signature):
                        logger.error("HMAC verification failed - pickle data may be tampered")
                        return None
                    
                    # HMAC verified, safe to unpickle
                    return pickle.loads(pickled_data)
                except Exception as e:
                    logger.error(f"HMAC-signed pickle deserialization failed: {e}")
                    return None
            else:
                # Legacy pickle data without HMAC (backward compatibility)
                # Log warning and attempt to deserialize
                logger.warning("Deserializing legacy pickle data without HMAC verification")
                try:
                    import base64
                    pickled_data = base64.b64decode(data.encode('utf-8'))
                    return pickle.loads(pickled_data)
                except Exception as e:
                    logger.error(f"Legacy pickle deserialization failed: {e}")
                    return None

    async def get(
        self, key: str, namespace: Optional[str] = None, default: Any = None
    ) -> Any:
        """Get value from cache"""
        if not self.is_connected:
            return default

        try:
            cache_key = self._generate_key(key, namespace)
            cache_entry = await asyncio.to_thread(self.kv_store.get, cache_key)

            if cache_entry is None:
                self.stats["misses"] += 1
                return default
                
            # Parse cache entry
            entry_data = json.loads(cache_entry)
            
            # Check TTL expiration
            if "expires_at" in entry_data:
                if time.time() > entry_data["expires_at"]:
                    # Expired, remove and return default
                    await asyncio.to_thread(self.kv_store.__delitem__, cache_key)
                    self.stats["misses"] += 1
                    return default
            
            value = self._deserialize_value(entry_data["value"])
            self.stats["hits"] += 1
            return value

        except Exception as e:
            logger.error(f"Cache get error: {e}")
            self.stats["errors"] += 1
            return default

    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
        namespace: Optional[str] = None,
    ) -> bool:
        """Set value in cache"""
        if not self.is_connected:
            return False

        try:
            cache_key = self._generate_key(key, namespace)
            serialized_value = self._serialize_value(value)

            ttl = ttl or self.config.default_ttl
            
            # Create cache entry with TTL
            cache_entry = {
                "value": serialized_value,
                "created_at": time.time()
            }
            
            if ttl and ttl > 0:
                cache_entry["expires_at"] = time.time() + ttl

            await asyncio.to_thread(self.kv_store.__setitem__, cache_key, json.dumps(cache_entry))
            self.stats["sets"] += 1
            return True

        except Exception as e:
            logger.error(f"Cache set error: {e}")
            self.stats["errors"] += 1
            return False

    async def delete(self, key: str, namespace: Optional[str] = None) -> bool:
        """Delete value from cache"""
        if not self.is_connected:
            return False

        try:
            cache_key = self._generate_key(key, namespace)
            
            # Check if key exists
            if await asyncio.to_thread(self.kv_store.__contains__, cache_key):
                await asyncio.to_thread(self.kv_store.__delitem__, cache_key)
                self.stats["deletes"] += 1
                return True
            
            return False

        except Exception as e:
            logger.error(f"Cache delete error: {e}")
            self.stats["errors"] += 1
            return False

    async def delete_pattern(
        self, pattern: str, namespace: Optional[str] = None
    ) -> int:
        """Delete all keys matching pattern"""
        if not self.is_connected:
            return 0

        try:
            cache_pattern = self._generate_key(pattern, namespace)
            deleted_count = 0
            
            # Find keys matching pattern
            keys_to_delete = []
            for key in list(await asyncio.to_thread(list, self.kv_store.keys())):
                if key.startswith(cache_pattern.replace('*', '')):
                    keys_to_delete.append(key)
            
            # Delete matching keys
            for key in keys_to_delete:
                if await asyncio.to_thread(self.kv_store.__contains__, key):
                    await asyncio.to_thread(self.kv_store.__delitem__, key)
                    deleted_count += 1
            
            self.stats["deletes"] += deleted_count
            return deleted_count

        except Exception as e:
            logger.error(f"Cache delete pattern error: {e}")
            self.stats["errors"] += 1
            return 0

    async def exists(self, key: str, namespace: Optional[str] = None) -> bool:
        """Check if key exists in cache"""
        if not self.is_connected:
            return False

        try:
            cache_key = self._generate_key(key, namespace)
            
            if await asyncio.to_thread(self.kv_store.__contains__, cache_key):
                # Check if not expired
                cache_entry = await asyncio.to_thread(self.kv_store.get, cache_key)
                if cache_entry:
                    entry_data = json.loads(cache_entry)
                    if "expires_at" in entry_data:
                        if time.time() > entry_data["expires_at"]:
                            # Expired, remove it
                            await asyncio.to_thread(self.kv_store.__delitem__, cache_key)
                            return False
                    return True
            
            return False

        except Exception as e:
            logger.error(f"Cache exists error: {e}")
            self.stats["errors"] += 1
            return False

    async def expire(self, key: str, ttl: int, namespace: Optional[str] = None) -> bool:
        """Set expiration for existing key"""
        if not self.is_connected:
            return False

        try:
            cache_key = self._generate_key(key, namespace)
            
            cache_entry = await asyncio.to_thread(self.kv_store.get, cache_key)
            if cache_entry:
                entry_data = json.loads(cache_entry)
                entry_data["expires_at"] = time.time() + ttl
                await asyncio.to_thread(self.kv_store.__setitem__, cache_key, json.dumps(entry_data))
                return True
            
            return False

        except Exception as e:
            logger.error(f"Cache expire error: {e}")
            self.stats["errors"] += 1
            return False

    async def increment(
        self, key: str, amount: int = 1, namespace: Optional[str] = None
    ) -> Optional[int]:
        """Increment numeric value"""
        if not self.is_connected:
            return None

        try:
            self._generate_key(key, namespace)
            
            # Get current value
            current_value = await self.get(key, namespace, 0)
            
            # Try to convert to int
            try:
                current_int = int(current_value)
            except (ValueError, TypeError):
                current_int = 0
            
            # Increment and store
            new_value = current_int + amount
            await self.set(key, new_value, namespace=namespace)
            
            return new_value

        except Exception as e:
            logger.error(f"Cache increment error: {e}")
            self.stats["errors"] += 1
            return None

    async def hash_set(
        self, key: str, field: str, value: Any, namespace: Optional[str] = None
    ) -> bool:
        """Set hash field"""
        if not self.is_connected:
            return False

        try:
            cache_key = self._generate_key(key, namespace)
            
            # Get existing hash or create new one
            cache_entry = await asyncio.to_thread(self.kv_store.get, cache_key)
            if cache_entry:
                entry_data = json.loads(cache_entry)
                if "hash_data" not in entry_data:
                    entry_data["hash_data"] = {}
            else:
                entry_data = {
                    "hash_data": {},
                    "created_at": time.time()
                }
            
            # Set the field
            entry_data["hash_data"][field] = self._serialize_value(value)
            
            await asyncio.to_thread(self.kv_store.__setitem__, cache_key, json.dumps(entry_data))
            return True

        except Exception as e:
            logger.error(f"Cache hash set error: {e}")
            self.stats["errors"] += 1
            return False

    async def hash_get(
        self, key: str, field: str, namespace: Optional[str] = None, default: Any = None
    ) -> Any:
        """Get hash field"""
        if not self.is_connected:
            return default

        try:
            cache_key = self._generate_key(key, namespace)
            cache_entry = await asyncio.to_thread(self.kv_store.get, cache_key)
            
            if not cache_entry:
                return default
            
            entry_data = json.loads(cache_entry)
            
            # Check TTL expiration
            if "expires_at" in entry_data:
                if time.time() > entry_data["expires_at"]:
                    # Expired, remove and return default
                    await asyncio.to_thread(self.kv_store.__delitem__, cache_key)
                    return default
            
            hash_data = entry_data.get("hash_data", {})
            if field not in hash_data:
                return default
            
            return self._deserialize_value(hash_data[field])

        except Exception as e:
            logger.error(f"Cache hash get error: {e}")
            self.stats["errors"] += 1
            return default

    async def list_push(
        self, key: str, value: Any, namespace: Optional[str] = None, left: bool = True
    ) -> bool:
        """Push value to list"""
        if not self.is_connected:
            return False

        try:
            cache_key = self._generate_key(key, namespace)
            
            # Get existing list or create new one
            cache_entry = await asyncio.to_thread(self.kv_store.get, cache_key)
            if cache_entry:
                entry_data = json.loads(cache_entry)
                if "list_data" not in entry_data:
                    entry_data["list_data"] = []
            else:
                entry_data = {
                    "list_data": [],
                    "created_at": time.time()
                }
            
            # Push the value
            serialized_value = self._serialize_value(value)
            if left:
                entry_data["list_data"].insert(0, serialized_value)
            else:
                entry_data["list_data"].append(serialized_value)
            
            await asyncio.to_thread(self.kv_store.__setitem__, cache_key, json.dumps(entry_data))
            return True

        except Exception as e:
            logger.error(f"Cache list push error: {e}")
            self.stats["errors"] += 1
            return False

    async def list_pop(
        self,
        key: str,
        namespace: Optional[str] = None,
        left: bool = True,
        default: Any = None,
    ) -> Any:
        """Pop value from list"""
        if not self.is_connected:
            return default

        try:
            cache_key = self._generate_key(key, namespace)
            cache_entry = await asyncio.to_thread(self.kv_store.get, cache_key)
            
            if not cache_entry:
                return default
            
            entry_data = json.loads(cache_entry)
            
            # Check TTL expiration
            if "expires_at" in entry_data:
                if time.time() > entry_data["expires_at"]:
                    # Expired, remove and return default
                    await asyncio.to_thread(self.kv_store.__delitem__, cache_key)
                    return default
            
            list_data = entry_data.get("list_data", [])
            if not list_data:
                return default
            
            # Pop value
            if left:
                popped_value = list_data.pop(0)
            else:
                popped_value = list_data.pop()
            
            # Update the list
            entry_data["list_data"] = list_data
            await asyncio.to_thread(self.kv_store.__setitem__, cache_key, json.dumps(entry_data))
            
            return self._deserialize_value(popped_value)

        except Exception as e:
            logger.error(f"Cache list pop error: {e}")
            self.stats["errors"] += 1
            return default

    async def clear_namespace(self, namespace: str) -> int:
        """Clear all keys in namespace"""
        pattern = f"{namespace}:*"
        return await self.delete_pattern(pattern)

    async def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        stats = self.stats.copy()

        if self.is_connected:
            try:
                # Get Key-Value Store info (basic statistics)
                prefix = self.config.key_prefix
                total_keys = len([k for k in await asyncio.to_thread(list, self.kv_store.keys()) if k.startswith(prefix)])
                
                stats["kv_store_info"] = {
                    "total_cache_keys": total_keys,
                    "connection_status": "connected",
                    "backend": "replit_key_value_store"
                }
            except Exception as e:
                logger.error(f"Failed to get Key-Value Store info: {e}")

        # Calculate hit rate
        total_requests = stats["hits"] + stats["misses"]
        stats["hit_rate"] = (
            (stats["hits"] / total_requests * 100) if total_requests > 0 else 0
        )

        return stats

    async def health_check(self) -> Dict[str, Any]:
        """Check cache health"""
        try:
            if not self.is_connected:
                return {"status": "disconnected", "error": "Not connected to Key-Value Store"}

            # Test basic operations
            test_key = "health_check_test"
            test_value = {"timestamp": datetime.utcnow().isoformat()}

            # Test set
            await self.set(test_key, test_value, ttl=10)

            # Test get
            retrieved = await self.get(test_key)

            # Test delete
            await self.delete(test_key)

            if retrieved == test_value:
                return {"status": "healthy", "message": "All operations successful"}
            else:
                return {"status": "degraded", "error": "Data integrity issue"}

        except Exception as e:
            return {"status": "error", "error": str(e)}


# Decorators for caching
def cache_result(
    ttl: int = 3600,
    namespace: Optional[str] = None,
    key_func: Optional[callable] = None,
):
    """Decorator to cache function results"""

    def decorator(func):
        async def async_wrapper(*args, **kwargs):
            # Generate cache key
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                # Default key generation
                key_data = f"{func.__name__}:{str(args)}:{str(sorted(kwargs.items()))}"
                cache_key = hashlib.md5(key_data.encode()).hexdigest()

            # Try to get from cache
            cached_result = await replit_cache.get(cache_key, namespace)
            if cached_result is not None:
                return cached_result

            # Execute function and cache result
            result = await func(*args, **kwargs)
            await replit_cache.set(cache_key, result, ttl, namespace)

            return result

        def sync_wrapper(*args, **kwargs):
            # For sync functions, create async wrapper
            async def async_func():
                return func(*args, **kwargs)

            return asyncio.run(async_wrapper(*args, **kwargs))

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    return decorator


# Global cache instance
replit_cache = ReplitCacheManager()

# Backward compatibility alias
redis_cache = replit_cache


async def initialize_cache():
    """Initialize Replit Key-Value Store cache on startup"""
    await replit_cache.connect()


async def cleanup_cache():
    """Cleanup cache on shutdown"""
    await replit_cache.disconnect()
