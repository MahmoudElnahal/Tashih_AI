"""Hadith verification: query the sources in order and build the response.

Primary-verdict rule (documented in docs/API.md):

1. Sources are tried in ``SOURCES`` order; the first one that returns a
   confident match answers. A source that fails is skipped (fallback).
2. Only matches tied at the best score are considered.
3. Dorar returns one scholar's grading per result, often for different chains
   of the same text. All of them are listed in ``best_match.grades``. The
   primary verdict is the one given by most scholars (each scholar counted
   once per verdict; unclear gradings ignored; ties go to Dorar's order), and
   ``best_match`` is the first result carrying it. ``DORAR_PRIMARY_RULE=first``
   switches to "first result with a definite verdict". For the offline dataset
   the primary grade is Al-Albani's when present, else the first grade.
4. If the listed scholars disagree, ``scholars_differ`` is true and the
   message says so. One scholar's grading is never presented as consensus.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

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
from app.services.verdict import (
    ACCEPTED,
    DISPUTED,
    REJECTED,
    Verdict,
    VerdictCode,
    verdicts_conflict,
)
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
MSG_DIFFER = (
    " تنبيه: وردت على بعض طرق هذا الحديث ورواياته أحكام أخرى مختلفة؛ راجع قائمة الأحكام وأهل العلم."
)
MSG_NOT_LITERAL = (
    " تنبيه: ما كتبته لا يطابق نص المصدر حرفاً بحرف؛ تأكد أن النص المعروض هو الحديث الذي تقصده."
)


MSG_DISPUTED = (
    "اختلف المحدثون في هذا الحديث، فلا يُذكر له حكم واحد. قبِله: {accepted}. وردّه: {rejected}. "
    "راجع قائمة الأحكام وأهل العلم."
)
#: Minimum score for a Bukhari/Muslim match to settle the verdict (exact text,
#: ignoring spaces, or every word present; never the typo-tolerant tier).
SAHIHAYN_MIN_SCORE = 0.95
MAX_NAMES_IN_MESSAGE = 4


class SahihaynLookup(Protocol):
    """A source that can say whether a text is in al-Bukhari or Muslim."""

    name: str

    async def search_sahihayn(self, text: str) -> list[Match]: ...

    async def search(self, text: str) -> list[Match]: ...


@dataclass(frozen=True, slots=True)
class _Found:
    source: str
    best: Match
    grades: tuple[SourceGrade, ...]
    others: tuple[Match, ...]
    verdict: Verdict
    disputed: bool = False


def _dedupe(grades: Sequence[SourceGrade]) -> tuple[SourceGrade, ...]:
    seen: dict[tuple[str, str], SourceGrade] = {}
    for grade in grades:
        seen.setdefault((grade.scholar, grade.grade), grade)
    return tuple(seen.values())


def _majority_code(top: Sequence[Match]) -> VerdictCode | None:
    """Most common definite verdict, counting each (scholar, verdict) once.

    Ties go to the verdict that appears first in the source's own order.
    """
    counts: dict[VerdictCode, int] = {}
    seen: set[tuple[str | None, VerdictCode]] = set()
    for match in top:
        code = match.verdict.code
        if code is VerdictCode.UNCLEAR or (match.muhaddith, code) in seen:
            continue
        seen.add((match.muhaddith, code))
        counts[code] = counts.get(code, 0) + 1
    return max(counts, key=lambda code: counts[code]) if counts else None


def _scholars(grades: Sequence[SourceGrade], codes: frozenset[VerdictCode]) -> list[str]:
    names: dict[str, None] = {}
    for grade in grades:
        if grade.verdict.code in codes:
            names.setdefault(grade.scholar)
    return list(names)


def select_primary(
    source: HadithSource, matches: Sequence[Match], rule: str = "majority"
) -> _Found:
    """Apply the primary-verdict rule to a non-empty list of matches."""
    top = [m for m in matches if m.score == matches[0].score]
    spans = source.grades_span_results
    wanted = _majority_code(top) if spans and rule == "majority" else None
    best = next(
        (
            m
            for m in top
            if m.verdict.code is not VerdictCode.UNCLEAR and wanted in (None, m.verdict.code)
        ),
        top[0],
    )
    grades = _dedupe([g for m in top for g in m.grades]) if spans else best.grades
    others = tuple(m for m in matches if m is not best)
    # When some scholars accept the text and others reject it, picking one side
    # for the headline would be a guess: report the disagreement instead.
    disputed = spans and rule == "majority" and verdicts_conflict([g.verdict.code for g in grades])
    return _Found(
        source=source.name,
        best=best,
        grades=grades,
        others=others,
        verdict=DISPUTED if disputed else best.verdict,
        disputed=disputed,
    )


def prefer_sahihayn(found: _Found, sahih: Match, source_name: str) -> _Found:
    """The text is in al-Bukhari or Muslim: that settles it.

    Other scholars' gradings (usually of side chains) stay listed in ``grades``.
    """
    return _Found(
        source=source_name,
        best=sahih,
        grades=_dedupe([*sahih.grades, *found.grades]),
        others=(found.best, *found.others),
        verdict=sahih.verdict,
    )


def add_grades(found: _Found, extra: Match, rule: str = "majority") -> _Found:
    """Add another source's gradings of the same text and re-check for a split."""
    grades = _dedupe([*found.grades, *extra.grades])
    disputed = found.disputed or (
        rule == "majority" and verdicts_conflict([g.verdict.code for g in grades])
    )
    return _Found(
        source=found.source,
        best=found.best,
        grades=grades,
        others=(*found.others, extra),
        verdict=DISPUTED if disputed else found.verdict,
        disputed=disputed,
    )


