"""Offline fallback: the six books from ``fawazahmed0/hadith-api`` (jsDelivr).

Python port of ``docs/reference/tashih-server.js``. Files are downloaded once
into ``data_dir`` (a Docker volume) and indexed in memory, one book at a time
to keep peak RAM low.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.services.matcher import Query, build_query, match_score
from app.services.normalize import normalize
from app.services.verdict import UNCLEAR, Verdict, classify
from app.sources.base import Match, SourceGrade, SourceUnavailable

logger = logging.getLogger(__name__)

SOURCE_NAME = "offline_six_books"
MAX_TEXT_CHARS = 1000
MAX_CANDIDATES = 15
MIN_NORMALIZED_CHARS = 10
_BIDI_MARKS = re.compile(r"[‎‏‪-‮]")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class Book:
    id: str
    name_ar: str
    compiler_ar: str
    sahih_book: bool = False


# Order matters: it is the tie-break preference (Bukhari, then Muslim, ...).
BOOKS: tuple[Book, ...] = (
    Book("ara-bukhari", "صحيح البخاري", "البخاري", sahih_book=True),
    Book("ara-muslim", "صحيح مسلم", "مسلم", sahih_book=True),
    Book("ara-abudawud", "سنن أبي داود", "أبو داود"),
    Book("ara-tirmidhi", "جامع الترمذي", "الترمذي"),
    Book("ara-nasai", "سنن النسائي", "النسائي"),
    Book("ara-ibnmajah", "سنن ابن ماجه", "ابن ماجه"),
)

SAHIH_BOOK_NOTE = "أخرجه في صحيحه"
NO_GRADE_NOTE = "لا يوجد حكم في قاعدة البيانات"

SCHOLARS_AR = {
    "Al-Albani": "الألباني",
    "Zubair Ali Zai": "زبير علي زئي",
    "Shuaib Al Arnaut": "شعيب الأرناؤوط",
    "Abu Ghuddah": "عبد الفتاح أبو غدة",
    "Muhammad Muhyi Al-Din Abdul Hamid": "محمد محيي الدين عبد الحميد",
    "Muhammad Fouad Abd al-Baqi": "محمد فؤاد عبد الباقي",
    "Ahmad Muhammad Shakir": "أحمد محمد شاكر",
    "Bashar Awad Maarouf": "بشار عواد معروف",
}
PRIMARY_SCHOLAR = "Al-Albani"


@dataclass(frozen=True, slots=True)
class _Entry:
    book_index: int
    number: str
    text: str
    norm: str
    grades: tuple[tuple[str, str], ...]


def grade_entry(
    book: Book, raw_grades: tuple[tuple[str, str], ...]
) -> tuple[str | None, str, tuple[SourceGrade, ...], Verdict]:
    """Return (muhaddith, grade_text, all grades, primary verdict).

    Primary rule: Al-Albani's grade when present, otherwise the first grade.
    Bukhari and Muslim carry no grades in the dataset and are ``sahih`` by the
    books' own condition.
    """
    if not raw_grades:
        if book.sahih_book:
            verdict = classify(SAHIH_BOOK_NOTE)
            grade = SourceGrade(book.compiler_ar, SAHIH_BOOK_NOTE, verdict)
            return book.compiler_ar, SAHIH_BOOK_NOTE, (grade,), verdict
        return None, NO_GRADE_NOTE, (), UNCLEAR

    grades = tuple(
        SourceGrade(SCHOLARS_AR.get(name, name), grade, classify(grade))
        for name, grade in raw_grades
    )
    index = next((i for i, (name, _) in enumerate(raw_grades) if name == PRIMARY_SCHOLAR), 0)
    primary = grades[index]
    return primary.scholar, primary.grade, grades, primary.verdict


class OfflineSixBooksSource:
    name = SOURCE_NAME
    grades_span_results = False

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        data_dir: Path,
        base_url: str = "https://cdn.jsdelivr.net/gh/fawazahmed0/hadith-api@1/editions/",
        download_timeout: float = 120.0,
    ) -> None:
        self._client = client
        self._data_dir = data_dir
        self._base_url = base_url.rstrip("/") + "/"
        self._download_timeout = download_timeout
        self._index: list[_Entry] = []
        self._status = "loading"
        self._task: asyncio.Task[None] | None = None

    def health(self) -> str:
        return self._status

    @property
    def size(self) -> int:
        return len(self._index)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._load_safely(), name="offline-index-load")

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def wait_ready(self) -> None:
        """Await the initial load (used by tests and scripts)."""
        await self.start()
        assert self._task is not None
        await self._task

    async def _load_safely(self) -> None:
        try:
            await self.load()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._status = "error"
            logger.exception("offline index failed to load")

    async def load(self) -> None:
        self._status = "loading"
        self._data_dir.mkdir(parents=True, exist_ok=True)
        index: list[_Entry] = []
        for book_index, book in enumerate(BOOKS):
            path = self._data_dir / f"{book.id}.min.json"
            if not path.exists():
                await self._download(book, path)
            entries = await asyncio.to_thread(_read_book, path, book_index)
            index.extend(entries)
            logger.info("offline book loaded", extra={"book": book.id, "hadiths": len(entries)})
        self._index = index
        self._status = "ready"
        logger.info("offline index ready", extra={"hadiths": len(index)})

    async def _download(self, book: Book, path: Path) -> None:
        url = f"{self._base_url}{book.id}.min.json"
        partial = path.with_suffix(".part")
        logger.info("downloading offline book", extra={"book": book.id})
        async with self._client.stream(
            "GET", url, timeout=self._download_timeout, follow_redirects=True
        ) as response:
            response.raise_for_status()
            with partial.open("wb") as handle:
                async for chunk in response.aiter_bytes():
                    handle.write(chunk)
        os.replace(partial, path)

    async def search(self, text: str) -> list[Match]:
        if self._status != "ready":
            raise SourceUnavailable(self.name, f"index is {self._status}")
        query = build_query(text)
        if not query.is_searchable:
            return []
        return await asyncio.to_thread(self._search_sync, query)

    def _search_sync(self, query: Query, sahih_books_only: bool = False) -> list[Match]:
        scored: list[tuple[float, _Entry]] = []
        for entry in self._index:
            if sahih_books_only and not BOOKS[entry.book_index].sahih_book:
                continue
            score = match_score(query, entry.norm)
            if score > 0:
                scored.append((score, entry))
        # Best score, then Bukhari/Muslim first, then the shorter text.
        scored.sort(key=lambda item: (-item[0], item[1].book_index, len(item[1].norm)))
        return [self._to_match(score, entry) for score, entry in scored[:MAX_CANDIDATES]]

    async def search_sahihayn(self, text: str) -> list[Match]:
        """Matches in Sahih al-Bukhari and Sahih Muslim only, best first."""
        if self._status != "ready":
            raise SourceUnavailable(self.name, f"index is {self._status}")
        query = build_query(text)
        if not query.is_searchable:
            return []
        return await asyncio.to_thread(self._search_sync, query, True)

    async def find_in_sahihayn(self, phrase: str) -> Match | None:
        """Shortest exact match of ``phrase`` in al-Bukhari, else Muslim."""
        if self._status != "ready":
            raise SourceUnavailable(self.name, f"index is {self._status}")
        matches = await asyncio.to_thread(self._search_sync, build_query(phrase), True)
        return next((m for m in matches if m.score == 1.0), None)

    @staticmethod
    def _to_match(score: float, entry: _Entry) -> Match:
        book = BOOKS[entry.book_index]
        muhaddith, grade_text, grades, verdict = grade_entry(book, entry.grades)
        text = _SPACES.sub(" ", _BIDI_MARKS.sub("", entry.text)).strip()
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS].rstrip() + "…"
        return Match(
            hadith_text=text,
            narrator=None,
            muhaddith=muhaddith,
            source_book=book.name_ar,
            number_or_page=entry.number,
            grade_text=grade_text,
            grades=grades,
            score=score,
            verdict=verdict,
        )


def _read_book(path: Path, book_index: int) -> list[_Entry]:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    entries: list[_Entry] = []
    for hadith in data.get("hadiths", []):
        text = hadith.get("text")
        if not text:
            continue
        norm = normalize(text)
        if len(norm) < MIN_NORMALIZED_CHARS:
            continue
        grades = tuple(
            (str(g.get("name", "")), str(g.get("grade", "")))
            for g in hadith.get("grades") or []
            if g.get("grade")
        )
        entries.append(_Entry(book_index, str(hadith.get("hadithnumber", "")), text, norm, grades))
    return entries
