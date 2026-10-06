"""Daily card: a hadith of the day taken verbatim from al-Bukhari / Muslim.

Nothing here is generated. Each day maps deterministically to one phrase of a
curated list; the full text, book and number are then read from the offline
dataset, so the ruling is certain ("أخرجه في صحيحه").
"""

from __future__ import annotations

from datetime import date

from app.errors import service_not_ready
from app.models import DISCLAIMER_AR
from app.models_prayer import DailyHadith, DailyResponse, NextPrayer
from app.sources.base import SourceUnavailable
from app.sources.offline import OfflineSixBooksSource

# Well-known short hadith, identified by a distinctive phrase. Every phrase is
# checked against the real dataset by scripts/check_daily.py.
DAILY_PHRASES: tuple[str, ...] = (
    "إنما الأعمال بالنيات",
    "بني الإسلام على خمس",
    "لا يؤمن أحدكم حتى يحب لأخيه ما يحب لنفسه",
    "من كان يؤمن بالله واليوم الآخر فليقل خيرا أو ليصمت",
    "المسلم من سلم المسلمون من لسانه ويده",
    "الدين النصيحة",
    "خيركم من تعلم القرآن وعلمه",
    "الكلمة الطيبة صدقة",
    "يسروا ولا تعسروا",
    "من يرد الله به خيرا يفقهه في الدين",
    "الطهور شطر الإيمان",
    "ما نقصت صدقة من مال",
    "ليس الشديد بالصرعة",
    "المؤمن للمؤمن كالبنيان يشد بعضه بعضا",
    "من صام رمضان إيمانا واحتسابا",
    "الحياء شعبة من الإيمان",
    "من دل على خير فله مثل أجر فاعله",
    "انصر أخاك ظالما أو مظلوما",
    "كل معروف صدقة",
    "إن الله لا ينظر إلى صوركم وأموالكم",
    "سباب المسلم فسوق وقتاله كفر",
    "آية المنافق ثلاث",
    "اتقوا النار ولو بشق تمرة",
    "لا تحاسدوا ولا تناجشوا",
    "إن الله رفيق يحب الرفق",
    "لا يدخل الجنة من كان في قلبه مثقال ذرة من كبر",
    "مثل المؤمنين في توادهم وتراحمهم",
)


def phrase_for(day: date, attempt: int = 0) -> str:
    return DAILY_PHRASES[(day.toordinal() + attempt) % len(DAILY_PHRASES)]


class DailyService:
    def __init__(self, offline: OfflineSixBooksSource | None) -> None:
        self._offline = offline

    async def hadith_of_the_day(self, day: date) -> DailyHadith:
        if self._offline is None:
            raise service_not_ready("The offline hadith source (SOURCES=...,offline)")
        # If a phrase is ever missing from the dataset, move on to the next one.
        for attempt in range(len(DAILY_PHRASES)):
            try:
                match = await self._offline.find_in_sahihayn(phrase_for(day, attempt))
            except SourceUnavailable as exc:
                raise service_not_ready("The offline hadith index") from exc
            if match is not None and match.source_book and match.number_or_page:
                return DailyHadith(
                    hadith_text=match.hadith_text,
                    source_book=match.source_book,
                    number=match.number_or_page,
                    verdict_code=match.verdict.code,
                    verdict_ar=match.verdict.label_ar,
                    grade_text=match.grade_text or "",
                )
        raise service_not_ready("The daily hadith list")

    async def card(
        self, day: date, next_prayer: NextPrayer | None, request_id: str
    ) -> DailyResponse:
        return DailyResponse(
            request_id=request_id,
            date=day,
            hadith=await self.hadith_of_the_day(day),
            next_prayer=next_prayer,
            disclaimer_ar=DISCLAIMER_AR,
        )
