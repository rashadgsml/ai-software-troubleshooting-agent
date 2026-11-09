"""API authentication and rate limiting.

Django analogy: DRF authentication + throttle classes. Limits are in-process, which is
correct for a single API instance. Several replicas would need a shared store (Redis).
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from functools import lru_cache
from typing import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader

from app.core.config import Settings, get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class RateLimiter:
    """Sliding-window limiter: at most `limit` hits per `window` seconds per key."""

    def __init__(self, limit: int, window: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window, self._clock = limit, window, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> float | None:
        """Record a hit. Returns None if allowed, else seconds until the next slot frees up."""
        now = self._clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return self.window - (now - hits[0])
            hits.append(now)
            return None


class Limiters:
    def __init__(self, settings: Settings):
        self.general = RateLimiter(settings.rate_limit_per_minute)
        self.investigations = RateLimiter(settings.investigation_rate_per_minute)
        self.failed_auth = RateLimiter(settings.failed_auth_per_minute)


@lru_cache
def get_limiters() -> Limiters:
    return Limiters(get_settings())


def _too_many(retry_after: float, detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS, detail, headers={"Retry-After": str(max(1, int(retry_after + 0.999)))}
    )


def _fingerprint(api_key: str) -> str:
    """Identify a caller in limiter state without keeping the raw key around."""
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def require_api_key(
    request: Request,
    supplied: str | None = Depends(api_key_header),
    settings: Settings = Depends(get_settings),
    limiters: Limiters = Depends(get_limiters),
) -> str:
    """401 unless X-API-Key matches. Compared in constant time; repeated failures per IP are throttled."""
    client_ip = request.client.host if request.client else "unknown"
    if supplied is None or not secrets.compare_digest(supplied.encode(), settings.api_key.encode()):
        if (wait := limiters.failed_auth.check(client_ip)) is not None:
            raise _too_many(wait, "too many failed authentication attempts")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing API key",
                            headers={"WWW-Authenticate": "ApiKey"})
    return _fingerprint(supplied)


def rate_limit(caller: str = Depends(require_api_key), limiters: Limiters = Depends(get_limiters)) -> str:
    if (wait := limiters.general.check(caller)) is not None:
        raise _too_many(wait, "rate limit exceeded")
    return caller


def investigation_rate_limit(caller: str = Depends(rate_limit), limiters: Limiters = Depends(get_limiters)) -> str:
    if (wait := limiters.investigations.check(caller)) is not None:
        raise _too_many(wait, "investigation rate limit exceeded")
    return caller


def validate_settings_for_startup(settings: Settings) -> None:
    """Fail fast instead of running a production API with a guessable key."""
    weak = settings.api_key in ("", "change-me") or len(settings.api_key) < 16
    if settings.app_env == "production" and weak:
        raise RuntimeError("APP_ENV=production requires API_KEY to be set to a random string of 16+ characters")
