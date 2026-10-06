"""Hadith verification: query the sources in order and build the response.

Primary-verdict rule (documented in docs/API.md):

1. Sources are tried in ``SOURCES`` order; the first one that returns a
   confident match answers. A source that fails is skipped (fallback).
2. Among the matches tied at the best score, the primary result is the first
   one whose grade text classifies to a definite verdict; if none does, the
   first match is used and the verdict is ``unclear``.
3. For Dorar (one grading per result) the gradings of all top-scored results
   are listed in ``best_match.grades``. For the offline dataset the primary
   grade is Al-Albani's when present.
4. If the listed scholars disagree, ``scholars_differ`` is true and the
   message says so. One scholar's grading is never presented as consensus.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from app.models import (
    DISCLAIMER_AR,
    Grade,
    HadithMatch,
    Status,
    VerifyResponse,
)
from app.services.cache import TTLCache
from app.services.intent import clean_query
from app.services.llm import LLM, ExplainFacts, validate_explanation
from app.services.matcher import build_query
from app.services.verdict import REJECTED, VerdictCode, verdicts_conflict
from app.sources.base import HadithSource, Match, SourceError, SourceGrade

logger = logging.getLogger(__name__)

MSG_NOT_FOUND = (
    "لم أجد هذا النص في المصادر المتاحة بدرجة ثقة كافية، لذلك لا أستطيع الحكم عليه. "
    "عدم العثور عليه لا يعني أنه صحيح ولا أنه ضعيف. يُرجى مراجعة أهل العلم المختصين بالحديث."
)
MSG_NOT_FOUND_OFFLINE = (
    " (تنبيه: تم البحث في الكتب الستة فقط لأن الموسوعة الحديثية غير متاحة حالياً.)"
)
MSG_TOO_SHORT = (
    "النص قصير جداً أو عام جداً للتحقق منه. من فضلك اكتب جزءاً أوضح من نص الحديث "
    "(كلمتان مميزتان على الأقل)."
)
MSG_UNAVAILABLE = (
    "تعذر الوصول إلى مصادر الأحاديث حالياً، لذلك لا أستطيع الحكم على هذا النص. "
    "حاول مرة أخرى بعد قليل."
)
MSG_NOT_ATTRIBUTE = " لا ينبغي نسبته إلى النبي ﷺ."
MSG_UNCLEAR = " عبارة الحكم تحتاج إلى مراجعة أهل العلم ولا يُستخرج منها حكم آلي."
MSG_DIFFER = " تنبيه: أحكام المحدثين على هذا الحديث مختلفة؛ راجع قائمة الأحكام وأهل العلم."


@dataclass(frozen=True, slots=True)
class _Found:
    source: str
    best: Match
    grades: tuple[SourceGrade, ...]
    others: tuple[Match, ...]


def _dedupe(grades: Sequence[SourceGrade]) -> tuple[SourceGrade, ...]:
    seen: dict[tuple[str, str], SourceGrade] = {}
    for grade in grades:
        seen.setdefault((grade.scholar, grade.grade), grade)
    return tuple(seen.values())


def select_primary(source: HadithSource, matches: Sequence[Match]) -> _Found:
    """Apply the primary-verdict rule to a non-empty list of matches."""
    top = [m for m in matches if m.score == matches[0].score]
    best = next((m for m in top if m.verdict.code is not VerdictCode.UNCLEAR), top[0])
    grades = _dedupe([g for m in top for g in m.grades]) if source.grades_span_results else best.grades
    others = tuple(m for m in matches if m is not best)
    return _Found(source=source.name, best=best, grades=grades, others=others)


def _to_model(match: Match, grades: Sequence[SourceGrade]) -> HadithMatch:
    return HadithMatch(
        hadith_text=match.hadith_text,
        narrator=match.narrator,
        muhaddith=match.muhaddith,
        source_book=match.source_book,
        number_or_page=match.number_or_page,
        grade_text=match.grade_text,
        grades=[Grade(scholar=g.scholar, grade=g.grade, verdict_code=g.verdict.code) for g in grades],
        match_score=match.score,
    )


def summary_message(found: _Found, differ: bool) -> str:
    best = found.best
    who = best.muhaddith or "المصدر"
    where = " — ".join(part for part in (best.source_book, best.number_or_page) if part)
    message = f"حكم عليه {who} بقوله: «{best.grade_text or 'غير مذكور'}»"
    if where:
        message += f" ({where})"
    message += "."
    if best.verdict.code in REJECTED:
        message += MSG_NOT_ATTRIBUTE
    elif best.verdict.code is VerdictCode.UNCLEAR:
        message += MSG_UNCLEAR
    if differ:
        message += MSG_DIFFER
    return message


class VerifyService:
    def __init__(
        self,
        sources: Sequence[HadithSource],
        llm: LLM | None,
        cache: TTLCache[VerifyResponse],
    ) -> None:
        self._sources = list(sources)
        self._llm = llm
        self._cache = cache

    @property
    def sources(self) -> list[HadithSource]:
        return self._sources

    async def verify(
        self, text: str, *, max_results: int, request_id: str, explain: bool = True
    ) -> VerifyResponse:
        query_text = clean_query(text) or text.strip()
        query = build_query(query_text)
        if not query.is_searchable:
            return self._empty(request_id, query_text, Status.NOT_FOUND, MSG_TOO_SHORT, None)

        want_explanation = explain and self._llm is not None
        cache_key = (query.normalized, max_results, want_explanation)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached.model_copy(update={"request_id": request_id})

        found: _Found | None = None
        answered: list[str] = []
        failed: list[str] = []
        for source in self._sources:
            try:
                matches = await source.search(query_text)
            except SourceError as exc:
                failed.append(source.name)
                logger.warning(
                    "source failed, falling back",
                    extra={"source": source.name, "detail": exc.detail, "request_id": request_id},
                )
                continue
            answered.append(source.name)
            if matches:
                found = select_primary(source, matches)
                break

        if found is None:
            if not answered:
                return self._empty(request_id, query_text, Status.UNAVAILABLE, MSG_UNAVAILABLE, None)
            message = MSG_NOT_FOUND
            if failed and answered == ["offline_six_books"]:
                message += MSG_NOT_FOUND_OFFLINE
            response = self._empty(request_id, query_text, Status.NOT_FOUND, message, answered[0])
            if not failed:
                self._cache.set(cache_key, response)
            return response

        best = found.best
        differ = verdicts_conflict([g.verdict.code for g in found.grades])
        explanation = await self._explain(best) if want_explanation else None
        response = VerifyResponse(
            request_id=request_id,
            status=Status.FOUND,
            verdict_code=best.verdict.code,
            verdict_ar=best.verdict.label_ar,
            query=query_text,
            best_match=_to_model(best, found.grades),
            other_matches=[_to_model(m, m.grades) for m in found.others[: max_results - 1]],
            explanation_ar=explanation,
            message_ar=summary_message(found, differ),
            disclaimer_ar=DISCLAIMER_AR,
            refer_to_scholars=differ or best.verdict.code is VerdictCode.UNCLEAR,
            scholars_differ=differ,
            source_used=found.source,
        )
        # Do not pin a degraded answer: skip the cache if a preferred source
        # failed or the explanation could not be produced this time.
        if not failed and (explanation is not None or not want_explanation):
            self._cache.set(cache_key, response)
        return response

    async def _explain(self, best: Match) -> str | None:
        assert self._llm is not None
        facts = ExplainFacts(
            hadith_text=best.hadith_text,
            narrator=best.narrator,
            muhaddith=best.muhaddith,
            source_book=best.source_book,
            number_or_page=best.number_or_page,
            grade_text=best.grade_text,
            verdict_code=best.verdict.code,
            verdict_ar=best.verdict.label_ar,
        )
        try:
            raw = await self._llm.explain(facts)
        except Exception as exc:
            logger.warning("explanation failed", extra={"error": type(exc).__name__})
            return None
        explanation = validate_explanation(raw, facts)
        if raw and explanation is None:
            logger.warning("explanation rejected by validator")
        return explanation

    @staticmethod
    def _empty(
        request_id: str, query: str, status: Status, message: str, source_used: str | None
    ) -> VerifyResponse:
        return VerifyResponse(
            request_id=request_id,
            status=status,
            verdict_code=None,
            verdict_ar=None,
            query=query,
            best_match=None,
            other_matches=[],
            explanation_ar=None,
            message_ar=message,
            disclaimer_ar=DISCLAIMER_AR,
            refer_to_scholars=status is not Status.OUT_OF_SCOPE,
            scholars_differ=False,
            source_used=source_used,
        )
