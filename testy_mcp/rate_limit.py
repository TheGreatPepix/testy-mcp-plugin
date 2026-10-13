import time
from dataclasses import dataclass
from typing import Protocol


class Cache(Protocol):
    def add(self, key: str, value: int, timeout: int) -> bool: ...

    def incr(self, key: str, delta: int = 1) -> int: ...


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    retry_after: int


def check_rate_limit(
    cache: Cache,
    subject: str,
    *,
    limit: int,
    window_seconds: int,
    now: float | None = None,
) -> RateLimitResult:
    """Apply a cache-backed fixed-window limit suitable for multiple workers."""

    if limit <= 0 or window_seconds <= 0:
        return RateLimitResult(True, 0)
    timestamp = time.time() if now is None else now
    bucket = int(timestamp) // window_seconds
    retry_after = window_seconds - (int(timestamp) % window_seconds)
    key = f"testy_mcp:rate:{subject}:{bucket}"
    if cache.add(key, 1, timeout=window_seconds + 1):
        count = 1
    else:
        try:
            count = cache.incr(key)
        except ValueError:
            cache.add(key, 1, timeout=window_seconds + 1)
            count = 1
    return RateLimitResult(count <= limit, retry_after)
