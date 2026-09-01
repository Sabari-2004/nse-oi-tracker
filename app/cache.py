# cache.py — In-memory TTL cache (no Redis needed)

import time
from threading import Lock

class TTLCache:
    """Simple thread-safe in-memory cache with per-key TTL."""

    def __init__(self, default_ttl: int = 60):
        self._store: dict = {}
        self._lock = Lock()
        self.default_ttl = default_ttl

    def get(self, key: str):
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.time() > expires_at:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value, ttl: int = None):
        ttl = ttl or self.default_ttl
        with self._lock:
            self._store[key] = (value, time.time() + ttl)

    def delete(self, key: str):
        with self._lock:
            self._store.pop(key, None)

    def clear(self):
        with self._lock:
            self._store.clear()

    def keys(self):
        with self._lock:
            now = time.time()
            return [k for k, (_, exp) in self._store.items() if exp > now]


# Global cache instance
cache = TTLCache(default_ttl=60)