def _to_model(match: Match, grades: Sequence[SourceGrade]) -> HadithMatch:
    return HadithMatch(
        hadith_text=match.hadith_text,
        narrator=match.narrator,
        muhaddith=match.muhaddith,
        source_book=match.source_book,
        number_or_page=match.number_or_page,
        grade_text=match.grade_text,
        grades=[
            Grade(scholar=g.scholar, grade=g.grade, verdict_code=g.verdict.code) for g in grades
        ],
        match_score=match.score,
    )


def summary_message(found: _Found, differ: bool) -> str:
    best = found.best
    if found.disputed:
        accepted = _scholars(found.grades, ACCEPTED)[:MAX_NAMES_IN_MESSAGE]
        rejected = _scholars(found.grades, REJECTED)[:MAX_NAMES_IN_MESSAGE]
        message = MSG_DISPUTED.format(accepted="، ".join(accepted), rejected="، ".join(rejected))
    else:
        who = best.muhaddith or "المصدر"
        where = " — ".join(part for part in (best.source_book, best.number_or_page) if part)
        message = f"حكم عليه {who} بقوله: «{best.grade_text or 'غير مذكور'}»"
        if where:
            message += f" ({where})"
        message += "."
        if found.verdict.code in REJECTED:
            message += MSG_NOT_ATTRIBUTE
        elif found.verdict.code is VerdictCode.UNCLEAR:
            message += MSG_UNCLEAR
        if differ:
            message += MSG_DIFFER
    if best.score < 1.0:
        message += MSG_NOT_LITERAL
    return message


class VerifyService:
    def __init__(
        self,
        sources: Sequence[HadithSource],
        llm: LLM | None,
        cache: TTLCache[VerifyResponse],
        *,
        primary_rule: str = "majority",
        sahihayn: SahihaynLookup | None = None,
    ) -> None:
        self._primary_rule = primary_rule
        self._sahihayn = sahihayn
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
                found = select_primary(source, matches, self._primary_rule)
                if source.grades_span_results:
                    found = await self._check_sahihayn(found, query_text)
                break

        if found is None:
            if not answered:
                return self._empty(
                    request_id, query_text, Status.UNAVAILABLE, MSG_UNAVAILABLE, None
                )
            message = MSG_NOT_FOUND
            if failed and answered == ["offline_six_books"]:
                message += MSG_NOT_FOUND_OFFLINE
            response = self._empty(request_id, query_text, Status.NOT_FOUND, message, answered[0])
            if not failed:
                self._cache.set(cache_key, response)
            return response

        best = found.best
        differ = verdicts_conflict([g.verdict.code for g in found.grades])
        # An unclear grade is not a ruling, so there is nothing for the LLM to explain.
        explainable = want_explanation and found.verdict.code is not VerdictCode.UNCLEAR
        explanation = await self._explain(best) if explainable else None
        response = VerifyResponse(
            request_id=request_id,
            status=Status.FOUND,
            verdict_code=found.verdict.code,
            verdict_ar=found.verdict.label_ar,
            query=query_text,
            best_match=_to_model(best, found.grades),
            other_matches=[_to_model(m, m.grades) for m in found.others[: max_results - 1]],
            explanation_ar=explanation,
            message_ar=summary_message(found, differ),
            disclaimer_ar=DISCLAIMER_AR,
            refer_to_scholars=differ or found.verdict.code is VerdictCode.UNCLEAR,
            scholars_differ=differ,
            source_used=found.source,
        )
        # Do not pin a degraded answer: skip the cache if a preferred source
        # failed or the explanation could not be produced this time.
        if not failed and (explanation is not None or not explainable):
            self._cache.set(cache_key, response)
        return response

    async def _check_sahihayn(self, found: _Found, query_text: str) -> _Found:
        """Cross-check a Dorar answer against the six books (local, no network).

        * In al-Bukhari or Muslim: that settles the verdict.
        * In one of the other four books: its gradings (Al-Albani, ...) join the
          list, which may reveal that scholars are split.
        """
        if self._sahihayn is None:
            return found
        try:
            in_sahihayn = await self._sahihayn.search_sahihayn(query_text)
            if in_sahihayn and in_sahihayn[0].score >= SAHIHAYN_MIN_SCORE:
                return prefer_sahihayn(found, in_sahihayn[0], self._sahihayn.name)
            in_six_books = await self._sahihayn.search(query_text)
        except SourceError:
            return found
        if in_six_books and in_six_books[0].score >= SAHIHAYN_MIN_SCORE:
            return add_grades(found, in_six_books[0], self._primary_rule)
        return found

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
