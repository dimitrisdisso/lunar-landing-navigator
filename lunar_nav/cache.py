"""
Simple in-memory caching for ephemeris and terrain data to speed up repeated queries.
"""
import hashlib
from datetime import datetime
from typing import Optional, Any

_cache = {}
_MAX_CACHE_SIZE = 50  # Keep last 50 items


def _cache_key(*args) -> str:
    """Generate cache key from arguments."""
    key_str = str(args)
    return hashlib.md5(key_str.encode()).hexdigest()


def get(category: str, *args) -> Optional[Any]:
    """Retrieve from cache."""
    key = f"{category}:{_cache_key(*args)}"
    if key in _cache:
        return _cache[key]
    return None


def put(category: str, value: Any, *args):
    """Store in cache with automatic size management."""
    key = f"{category}:{_cache_key(*args)}"
    
    # Simple LRU: if cache is full, remove oldest
    if len(_cache) >= _MAX_CACHE_SIZE:
        oldest = next(iter(_cache))
        del _cache[oldest]
    
    _cache[key] = value


def clear(category: Optional[str] = None):
    """Clear cache, optionally for specific category."""
    global _cache
    if category is None:
        _cache = {}
    else:
        keys_to_delete = [k for k in _cache.keys() if k.startswith(f"{category}:")]
        for k in keys_to_delete:
            del _cache[k]
