"""Check every daily phrase against the real offline dataset.

Usage: python -m scripts.check_daily   (needs the dataset in OFFLINE_DATA_DIR)
"""

from __future__ import annotations

import asyncio
import sys

import httpx

from app.config import get_settings
from app.services.daily import DAILY_PHRASES
from app.sources.offline import OfflineSixBooksSource


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    async with httpx.AsyncClient() as client:
        source = OfflineSixBooksSource(client, data_dir=get_settings().offline_data_dir)
        await source.wait_ready()
        missing = 0
        for phrase in DAILY_PHRASES:
            match = await source.find_in_sahihayn(phrase)
            if match is None:
                missing += 1
                print(f"MISSING  {phrase}")
            else:
                print(
                    f"ok  {match.source_book} #{match.number_or_page} "
                    f"({len(match.hadith_text)} chars)  {phrase}"
                )
        print(f"{len(DAILY_PHRASES) - missing}/{len(DAILY_PHRASES)} phrases resolved")
        return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
