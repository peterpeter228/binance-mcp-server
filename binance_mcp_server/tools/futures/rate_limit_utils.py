"""
Rate Limiting Utilities for MCP Tools.

Provides:
- Exponential backoff with jitter for rate limit handling
- Parameter-based caching with TTL
- Retry decorators for API calls
"""

import time
import random
import hashlib
import json
import logging
import threading
import functools
from typing import Dict, Any, Optional, Callable, TypeVar, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class RetryConfig:
    """Configuration for retry with exponential backoff."""
    max_retries: int = 3
    base_delay_ms: int = 1000
    max_delay_ms: int = 30000
    jitter_factor: float = 0.3  # 0-1, random factor to add to delay
    retry_codes: tuple = (-1003, -1015, 429)  # Rate limit error codes


def calculate_backoff_delay(
    attempt: int,
    base_delay_ms: int = 1000,
    max_delay_ms: int = 30000,
    jitter_factor: float = 0.3
) -> float:
    """
    Calculate exponential backoff delay with jitter.
    
    Args:
        attempt: Current attempt number (0-indexed)
        base_delay_ms: Base delay in milliseconds
        max_delay_ms: Maximum delay in milliseconds
        jitter_factor: Random factor (0-1) to add randomness
        
    Returns:
        Delay in seconds
    """
    # Exponential backoff: base * 2^attempt
    delay_ms = base_delay_ms * (2 ** attempt)
    
    # Cap at max delay
    delay_ms = min(delay_ms, max_delay_ms)
    
    # Add jitter: +/- jitter_factor * delay
    jitter_range = delay_ms * jitter_factor
    jitter = random.uniform(-jitter_range, jitter_range)
    delay_ms = max(0, delay_ms + jitter)
    
    return delay_ms / 1000.0  # Convert to seconds


