"""Single error envelope for every non-2xx response."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.models import ErrorBody, ErrorResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message_ar: str,
        message_en: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message_en)
        self.status_code = status_code
        self.code = code
        self.message_ar = message_ar
        self.message_en = message_en
        self.headers = headers


def unauthorized() -> AppError:
    return AppError(
        401, "unauthorized", "مفتاح الواجهة غير صحيح أو مفقود.", "Missing or invalid API key."
    )


def rate_limited(retry_after: int) -> AppError:
    return AppError(
        429,
        "rate_limited",
        "عدد الطلبات كبير. حاول مرة أخرى بعد قليل.",
        "Too many requests. Please retry later.",
        headers={"Retry-After": str(retry_after)},
    )


def upstream_unavailable(service: str, *, timeout: bool = False) -> AppError:
    return AppError(
        504 if timeout else 502,
        "upstream_timeout" if timeout else "upstream_error",
        "تعذر الوصول إلى الخدمة الخارجية حالياً. حاول مرة أخرى بعد قليل.",
        f"Upstream service '{service}' {'timed out' if timeout else 'returned an error'}.",
    )


def service_not_ready(what: str) -> AppError:
    return AppError(
        503,
        "service_not_ready",
        "الخدمة ما زالت تُجهَّز. حاول مرة أخرى بعد قليل.",
        f"{what} is not ready yet.",
        headers={"Retry-After": "15"},
    )


def request_id_of(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _envelope(
    request: Request,
    status_code: int,
    code: str,
    message_ar: str,
    message_en: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code,
            message_ar=message_ar,
            message_en=message_en,
            request_id=request_id_of(request),
        )
    )
    return JSONResponse(body.model_dump(), status_code=status_code, headers=headers)


_HTTP_CODES = {
    404: ("not_found", "المسار غير موجود."),
    405: ("method_not_allowed", "الطريقة غير مسموح بها."),
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return _envelope(
            request, exc.status_code, exc.code, exc.message_ar, exc.message_en, exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        detail = f"{where}: {first.get('msg', 'invalid')}" if where else "Invalid request."
        return _envelope(request, 422, "validation_error", "البيانات المرسلة غير صحيحة.", detail)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, message_ar = _HTTP_CODES.get(exc.status_code, ("http_error", "حدث خطأ في الطلب."))
        return _envelope(request, exc.status_code, code, message_ar, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"request_id": request_id_of(request)})
        return _envelope(
            request,
            500,
            "internal_error",
            "حدث خطأ غير متوقع. حاول مرة أخرى.",
            "Internal server error.",
        )
