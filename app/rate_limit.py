"""Simple in-memory rate limiter by IP address."""
import os
import time
from collections import defaultdict

from fastapi import HTTPException, Request


class RateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, request: Request) -> None:
        if os.environ.get("APP_ENV") == "test":
            return
        key = request.client.host if request.client else "unknown"
        now = time.time()
        cutoff = now - self.window

        # Prune old entries
        hits = self._hits[key] = [t for t in self._hits[key] if t > cutoff]

        if len(hits) >= self.max_requests:
            raise HTTPException(
                status_code=429,
                detail=f"Too many requests. Try again in {self.window} seconds.",
            )
        hits.append(now)


# 5 login attempts per minute per IP
login_limiter = RateLimiter(max_requests=5, window_seconds=60)

# 3 registration attempts per 10 minutes per IP
register_limiter = RateLimiter(max_requests=3, window_seconds=600)
