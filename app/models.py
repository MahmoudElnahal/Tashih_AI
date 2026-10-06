"""Public API models (v1). Field names are stable; changes are additive only."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.verdict import VerdictCode

DISCLAIMER_AR = "الحكم منقول من المصدر وليس من النموذج. للفتوى راجع أهل العلم."


class Status(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    OUT_OF_SCOPE = "out_of_scope"
    UNAVAILABLE = "unavailable"


# ---------------------------------------------------------------- errors ----


class ErrorBody(BaseModel):
    code: str = Field(
        description="Machine-readable error code / رمز الخطأ", examples=["rate_limited"]
    )
    message_ar: str = Field(description="رسالة للمستخدم بالعربية")
    message_en: str = Field(description="Developer-facing message in English")
    request_id: str = Field(description="Same value as the X-Request-ID response header")


class ErrorResponse(BaseModel):
    """The single error envelope used by every non-2xx response."""

    error: ErrorBody

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": {
                    "code": "validation_error",
                    "message_ar": "البيانات المرسلة غير صحيحة.",
                    "message_en": "text: String should have at least 1 character",
                    "request_id": "6f1c1c3e-3f0b-4f43-9a55-0b8a2f7a1d11",
                }
            }
        }
    )


# ---------------------------------------------------------------- health ----


class HealthSources(BaseModel):
    dorar: str = Field(
        description="ok | blocked | unavailable | unknown | disabled",
        examples=["ok"],
    )
    offline: str = Field(description="ready | loading | error | disabled", examples=["ready"])


class HealthResponse(BaseModel):
    status: str = Field(description="ok | degraded", examples=["ok"])
    version: str = Field(examples=["0.1.0"])
    sources: HealthSources
    llm_enabled: bool = Field(description="True when OPENAI_API_KEY is configured")


# ---------------------------------------------------------------- verify ----


class Grade(BaseModel):
    scholar: str = Field(description="المحدِّث صاحب الحكم / the grading scholar")
    grade: str = Field(description="نص الحكم كما ورد في المصدر / grade text as given by the source")
    verdict_code: VerdictCode = Field(
        description="Deterministic classification of this single grade"
    )


class HadithMatch(BaseModel):
    hadith_text: str = Field(description="نص الحديث كما في المصدر")
    narrator: str | None = Field(description="الراوي (null when the source does not give it)")
    muhaddith: str | None = Field(description="المحدِّث صاحب الحكم الأساسي")
    source_book: str | None = Field(description="المصدر (الكتاب)")
    number_or_page: str | None = Field(description="الصفحة أو الرقم")
    grade_text: str | None = Field(description="خلاصة حكم المحدث — the primary grade text")
    grades: list[Grade] = Field(description="كل الأحكام المتاحة لهذا الحديث / all available grades")
    match_score: float = Field(ge=0, le=1, description="1.0 = exact normalized substring match")


class VerifyRequest(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=2000,
        description="نص الحديث المراد التحقق منه / the hadith text to verify",
        examples=["إنما الأعمال بالنيات"],
    )
    max_results: int = Field(
        default=3, ge=1, le=10, description="Maximum matches returned (best_match + other_matches)"
    )
    explain: bool = Field(
        default=True,
        description="Ask the LLM for a short grounded explanation (ignored when no LLM is configured)",
    )


class VerifyResponse(BaseModel):
    request_id: str
    status: Status = Field(description="found | not_found | out_of_scope | unavailable")
    verdict_code: VerdictCode | None = Field(
        description="sahih | hasan | daif | mawdu | unclear — null unless status=found"
    )
    verdict_ar: str | None = Field(description="Arabic label of the verdict", examples=["ضعيف جداً"])
    query: str = Field(description="The hadith text that was actually searched")
    best_match: HadithMatch | None
    other_matches: list[HadithMatch]
    explanation_ar: str | None = Field(
        description="Short LLM explanation grounded in best_match; null when unavailable or rejected"
    )
    message_ar: str = Field(description="Deterministic user-facing summary or non-found message")
    disclaimer_ar: str
    refer_to_scholars: bool
    scholars_differ: bool = Field(
        description="True when the listed scholars disagree (some accept, some reject)"
    )
    source_used: str | None = Field(
        description="dorar | offline_six_books — the source that answered; null if none did"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "request_id": "6f1c1c3e-3f0b-4f43-9a55-0b8a2f7a1d11",
                "status": "found",
                "verdict_code": "daif",
                "verdict_ar": "ضعيف",
                "query": "اطلبوا العلم ولو في الصين",
                "best_match": {
                    "hadith_text": "اطلبوا العلمَ ولو في الصِّينِ",
                    "narrator": None,
                    "muhaddith": "ابن باز",
                    "source_book": "التحفة الكريمة",
                    "number_or_page": "72",
                    "grade_text": "ضعيف من جميع طرقه [عند جمهور أهل العلم بالحديث]",
                    "grades": [
                        {
                            "scholar": "ابن باز",
                            "grade": "ضعيف من جميع طرقه [عند جمهور أهل العلم بالحديث]",
                            "verdict_code": "daif",
                        }
                    ],
                    "match_score": 1.0,
                },
                "other_matches": [],
                "explanation_ar": None,
                "message_ar": "حكم عليه ابن باز بقوله: «ضعيف من جميع طرقه». لا ينبغي نسبته إلى النبي ﷺ.",
                "disclaimer_ar": DISCLAIMER_AR,
                "refer_to_scholars": True,
                "scholars_differ": False,
                "source_used": "dorar",
            }
        }
    )


# ------------------------------------------------------------------ chat ----


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(
        min_length=1,
        max_length=50,
        description="Conversation so far; the last user message is the one answered",
    )
    session_id: str | None = Field(
        default=None, max_length=100, description="Opaque client id, echoed back"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "messages": [{"role": "user", "content": "ما صحة حديث اطلبوا العلم ولو في الصين؟"}],
                "session_id": None,
            }
        }
    )


class ChatResponse(VerifyResponse):
    reply_ar: str = Field(description="Chat-ready full reply (plain text with line breaks)")
    session_id: str | None = None
