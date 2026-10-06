"""OpenAI layer: text extraction and grounded explanation only.

Every method returns ``None`` on any failure so callers can always fall back
to the deterministic result. LLM output is never trusted: see
``validate_extraction`` and ``validate_explanation``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Literal, Protocol

from openai import AsyncOpenAI

from app import prompts
from app.services.normalize import normalize
from app.services.verdict import ACCEPTED, REJECTED, VerdictCode, analyze

logger = logging.getLogger(__name__)

MAX_EXPLANATION_CHARS = 450
MAX_EXPLANATION_SENTENCES = 3
MIN_TYPO_COVERAGE = 0.7
MIN_RUN_CHARS = 3
_SENTENCE_END = re.compile(r"[.!؟?]+")
_ARABIC = "\u0621-\u064a"
_FIQH_WORDS = re.compile(
    rf"(?<![{_ARABIC}])(?:يجوز|حرام|حلال|مكروه|مستحب|فتوي|الراجح|يجب عليك|اجمع|اجمعوا|باتفاق)"
    rf"(?![{_ARABIC}])"
)
# The explanation may only restate the ruling: no commentary on the hadith's
# meaning and no advice about acting on it.
_OVERREACH = re.compile(
    rf"(?<![{_ARABIC}])(?:يمكن الاعتماد|يمكن العمل|يعتمد عليه|يعمل به|العمل به|يستشهد|يحتج به|"
    rf"يتحدث|يتكلم عن|يدل علي|يحث|يدعو الي|يبين|يوضح|اهميه|معني الحديث|فضل|موثوق)"
)


@dataclass(frozen=True, slots=True)
class Extraction:
    intent: Literal["hadith_check", "out_of_scope"]
    hadith_text: str


@dataclass(frozen=True, slots=True)
class ExplainFacts:
    """The only data the explanation call is allowed to see."""

    hadith_text: str
    narrator: str | None
    muhaddith: str | None
    source_book: str | None
    number_or_page: str | None
    grade_text: str | None
    verdict_code: VerdictCode
    verdict_ar: str


class LLM(Protocol):
    async def extract(self, message: str) -> Extraction | None: ...

    async def explain(self, facts: ExplainFacts) -> str | None: ...


def validate_extraction(message: str, extraction: Extraction) -> Extraction | None:
    """Accept the LLM's text only if it is the user's own text, give or take typos.

    The model may fix spelling ("انماا لاعمال" -> "إنما الأعمال") but may not
    complete, extend or swap the hadith: at least ``MIN_TYPO_COVERAGE`` of the
    returned text must be made of runs of letters that appear, in order, in
    the user's message.
    """
    if extraction.intent == "out_of_scope":
        return Extraction("out_of_scope", "")
    text = extraction.hadith_text.strip()
    extracted = normalize(text).replace(" ", "")
    source = normalize(message).replace(" ", "")
    if not extracted:
        return None
    if extracted not in source and typo_coverage(source, extracted) < MIN_TYPO_COVERAGE:
        return None
    return Extraction("hadith_check", text)


def typo_coverage(source: str, candidate: str) -> float:
    """Share of ``candidate`` covered by in-order runs (>= 3 letters) from ``source``."""
    if not candidate:
        return 0.0
    blocks = SequenceMatcher(None, source, candidate, autojunk=False).get_matching_blocks()
    return sum(block.size for block in blocks if block.size >= MIN_RUN_CHARS) / len(candidate)


def validate_explanation(explanation: str | None, facts: ExplainFacts) -> str | None:
    """Return the explanation only if it cannot mislead; otherwise ``None``.

    Dropped when it is empty/too long, issues fiqh rulings or claims consensus,
    or contains wording that contradicts ``verdict_code`` (authentic wording
    for a weak/fabricated hadith, weak wording for an authentic one, or any
    ruling wording when the verdict is ``unclear``).
    """
    text = (explanation or "").strip()
    if not text or len(text) > MAX_EXPLANATION_CHARS:
        return None
    if len([s for s in _SENTENCE_END.split(text) if s.strip()]) > MAX_EXPLANATION_SENTENCES:
        return None

    # Names of books/scholars may legitimately contain ruling words
    # ("صحيح البخاري", "السلسلة الضعيفة"), so remove the given fields first.
    stripped = normalize(text)
    for field in (
        facts.source_book,
        facts.muhaddith,
        facts.narrator,
        facts.hadith_text,
        facts.grade_text,
    ):
        value = normalize(field)
        if value:
            stripped = stripped.replace(value, " ")
    if _FIQH_WORDS.search(stripped) or _OVERREACH.search(stripped):
        return None

    signals = analyze(stripped)
    code = facts.verdict_code
    if code in ACCEPTED and (signals.negative or signals.ambiguous):
        return None
    if code in REJECTED and signals.authentic:
        return None
    if code is VerdictCode.UNCLEAR and (signals.authentic or signals.negative):
        return None
    return text


class OpenAILLM:
    def __init__(self, *, api_key: str, model: str, timeout: float) -> None:
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout, max_retries=0)
        self._model = model

    async def close(self) -> None:
        await self._client.close()

    async def _json_call(
        self, system: str, user: str, schema: dict[str, object]
    ) -> dict[str, Any] | None:
        try:
            response_format: Any = {"type": "json_schema", "json_schema": schema}
            completion = await self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                max_tokens=600,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format=response_format,
            )
            content = completion.choices[0].message.content
            data = json.loads(content or "")
            return data if isinstance(data, dict) else None
        except Exception as exc:
            # Never log the prompt or the key; the error type is enough.
            logger.warning("llm call failed", extra={"error": type(exc).__name__})
            return None

    async def extract(self, message: str) -> Extraction | None:
        data = await self._json_call(prompts.EXTRACT_SYSTEM, message, prompts.EXTRACT_SCHEMA)
        if not data or data.get("intent") not in ("hadith_check", "out_of_scope"):
            return None
        return Extraction(data["intent"], str(data.get("hadith_text", "")))

    async def explain(self, facts: ExplainFacts) -> str | None:
        payload = json.dumps(
            {
                "hadith_text": facts.hadith_text,
                "narrator": facts.narrator,
                "muhaddith": facts.muhaddith,
                "source_book": facts.source_book,
                "number_or_page": facts.number_or_page,
                "grade_text": facts.grade_text,
                "verdict_code": facts.verdict_code.value,
                "verdict_ar": facts.verdict_ar,
            },
            ensure_ascii=False,
        )
        data = await self._json_call(prompts.EXPLAIN_SYSTEM, payload, prompts.EXPLAIN_SCHEMA)
        if not data:
            return None
        value = data.get("explanation_ar")
        return value if isinstance(value, str) else None
