"""The ``HadithSource`` abstraction. New data sources implement this protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.services.verdict import Verdict


class SourceError(Exception):
    """A source could not answer. Carries the source name for logs."""

    def __init__(self, source: str, detail: str) -> None:
        super().__init__(f"{source}: {detail}")
        self.source = source
        self.detail = detail


class SourceBlocked(SourceError):
    """The upstream refused us (e.g. Cloudflare HTTP 403 for datacenter IPs)."""


class SourceUnavailable(SourceError):
    """Timeout, network error, bad payload, or the source is still loading."""


@dataclass(frozen=True, slots=True)
class SourceGrade:
    scholar: str
    grade: str
    verdict: Verdict


@dataclass(frozen=True, slots=True)
class Match:
    """One confident match. ``verdict`` is classified from ``grade_text``."""

    hadith_text: str
    narrator: str | None
    muhaddith: str | None
    source_book: str | None
    number_or_page: str | None
    grade_text: str | None
    grades: tuple[SourceGrade, ...]
    score: float
    verdict: Verdict


@runtime_checkable
class HadithSource(Protocol):
    #: Stable identifier reported as ``source_used`` in API responses.
    name: str
    #: True when each result is one scholar's grading of the matched text
    #: (Dorar), so gradings of equally-scored results are merged into
    #: ``best_match.grades``. False when a result already carries all its grades.
    grades_span_results: bool

    async def search(self, text: str) -> list[Match]:
        """Return confident matches only, best first. Empty list = not found.

        Raises ``SourceBlocked`` / ``SourceUnavailable`` when it cannot answer.
        """
        ...

    def health(self) -> str:
        """Short status string for ``GET /health``."""
        ...

    async def start(self) -> None:
        """Begin any background warm-up. Must not block startup."""
        ...

    async def close(self) -> None: ...
