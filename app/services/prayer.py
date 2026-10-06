"""Prayer times, qibla and focus-window schedule (data from Aladhan).

The backend only computes schedules. Locking apps is done on the device by the
Flutter client; see docs/API.md.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.errors import AppError, upstream_unavailable
from app.models_prayer import (
    CalculationMethod,
    FocusScheduleResponse,
    FocusWindow,
    HijriDate,
    NextPrayer,
    PrayerTime,
    PrayerTimesResponse,
    QiblaResponse,
)
from app.services.cache import TTLCache
from app.sources.aladhan import AladhanClient, AladhanError, DayTimings

SOURCE = "aladhan"
KAABA = (21.4225, 39.8262)
COORD_DECIMALS = 2  # ~1.1 km: prayer times differ by a few seconds at most

NAMES_AR = {
    "fajr": "الفجر",
    "sunrise": "الشروق",
    "dhuhr": "الظهر",
    "asr": "العصر",
    "maghrib": "المغرب",
    "isha": "العشاء",
}
JUMUAH_AR = "الجمعة"
PRAYER_KEYS = ("fajr", "dhuhr", "asr", "maghrib", "isha")
BLOCKED_CATEGORIES = ["social", "games", "video"]
_COMPASS_AR = ["شمال", "شمال شرق", "شرق", "جنوب شرق", "جنوب", "جنوب غرب", "غرب", "شمال غرب"]
FRIDAY = 4


@dataclass(frozen=True, slots=True)
class FocusOptions:
    before_min: int = 5
    during_min: int = 20
    jumuah_during_min: int = 60
    include_sunrise: bool = False
    #: per-prayer overrides of ``during_min`` (key -> minutes)
    during_overrides: dict[str, int] | None = None


def compass_ar(direction_deg: float) -> str:
    return _COMPASS_AR[round(direction_deg % 360 / 45) % 8]


def _upstream(exc: AladhanError) -> AppError:
    return upstream_unavailable(SOURCE, timeout=exc.timeout)


class PrayerService:
    def __init__(
        self,
        aladhan: AladhanClient,
        *,
        default_method: int = 5,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        cache_entries: int = 2000,
    ) -> None:
        self._aladhan = aladhan
        self._default_method = default_method
        self._clock = clock
        self._timings: TTLCache[DayTimings] = TTLCache(
            max_entries=cache_entries, ttl_seconds=24 * 3600
        )
        self._qibla: TTLCache[float] = TTLCache(
            max_entries=cache_entries, ttl_seconds=30 * 24 * 3600
        )

    # ------------------------------------------------------------ fetching --

    async def _day(self, day: date, lat: float, lon: float, method: int) -> DayTimings:
        lat, lon = round(lat, COORD_DECIMALS), round(lon, COORD_DECIMALS)
        key = (day, lat, lon, method)
        cached = self._timings.get(key)
        if cached is not None:
            return cached
        try:
            timings = await self._aladhan.timings(day, lat, lon, method)
        except AladhanError as exc:
            raise _upstream(exc) from exc
        self._timings.set(key, timings)
        return timings

    async def _resolve(
        self, lat: float, lon: float, day: date | None, method: int | None
    ) -> tuple[DayTimings, datetime, bool]:
        """Return (timings for the requested day, now at the location, is it today)."""
        method = self._default_method if method is None else method
        now_utc = self._clock()
        timings = await self._day(day or now_utc.date(), lat, lon, method)
        now_local = now_utc.astimezone(ZoneInfo(timings.timezone))
        if day is None and timings.day != now_local.date():
            # The location's calendar day differs from the UTC day.
            timings = await self._day(now_local.date(), lat, lon, method)
        return timings, now_local, timings.day == now_local.date()

    async def _next_prayer(
        self, today: DayTimings, now: datetime, lat: float, lon: float
    ) -> NextPrayer:
        for key in PRAYER_KEYS:
            if today.times[key] > now:
                return self._to_next(key, today, now)
        tomorrow = await self._day(today.day + timedelta(days=1), lat, lon, today.method_id)
        return self._to_next("fajr", tomorrow, now)

    @staticmethod
    def _to_next(key: str, day: DayTimings, now: datetime) -> NextPrayer:
        moment = day.times[key]
        name = JUMUAH_AR if key == "dhuhr" and day.day.weekday() == FRIDAY else NAMES_AR[key]
        return NextPrayer(
            key=key,
            name_ar=name,
            time=moment,
            seconds_remaining=max(0, int((moment - now).total_seconds())),
        )

    # ----------------------------------------------------------- endpoints --

    async def times(
        self, *, lat: float, lon: float, day: date | None, method: int | None, request_id: str
    ) -> PrayerTimesResponse:
        timings, now, is_today = await self._resolve(lat, lon, day, method)
        hijri = timings.hijri
        return PrayerTimesResponse(
            request_id=request_id,
            date=timings.day,
            timezone=timings.timezone,
            latitude=lat,
            longitude=lon,
            method=CalculationMethod(id=timings.method_id, name=timings.method_name),
            hijri=HijriDate(
                date=hijri.date,
                day=hijri.day,
                month_number=hijri.month_number,
                month_ar=hijri.month_ar,
                month_en=hijri.month_en,
                year=hijri.year,
                weekday_ar=hijri.weekday_ar,
            ),
            is_friday=timings.day.weekday() == FRIDAY,
            prayers=[
                PrayerTime(key=key, name_ar=NAMES_AR[key], time=moment, is_prayer=key != "sunrise")
                for key, moment in timings.times.items()
            ],
            next_prayer=await self._next_prayer(timings, now, lat, lon) if is_today else None,
            source=SOURCE,
        )

    async def next_prayer(self, *, lat: float, lon: float, method: int | None) -> NextPrayer:
        timings, now, _ = await self._resolve(lat, lon, None, method)
        return await self._next_prayer(timings, now, lat, lon)

    async def local_today(self, *, lat: float, lon: float, method: int | None) -> date:
        timings, _, _ = await self._resolve(lat, lon, None, method)
        return timings.day

    async def qibla(self, *, lat: float, lon: float, request_id: str) -> QiblaResponse:
        key = (round(lat, 3), round(lon, 3))
        direction = self._qibla.get(key)
        if direction is None:
            try:
                direction = await self._aladhan.qibla(*key)
            except AladhanError as exc:
                raise _upstream(exc) from exc
            self._qibla.set(key, direction)
        direction = round(direction % 360, 2)
        return QiblaResponse(
            request_id=request_id,
            latitude=lat,
            longitude=lon,
            direction_deg=direction,
            compass_ar=compass_ar(direction),
            kaaba_latitude=KAABA[0],
            kaaba_longitude=KAABA[1],
            source=SOURCE,
        )

    async def focus_schedule(
        self,
        *,
        lat: float,
        lon: float,
        day: date | None,
        method: int | None,
        options: FocusOptions,
        request_id: str,
    ) -> FocusScheduleResponse:
        timings, now, _ = await self._resolve(lat, lon, day, method)
        windows = build_focus_windows(timings, options)
        return FocusScheduleResponse(
            request_id=request_id,
            date=timings.day,
            timezone=timings.timezone,
            latitude=lat,
            longitude=lon,
            method=CalculationMethod(id=timings.method_id, name=timings.method_name),
            is_friday=timings.day.weekday() == FRIDAY,
            before_min=options.before_min,
            during_min=options.during_min,
            jumuah_during_min=options.jumuah_during_min,
            windows=windows,
            active_window=next((w for w in windows if w.start <= now < w.end), None),
            blocked_categories=list(BLOCKED_CATEGORIES),
            source=SOURCE,
        )


def build_focus_windows(timings: DayTimings, options: FocusOptions) -> list[FocusWindow]:
    """One window per prayer: ``[adhan - before_min, adhan + during_min)``.

    On Fridays the dhuhr window is the Jumuah window and uses
    ``jumuah_during_min`` unless a dhuhr override is given. Sunrise is not a
    prayer and is skipped unless ``include_sunrise`` is set.
    """
    overrides = options.during_overrides or {}
    is_friday = timings.day.weekday() == FRIDAY
    windows: list[FocusWindow] = []
    for key, adhan in timings.times.items():
        if key == "sunrise" and not options.include_sunrise:
            continue
        is_jumuah = key == "dhuhr" and is_friday
        during = overrides.get(key, options.jumuah_during_min if is_jumuah else options.during_min)
        name = JUMUAH_AR if is_jumuah else NAMES_AR[key]
        duration = options.before_min + during
        if key == "sunrise":
            reminder = f"وقت الشروق. يُفتح الهاتف بعد {duration} دقيقة."
        else:
            reminder = (
                f"حان وقت صلاة {name}. اترك هاتفك الآن، وتُفتح التطبيقات بعد {duration} دقيقة."
            )
        windows.append(
            FocusWindow(
                prayer=key,
                name_ar=name,
                is_jumuah=is_jumuah,
                adhan=adhan,
                start=adhan - timedelta(minutes=options.before_min),
                end=adhan + timedelta(minutes=during),
                duration_min=duration,
                reminder_ar=reminder,
            )
        )
    return windows
