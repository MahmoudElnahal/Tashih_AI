"""v1 daily-companion routes: prayer times, qibla, focus schedule, daily card."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from app.deps import AppState, enforce_rate_limit, get_state, require_api_key
from app.errors import AppError, request_id_of, service_not_ready
from app.models import ErrorResponse
from app.models_prayer import (
    DailyResponse,
    FocusScheduleResponse,
    PrayerTimesResponse,
    QiblaResponse,
)
from app.routers.hadith import ERROR_RESPONSES
from app.services.daily import DailyService
from app.services.prayer import PRAYER_KEYS, FocusOptions, PrayerService

UPSTREAM_RESPONSES: dict[int | str, dict[str, Any]] = {
    **ERROR_RESPONSES,
    502: {"model": ErrorResponse, "description": "Aladhan returned an error"},
    504: {"model": ErrorResponse, "description": "Aladhan timed out"},
}

router = APIRouter(
    prefix="/v1",
    tags=["prayer"],
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
    responses=UPSTREAM_RESPONSES,
)

Lat = Annotated[float, Query(ge=-90, le=90, description="خط العرض / latitude", examples=[30.0444])]
Lon = Annotated[
    float, Query(ge=-180, le=180, description="خط الطول / longitude", examples=[31.2357])
]
Day = Annotated[
    date | None,
    Query(alias="date", description="YYYY-MM-DD. Default: today at the location."),
]
Method = Annotated[
    int | None,
    Query(
        ge=0,
        le=99,
        description=(
            "Aladhan calculation method id (5 = Egyptian General Authority of Survey, "
            "4 = Umm Al-Qura, 3 = Muslim World League, ...). Default: server setting "
            "PRAYER_DEFAULT_METHOD (5)."
        ),
    ),
]
Minutes = Annotated[int | None, Query(ge=0, le=180)]


def prayer_service(state: AppState = Depends(get_state)) -> PrayerService:
    if state.prayer is None:
        raise service_not_ready("The prayer service")
    return state.prayer


def daily_service(state: AppState = Depends(get_state)) -> DailyService:
    if state.daily is None:
        raise service_not_ready("The daily service")
    return state.daily


@router.get(
    "/prayer/times",
    response_model=PrayerTimesResponse,
    summary="مواقيت الصلاة / Prayer times",
    description=(
        "الصلوات الخمس والشروق لليوم المطلوب، التاريخ الهجري، والصلاة القادمة مع الثواني "
        "المتبقية.\n\n"
        "All datetimes are ISO-8601 with the location's UTC offset. Data comes from Aladhan and "
        "is cached per (date, coordinates rounded to 2 decimals, method)."
    ),
)
async def prayer_times(
    request: Request,
    lat: Lat,
    lon: Lon,
    day: Day = None,
    method: Method = None,
    service: PrayerService = Depends(prayer_service),
) -> PrayerTimesResponse:
    return await service.times(
        lat=lat, lon=lon, day=day, method=method, request_id=request_id_of(request)
    )


@router.get(
    "/prayer/qibla",
    response_model=QiblaResponse,
    summary="اتجاه القبلة / Qibla direction",
    description="Clockwise degrees from true north (not magnetic north) toward the Kaaba.",
)
async def qibla(
    request: Request, lat: Lat, lon: Lon, service: PrayerService = Depends(prayer_service)
) -> QiblaResponse:
    return await service.qibla(lat=lat, lon=lon, request_id=request_id_of(request))


@router.get(
    "/prayer/focus-schedule",
    response_model=FocusScheduleResponse,
    summary="نوافذ التركيز وقت الصلاة / Prayer focus windows",
    description=(
        "نافذة لكل صلاة تبدأ قبل الأذان بـ `before_min` وتنتهي بعده بـ `during_min`. "
        "تطبيق الجوال يستخدم هذه النوافذ لقفل التطبيقات المشتتة؛ الخادم يحسب الجدول فقط.\n\n"
        "One window per prayer: `[adhan - before_min, adhan + during_min)`. On Fridays the "
        "dhuhr window is Jumuah and lasts `jumuah_during_min`. `<prayer>_during_min` overrides "
        "one prayer. Sunrise is skipped unless `include_sunrise=true`. The backend never blocks "
        "anything itself: the client enforces the windows on the device."
    ),
)
async def focus_schedule(
    request: Request,
    lat: Lat,
    lon: Lon,
    day: Day = None,
    method: Method = None,
    before_min: Annotated[int, Query(ge=0, le=60)] = 5,
    during_min: Annotated[int, Query(ge=1, le=180)] = 20,
    jumuah_during_min: Annotated[int, Query(ge=1, le=180)] = 60,
    include_sunrise: bool = False,
    fajr_during_min: Minutes = None,
    dhuhr_during_min: Minutes = None,
    asr_during_min: Minutes = None,
    maghrib_during_min: Minutes = None,
    isha_during_min: Minutes = None,
    service: PrayerService = Depends(prayer_service),
) -> FocusScheduleResponse:
    given = (fajr_during_min, dhuhr_during_min, asr_during_min, maghrib_during_min, isha_during_min)
    overrides = {
        key: value for key, value in zip(PRAYER_KEYS, given, strict=True) if value is not None
    }
    return await service.focus_schedule(
        lat=lat,
        lon=lon,
        day=day,
        method=method,
        options=FocusOptions(
            before_min=before_min,
            during_min=during_min,
            jumuah_during_min=jumuah_during_min,
            include_sunrise=include_sunrise,
            during_overrides=overrides,
        ),
        request_id=request_id_of(request),
    )


@router.get(
    "/daily",
    response_model=DailyResponse,
    summary="بطاقة اليوم / Daily card",
    description=(
        "حديث اليوم من صحيح البخاري أو صحيح مسلم (النص والرقم من قاعدة البيانات، بلا أي محتوى "
        "مولَّد)، مع الصلاة القادمة إذا أُرسل الموقع.\n\n"
        "The hadith is chosen deterministically by date from a curated list and read verbatim "
        "from the offline dataset. Returns 503 `service_not_ready` while the dataset is loading. "
        "If Aladhan is down the card is still returned with `next_prayer: null`."
    ),
    responses={503: {"model": ErrorResponse, "description": "Offline dataset not ready"}},
)
async def daily(
    request: Request,
    lat: Annotated[float | None, Query(ge=-90, le=90)] = None,
    lon: Annotated[float | None, Query(ge=-180, le=180)] = None,
    day: Day = None,
    method: Method = None,
    state: AppState = Depends(get_state),
    service: DailyService = Depends(daily_service),
) -> DailyResponse:
    next_prayer = None
    if lat is not None and lon is not None and state.prayer is not None:
        try:
            next_prayer = await state.prayer.next_prayer(lat=lat, lon=lon, method=method)
        except AppError:
            next_prayer = None
    today = day or (await _local_today(state, lat, lon, method))
    return await service.card(today, next_prayer, request_id_of(request))


async def _local_today(
    state: AppState, lat: float | None, lon: float | None, method: int | None
) -> date:
    """Today's date at the location when it is known, else the UTC date."""
    if lat is not None and lon is not None and state.prayer is not None:
        try:
            return await state.prayer.local_today(lat=lat, lon=lon, method=method)
        except AppError:
            pass
    return datetime.now(UTC).date()
