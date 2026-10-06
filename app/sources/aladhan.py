"""Aladhan API client (prayer times and qibla). https://aladhan.com/prayer-times-api

Verified response shapes are saved in ``tests/fixtures/aladhan_*.json``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

# Aladhan key -> our stable lowercase key, in chronological order.
TIMING_KEYS: tuple[tuple[str, str], ...] = (
    ("Fajr", "fajr"),
    ("Sunrise", "sunrise"),
    ("Dhuhr", "dhuhr"),
    ("Asr", "asr"),
    ("Maghrib", "maghrib"),
    ("Isha", "isha"),
)
_HH_MM = re.compile(r"^\s*(\d{1,2}):(\d{2})")


class AladhanError(Exception):
    def __init__(self, detail: str, *, timeout: bool = False) -> None:
        super().__init__(detail)
        self.detail = detail
        self.timeout = timeout


@dataclass(frozen=True, slots=True)
class Hijri:
    date: str
    day: int
    month_number: int
    month_ar: str
    month_en: str
    year: int
    weekday_ar: str


@dataclass(frozen=True, slots=True)
class DayTimings:
    day: date
    timezone: str
    method_id: int
    method_name: str
    hijri: Hijri
    #: key (fajr, sunrise, dhuhr, asr, maghrib, isha) -> timezone-aware datetime
    times: dict[str, datetime]


def parse_timings(payload: Any, day: date) -> DayTimings:
    """Turn an Aladhan ``/timings`` payload into aware datetimes."""
    try:
        data = payload["data"]
        timezone = str(data["meta"]["timezone"])
        zone = ZoneInfo(timezone)
        method = data["meta"]["method"]
        hijri = data["date"]["hijri"]
        times: dict[str, datetime] = {}
        previous: datetime | None = None
        for source_key, key in TIMING_KEYS:
            match = _HH_MM.match(str(data["timings"][source_key]))
            if match is None:
                raise ValueError(f"bad time for {source_key}")
            moment = datetime(
                day.year, day.month, day.day, int(match[1]), int(match[2]), tzinfo=zone
            )
            # At high latitudes Isha can fall after midnight.
            if previous is not None and moment < previous:
                moment += timedelta(days=1)
            times[key] = moment
            previous = moment
        return DayTimings(
            day=day,
            timezone=timezone,
            method_id=int(method["id"]),
            method_name=str(method["name"]),
            hijri=Hijri(
                date=str(hijri["date"]),
                day=int(hijri["day"]),
                month_number=int(hijri["month"]["number"]),
                month_ar=str(hijri["month"]["ar"]),
                month_en=str(hijri["month"]["en"]),
                year=int(hijri["year"]),
                weekday_ar=str(hijri["weekday"]["ar"]),
            ),
            times=times,
        )
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise AladhanError(f"unexpected timings payload: {type(exc).__name__}") from exc


class AladhanClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str = "https://api.aladhan.com/v1",
        timeout: float = 10.0,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def _get(self, path: str, params: dict[str, str | int | float] | None = None) -> Any:
        try:
            response = await self._client.get(
                f"{self._base_url}{path}", params=params, timeout=self._timeout
            )
        except httpx.TimeoutException as exc:
            raise AladhanError("timeout", timeout=True) from exc
        except httpx.HTTPError as exc:
            raise AladhanError(f"network error: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise AladhanError(f"HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise AladhanError("invalid JSON") from exc

    async def timings(
        self, day: date, latitude: float, longitude: float, method: int
    ) -> DayTimings:
        payload = await self._get(
            f"/timings/{day:%d-%m-%Y}",
            {"latitude": latitude, "longitude": longitude, "method": method},
        )
        return parse_timings(payload, day)

    async def qibla(self, latitude: float, longitude: float) -> float:
        payload = await self._get(f"/qibla/{latitude}/{longitude}")
        try:
            return float(payload["data"]["direction"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AladhanError("unexpected qibla payload") from exc