class ParameterCache:
    """
    Thread-safe cache with TTL based on parameter hashing.
    
    Caches results based on a hash of the input parameters,
    allowing cache hits for identical requests within TTL.
    """
    
    def __init__(self, default_ttl_seconds: float = 30.0):
        self._cache: Dict[str, Any] = {}
        self._timestamps: Dict[str, float] = {}
        self._ttls: Dict[str, float] = {}
        self._lock = threading.RLock()
        self.default_ttl = default_ttl_seconds
    
    @staticmethod
    def _hash_params(params: Dict[str, Any]) -> str:
        """Create a hash key from parameters."""
        # Sort and serialize params for consistent hashing
        serialized = json.dumps(params, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()[:16]
    
    def get(self, key: str) -> Tuple[bool, Optional[Any]]:
        """
        Get cached value if not expired.
        
        Args:
            key: Cache key
            
        Returns:
            Tuple of (hit, value) - hit is True if cache hit, value is cached data
        """
        with self._lock:
            if key not in self._cache:
                return False, None
            
            ttl = self._ttls.get(key, self.default_ttl)
            elapsed = time.time() - self._timestamps.get(key, 0)
            
            if elapsed > ttl:
                # Expired - clean up
                del self._cache[key]
                del self._timestamps[key]
                self._ttls.pop(key, None)
                return False, None
            
            logger.debug(f"Cache hit for key {key}, age={elapsed:.1f}s")
            return True, self._cache[key]
    
    def set(self, key: str, value: Any, ttl: Optional[float] = None):
        """
        Set cached value with optional custom TTL.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Optional custom TTL in seconds
        """
        with self._lock:
            self._cache[key] = value
            self._timestamps[key] = time.time()
            if ttl is not None:
                self._ttls[key] = ttl
            else:
                self._ttls[key] = self.default_ttl
    
    def invalidate(self, key: str):
        """Invalidate a specific cache entry."""
        with self._lock:
            self._cache.pop(key, None)
            self._timestamps.pop(key, None)
            self._ttls.pop(key, None)
    
    def clear(self):
        """Clear all cached entries."""
        with self._lock:
            self._cache.clear()
            self._timestamps.clear()
            self._ttls.clear()
    
    def cleanup_expired(self):
        """Remove all expired entries."""
        with self._lock:
            current_time = time.time()
            expired_keys = [
                key for key, ts in self._timestamps.items()
                if current_time - ts > self._ttls.get(key, self.default_ttl)
            ]
            for key in expired_keys:
                self._cache.pop(key, None)
                self._timestamps.pop(key, None)
                self._ttls.pop(key, None)


# Global caches for each tool (with different TTLs)
_tool_caches: Dict[str, ParameterCache] = {}
_cache_lock = threading.Lock()


def get_tool_cache(tool_name: str, default_ttl: float = 30.0) -> ParameterCache:
    """
    Get or create a cache for a specific tool.
    
    Args:
        tool_name: Name of the tool
        default_ttl: Default TTL in seconds
        
    Returns:
        ParameterCache instance for the tool
    """
    global _tool_caches
    
    with _cache_lock:
        if tool_name not in _tool_caches:
            _tool_caches[tool_name] = ParameterCache(default_ttl)
        return _tool_caches[tool_name]


def with_retry_and_cache(
    tool_name: str,
    cache_ttl_seconds: float = 30.0,
    retry_config: Optional[RetryConfig] = None
):
    """
    Decorator that adds caching and retry logic to API-calling functions.
    
    Args:
        tool_name: Name of the tool (for cache isolation)
        cache_ttl_seconds: Cache TTL in seconds
        retry_config: Optional retry configuration
        
    Returns:
        Decorated function with caching and retry
    """
    if retry_config is None:
        retry_config = RetryConfig()
    
    def decorator(func: Callable[..., Dict[str, Any]]) -> Callable[..., Dict[str, Any]]:
        cache = get_tool_cache(tool_name, cache_ttl_seconds)
        
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Dict[str, Any]:
            # Create cache key from arguments
            cache_params = {
                "args": args,
                "kwargs": kwargs
            }
            cache_key = ParameterCache._hash_params(cache_params)
            
            # Check cache
            hit, cached_result = cache.get(cache_key)
            if hit:
                result = cached_result.copy()
                result["_cache_hit"] = True
                return result
            
            # Execute with retry
            last_error = None
            for attempt in range(retry_config.max_retries + 1):
                try:
                    result = func(*args, **kwargs)
                    
                    # Check if result indicates rate limit error
                    if not result.get("success", False):
                        error = result.get("error", {})
                        error_code = error.get("code") if isinstance(error, dict) else None
                        
                        if error_code in retry_config.retry_codes:
                            if attempt < retry_config.max_retries:
                                delay = calculate_backoff_delay(
                                    attempt,
                                    retry_config.base_delay_ms,
                                    retry_config.max_delay_ms,
                                    retry_config.jitter_factor
                                )
                                logger.warning(
                                    f"Rate limit hit for {tool_name}, "
                                    f"attempt {attempt + 1}/{retry_config.max_retries + 1}, "
                                    f"sleeping {delay:.2f}s"
                                )
                                time.sleep(delay)
                                continue
                    
                    # Success or non-retryable error
                    if result.get("success", False):
                        result["_cache_hit"] = False
                        cache.set(cache_key, result)
                    
                    return result
                    
                except Exception as e:
                    last_error = e
                    if attempt < retry_config.max_retries:
                        delay = calculate_backoff_delay(
                            attempt,
                            retry_config.base_delay_ms,
                            retry_config.max_delay_ms,
                            retry_config.jitter_factor
                        )
                        logger.warning(
                            f"Exception in {tool_name}: {e}, "
                            f"attempt {attempt + 1}/{retry_config.max_retries + 1}, "
                            f"sleeping {delay:.2f}s"
                        )
                        time.sleep(delay)
            
            # All retries exhausted
            if last_error:
                return {
                    "success": False,
                    "error": {
                        "type": "retry_exhausted",
                        "message": f"Failed after {retry_config.max_retries + 1} attempts: {str(last_error)}"
                    }
                }
            
            return result
        
        return wrapper
    return decorator


def make_api_call_with_backoff(
    api_func: Callable[[], Tuple[bool, Any]],
    retry_config: Optional[RetryConfig] = None,
    operation_name: str = "API call"
) -> Tuple[bool, Any, Optional[str]]:
    """
    Execute an API call with exponential backoff on rate limit errors.
    
    Args:
        api_func: Function that makes the API call, returns (success, data)
        retry_config: Optional retry configuration
        operation_name: Name for logging
        
    Returns:
        Tuple of (success, data, error_message)
    """
    if retry_config is None:
        retry_config = RetryConfig()
    
    last_error = None
    
    for attempt in range(retry_config.max_retries + 1):
        try:
            success, data = api_func()
            
            if success:
                return True, data, None
            
            # Check for rate limit error
            error_code = data.get("code") if isinstance(data, dict) else None
            
            if error_code in retry_config.retry_codes:
                if attempt < retry_config.max_retries:
                    delay = calculate_backoff_delay(
                        attempt,
                        retry_config.base_delay_ms,
                        retry_config.max_delay_ms,
                        retry_config.jitter_factor
                    )
                    logger.warning(
                        f"Rate limit on {operation_name}, "
                        f"attempt {attempt + 1}/{retry_config.max_retries + 1}, "
                        f"sleeping {delay:.2f}s"
                    )
                    time.sleep(delay)
                    continue
            
            # Non-retryable error
            error_msg = data.get("message", str(data)) if isinstance(data, dict) else str(data)
            return False, data, error_msg
            
        except Exception as e:
            last_error = str(e)
            if attempt < retry_config.max_retries:
                delay = calculate_backoff_delay(
                    attempt,
                    retry_config.base_delay_ms,
                    retry_config.max_delay_ms,
                    retry_config.jitter_factor
                )
                logger.warning(
                    f"Exception in {operation_name}: {e}, "
                    f"attempt {attempt + 1}/{retry_config.max_retries + 1}, "
                    f"sleeping {delay:.2f}s"
                )
                time.sleep(delay)
    
    return False, None, f"Failed after {retry_config.max_retries + 1} attempts: {last_error}"


class RateLimitTracker:
    """
    Tracks rate limit state across multiple API calls.
    
    Implements a token bucket algorithm for proactive rate limiting.
    """
    
    def __init__(self, max_requests: int = 1200, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: list = []
        self._lock = threading.Lock()
    
    def can_make_request(self) -> bool:
        """Check if we can make a request without hitting rate limit."""
        self._cleanup_old_requests()
        with self._lock:
            return len(self._requests) < self.max_requests
    
    def record_request(self):
        """Record a request timestamp."""
        with self._lock:
            self._requests.append(time.time())
    
    def _cleanup_old_requests(self):
        """Remove requests outside the current window."""
        cutoff = time.time() - self.window_seconds
        with self._lock:
            self._requests = [t for t in self._requests if t > cutoff]
    
    def wait_if_needed(self) -> float:
        """
        Wait if we're at the rate limit.
        
        Returns:
            Time waited in seconds
        """
        self._cleanup_old_requests()
        
        with self._lock:
            if len(self._requests) >= self.max_requests:
                # Calculate time to wait
                oldest = min(self._requests)
                wait_time = oldest + self.window_seconds - time.time()
                
                if wait_time > 0:
                    logger.info(f"Rate limit reached, waiting {wait_time:.2f}s")
                    time.sleep(wait_time)
                    return wait_time
        
        return 0.0


# Global rate limit tracker
_rate_tracker = RateLimitTracker()


def get_rate_tracker() -> RateLimitTracker:
    """Get the global rate limit tracker."""
    return _rate_tracker
