"""Small in-memory TTL cache with LRU eviction (single process)."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Hashable


class TTLCache[T]:
    def __init__(
        self, *, max_entries: int, ttl_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._max = max_entries
        self._ttl = ttl_seconds
        self._clock = clock
        self._items: OrderedDict[Hashable, tuple[float, T]] = OrderedDict()

    def get(self, key: Hashable) -> T | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < self._clock():
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return value

    def set(self, key: Hashable, value: T) -> None:
        if self._ttl <= 0:
            return
        self._items[key] = (self._clock() + self._ttl, value)
        self._items.move_to_end(key)
        while len(self._items) > self._max:
            self._items.popitem(last=False)
