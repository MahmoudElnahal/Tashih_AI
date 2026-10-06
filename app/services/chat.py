"""Chat router: last user message -> intent -> handler -> chat-style reply.

Heuristics run first; the LLM is called only when they are unsure or when the
deterministic search found nothing and a cleaner extraction might help.
"""

from __future__ import annotations

import logging

from app.models import DISCLAIMER_AR, ChatRequest, ChatResponse, Status, VerifyResponse
from app.services.intent import Intent, guess_intent
from app.services.llm import LLM, Extraction, validate_extraction
from app.services.normalize import normalize
from app.services.verify import VerifyService

logger = logging.getLogger(__name__)

MSG_OUT_OF_SCOPE = (
    "أنا «تصحيح»، مساعد متخصص في التحقق من الأحاديث فقط. أرسل لي نص الحديث وسأذكر لك "
    "حكمه ومن حكم عليه ومصدره. لا أجيب عن الأسئلة الفقهية أو التفسير أو غيرها؛ "
    "لهذه المسائل يُرجى سؤال أهل العلم."
)
MSG_CORRECTED = " (صُحِّحت كتابة النص قبل البحث إلى: «{text}».)"
MSG_NEEDS_TEXT = "من فضلك اكتب نص الحديث الذي تريد التحقق منه، وسأبحث عن حكمه ومصدره."


def build_reply(result: VerifyResponse) -> str:
    """Deterministic chat reply assembled from the response fields."""
    if result.status is not Status.FOUND or result.best_match is None:
        return result.message_ar

    best = result.best_match
    lines = [f"الحكم: {result.verdict_ar}", f"نص الحديث: {best.hadith_text}"]
    if best.narrator:
        lines.append(f"الراوي: {best.narrator}")
    if best.muhaddith:
        lines.append(f"المحدِّث: {best.muhaddith}")
    source = " — ".join(part for part in (best.source_book, best.number_or_page) if part)
    if source:
        lines.append(f"المصدر: {source}")
    if best.grade_text:
        lines.append(f"خلاصة حكم المحدِّث: {best.grade_text}")
    lines.append("")
    lines.append(result.message_ar)
    if result.explanation_ar:
        lines.append(result.explanation_ar)
    lines.append("")
    lines.append(result.disclaimer_ar)
    return "\n".join(lines)


class ChatService:
    def __init__(self, verify: VerifyService, llm: LLM | None) -> None:
        self._verify = verify
        self._llm = llm

    async def chat(self, request: ChatRequest, *, request_id: str) -> ChatResponse:
        message = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
        result = await self._answer(message.strip(), request_id)
        return ChatResponse(
            **result.model_dump(), reply_ar=build_reply(result), session_id=request.session_id
        )

    async def _answer(self, message: str, request_id: str) -> VerifyResponse:
        guess = guess_intent(message)
        if guess.intent is Intent.OUT_OF_SCOPE:
            return self._static(request_id, message, MSG_OUT_OF_SCOPE)
        if guess.intent is Intent.NEEDS_TEXT:
            return self._static(request_id, message, MSG_NEEDS_TEXT)

        result = await self._verify.verify(guess.hadith_text, max_results=3, request_id=request_id)
        if result.status is not Status.NOT_FOUND:
            return result

        # Deterministic search found nothing: ask the LLM for a cleaner
        # extraction (it can never change the ruling, only the search text).
        extraction = await self._extract(message)
        if extraction is None:
            return (
                result if guess.confident else self._static(request_id, message, MSG_OUT_OF_SCOPE)
            )
        if extraction.intent == "out_of_scope":
            return (
                result if guess.confident else self._static(request_id, message, MSG_OUT_OF_SCOPE)
            )
        corrected_text = normalize(extraction.hadith_text)
        if not corrected_text or corrected_text == normalize(guess.hadith_text):
            return result
        corrected = await self._verify.verify(
            extraction.hadith_text, max_results=3, request_id=request_id
        )
        if corrected.status is Status.FOUND:
            note = MSG_CORRECTED.format(text=corrected.query)
            return corrected.model_copy(update={"message_ar": corrected.message_ar + note})
        return corrected

    async def _extract(self, message: str) -> Extraction | None:
        """Validated LLM extraction, or ``None`` when there is no usable answer.

        A hadith request whose corrected text fails validation comes back as
        ``hadith_check`` with empty text: the intent is trusted, the text is not.
        """
        if self._llm is None:
            return None
        try:
            raw = await self._llm.extract(message)
        except Exception as exc:
            logger.warning("extraction failed", extra={"error": type(exc).__name__})
            return None
        if raw is None:
            return None
        validated = validate_extraction(message, raw)
        if validated is None:
            logger.warning("extraction rejected by validator")
            return Extraction("hadith_check", "")
        return validated

    @staticmethod
    def _static(request_id: str, message: str, text: str) -> VerifyResponse:
        return VerifyResponse(
            request_id=request_id,
            status=Status.OUT_OF_SCOPE,
            verdict_code=None,
            verdict_ar=None,
            query=message,
            best_match=None,
            other_matches=[],
            explanation_ar=None,
            message_ar=text,
            disclaimer_ar=DISCLAIMER_AR,
            refer_to_scholars=False,
            scholars_differ=False,
            source_used=None,
        )
