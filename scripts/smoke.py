"""Manual smoke test against the real sources (not part of the test suite).

Usage: python -m scripts.smoke [dorar|offline|dorar,offline]
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import httpx

from app.config import Settings
from app.main import build_sources
from app.models import VerifyResponse
from app.services.cache import TTLCache
from app.services.verify import VerifyService
from app.sources.offline import OfflineSixBooksSource

SAMPLES = [
    "إنما الأعمال بالنيات",
    "طلب العلم فريضة على كل مسلم",
    "اطلبوا العلم ولو في الصين",
    "قال الفيل للنملة سافرت بالطائرة الى المريخ",
    "الصلاة",
]


async def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    names = sys.argv[1] if len(sys.argv) > 1 else "dorar,offline"
    settings = Settings(sources=names, offline_data_dir=Path("data/hadith"))
    async with httpx.AsyncClient() as client:
        sources = build_sources(settings, client)
        for source in sources:
            if isinstance(source, OfflineSixBooksSource):
                started = time.perf_counter()
                await source.wait_ready()
                print(
                    f"offline index: {source.size} hadith in {time.perf_counter() - started:.1f}s"
                )
        service = VerifyService(
            sources, None, TTLCache[VerifyResponse](max_entries=10, ttl_seconds=0)
        )
        for text in SAMPLES:
            started = time.perf_counter()
            r = await service.verify(text, max_results=3, request_id="smoke")
            took = (time.perf_counter() - started) * 1000
            print(f"\n== {text}  [{took:.0f} ms]")
            print(
                f"   status={r.status.value} verdict={r.verdict_code} ({r.verdict_ar}) "
                f"source={r.source_used} differ={r.scholars_differ}"
            )
            if r.best_match:
                b = r.best_match
                print(
                    f"   {b.source_book} | {b.number_or_page} | {b.muhaddith} | {b.grade_text} | score={b.match_score}"
                )
                print("   grades:", [(g.scholar, g.grade, g.verdict_code.value) for g in b.grades])
            print("   msg:", r.message_ar)


if __name__ == "__main__":
    asyncio.run(main())
