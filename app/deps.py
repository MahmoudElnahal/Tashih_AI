"""FastAPI dependencies: shared state, API key and per-IP rate limit."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

from app.config import Settings
from app.errors import rate_limited, unauthorized
from app.services.chat import ChatService
from app.services.verify import VerifyService

if TYPE_CHECKING:
    from app.services.daily import DailyService
    from app.services.prayer import PrayerService

WINDOW_SECONDS = 60

_api_key_header = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="Required only when the server sets API_KEYS.",
)


class RateLimiter:
    """Fixed-window counter per client IP (in memory, single process)."""

    def __init__(self, limit: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._limit = limit
        self._clock = clock
        self._hits: dict[str, tuple[int, int]] = {}

    def check(self, key: str) -> int | None:
        """Return ``None`` if allowed, else seconds until the window resets."""
        if self._limit <= 0:
            return None
        now = self._clock()
        window = int(now // WINDOW_SECONDS)
        seen_window, count = self._hits.get(key, (window, 0))
        if seen_window != window:
            count = 0
            if len(self._hits) > 10_000:
                self._hits = {k: v for k, v in self._hits.items() if v[0] == window}
        if count >= self._limit:
            return max(1, int((window + 1) * WINDOW_SECONDS - now))
        self._hits[key] = (window, count + 1)
        return None


@dataclass
class AppState:
    settings: Settings
    verify: VerifyService
    chat: ChatService
    rate_limiter: RateLimiter
    prayer: PrayerService | None = None
    daily: DailyService | None = None


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.tashih
    return state


def require_api_key(
    state: AppState = Depends(get_state), api_key: str | None = Depends(_api_key_header)
) -> None:
    keys = state.settings.api_key_set
    if keys and api_key not in keys:
        raise unauthorized()


def enforce_rate_limit(request: Request, state: AppState = Depends(get_state)) -> None:
    client_ip = request.client.host if request.client else "unknown"
    retry_after = state.rate_limiter.check(client_ip)
    if retry_after is not None:
        raise rate_limited(retry_after)
