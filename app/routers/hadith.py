"""v1 hadith routes: /v1/verify and /v1/chat."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from app.deps import AppState, enforce_rate_limit, get_state, require_api_key
from app.errors import request_id_of
from app.models import ChatRequest, ChatResponse, ErrorResponse, VerifyRequest, VerifyResponse

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Missing or invalid X-API-Key"},
    422: {"model": ErrorResponse, "description": "Validation error"},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded (see Retry-After)"},
    500: {"model": ErrorResponse, "description": "Unexpected server error"},
}

router = APIRouter(
    prefix="/v1",
    tags=["hadith"],
    dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)],
    responses=ERROR_RESPONSES,
)


@router.post(
    "/verify",
    response_model=VerifyResponse,
    summary="التحقق من حديث / Verify a hadith text",
    description=(
        "يبحث عن نص الحديث في المصادر ويعيد الحكم ومن حكم عليه والمصدر. "
        "الحكم يُحسب آلياً من نص حكم المحدِّث في المصدر، وليس من النموذج اللغوي.\n\n"
        "Looks the text up in the configured sources and returns the ruling, the grading "
        "scholar and the source. `verdict_code` is computed deterministically from the "
        "source's grade text, never by the LLM. A text that is not found with confidence "
        "returns `status=not_found`; when every source is down the status is `unavailable` "
        "(both with HTTP 200)."
    ),
)
async def verify(
    body: VerifyRequest, request: Request, state: AppState = Depends(get_state)
) -> VerifyResponse:
    return await state.verify.verify(
        body.text,
        max_results=body.max_results,
        request_id=request_id_of(request),
        explain=body.explain,
    )


@router.post(
    "/chat",
    response_model=ChatResponse,
    summary="محادثة التحقق من الأحاديث / Chat endpoint",
    description=(
        "يأخذ آخر رسالة من المستخدم، يحدد هل هي طلب تحقق من حديث، يستخرج نص الحديث، "
        "ثم يعيد نفس حقول `/v1/verify` مع رد جاهز للعرض في `reply_ar`.\n\n"
        "Answers the last user message. Non-hadith requests get `status=out_of_scope`. "
        "Same fields as `/v1/verify` plus `reply_ar` and `session_id`."
    ),
)
async def chat(
    body: ChatRequest, request: Request, state: AppState = Depends(get_state)
) -> ChatResponse:
    return await state.chat.chat(body, request_id=request_id_of(request))
