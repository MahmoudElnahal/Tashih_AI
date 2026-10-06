"""Public API models for the prayer / daily-companion module (v1)."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field

from app.services.verdict import VerdictCode


class CalculationMethod(BaseModel):
    id: int = Field(description="Aladhan calculation method id", examples=[5])
    name: str = Field(examples=["Egyptian General Authority of Survey"])


class HijriDate(BaseModel):
    date: str = Field(description="DD-MM-YYYY", examples=["25-04-1448"])
    day: int
    month_number: int
    month_ar: str = Field(examples=["رَبيع الثاني"])
    month_en: str
    year: int
    weekday_ar: str = Field(examples=["الثلاثاء"])


class PrayerTime(BaseModel):
    key: str = Field(description="fajr | sunrise | dhuhr | asr | maghrib | isha")
    name_ar: str = Field(examples=["الفجر"])
    time: datetime = Field(
        description="ISO-8601 with UTC offset", examples=["2026-10-06T05:25:00+03:00"]
    )
    is_prayer: bool = Field(description="False for sunrise, which is not a prayer")


class NextPrayer(BaseModel):
    key: str
    name_ar: str
    time: datetime
    seconds_remaining: int = Field(ge=0)


class PrayerTimesResponse(BaseModel):
    request_id: str
    date: date
    timezone: str = Field(description="IANA timezone of the location", examples=["Africa/Cairo"])
    latitude: float
    longitude: float
    method: CalculationMethod
    hijri: HijriDate
    is_friday: bool
    prayers: list[PrayerTime] = Field(
        description="Fajr, sunrise, dhuhr, asr, maghrib, isha in order"
    )
    next_prayer: NextPrayer | None = Field(
        description="Next of the five prayers from now; null when `date` is not today at the location"
    )
    source: str = Field(examples=["aladhan"])


class QiblaResponse(BaseModel):
    request_id: str
    latitude: float
    longitude: float
    direction_deg: float = Field(
        ge=0, lt=360, description="Clockwise degrees from true north", examples=[136.14]
    )
    compass_ar: str = Field(description="Nearest of 8 compass points", examples=["جنوب شرق"])
    kaaba_latitude: float
    kaaba_longitude: float
    source: str = Field(examples=["aladhan"])


class FocusWindow(BaseModel):
    prayer: str = Field(description="fajr | sunrise | dhuhr | asr | maghrib | isha")
    name_ar: str = Field(description="«الجمعة» replaces «الظهر» on Fridays", examples=["العصر"])
    is_jumuah: bool
    adhan: datetime = Field(description="The prayer time itself")
    start: datetime = Field(description="adhan - before_min")
    end: datetime = Field(description="adhan + during_min")
    duration_min: int
    reminder_ar: str = Field(description="Short text for the lock screen")


class FocusScheduleResponse(BaseModel):
    request_id: str
    date: date
    timezone: str
    latitude: float
    longitude: float
    method: CalculationMethod
    is_friday: bool
    before_min: int
    during_min: int
    jumuah_during_min: int
    windows: list[FocusWindow]
    active_window: FocusWindow | None = Field(
        description="The window containing the current moment, if any"
    )
    blocked_categories: list[str] = Field(
        description="Recommended default app categories to block; the client maps them to apps",
        examples=[["social", "games", "video"]],
    )
    source: str


class DailyHadith(BaseModel):
    hadith_text: str
    source_book: str = Field(examples=["صحيح البخاري"])
    number: str = Field(examples=["1"])
    verdict_code: VerdictCode
    verdict_ar: str
    grade_text: str = Field(examples=["أخرجه في صحيحه"])


class DailyResponse(BaseModel):
    request_id: str
    date: date
    hadith: DailyHadith
    next_prayer: NextPrayer | None = Field(description="Null unless lat and lon are provided")
    disclaimer_ar: str
