"""RFC 7807 Problem Details responses with localized title and detail."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from report_engine.domain.errors import DomainError, ErrorCode
from report_engine.infrastructure.i18n import resolve_locale, translate_error
from report_engine.infrastructure.logging import correlation_id_var

PROBLEM_CONTENT_TYPE = "application/problem+json"


class ApiErrorCode(StrEnum):
    VALIDATION_FAILED = "VALIDATION_FAILED"
    UNAUTHORIZED = "UNAUTHORIZED"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"


DOMAIN_STATUS: dict[ErrorCode, int] = {
    ErrorCode.REPORT_DATE_IN_FUTURE: 422,
    ErrorCode.NO_LOG_DATA: 422,
    ErrorCode.REPORT_NOT_FOUND: 404,
    ErrorCode.REPORT_ALREADY_DELIVERED: 409,
    ErrorCode.REPORT_NOT_SUMMARIZED: 409,
    ErrorCode.REPORT_VERSION_CONFLICT: 409,
    ErrorCode.FAILURE_WORKFLOW_REQUIRED: 422,
}


def problem_response(
    request: Request,
    status: int,
    code: str,
    params: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    locale = resolve_locale(request.headers.get("accept-language"))
    body: dict[str, Any] = {
        "type": f"urn:ops-report:problem:{code.lower().replace('_', '-')}",
        "title": translate_error(locale, code, "title", params),
        "status": status,
        "detail": translate_error(locale, code, "detail", params),
        "instance": request.url.path,
        "code": code,
        "correlationId": correlation_id_var.get(),
    }
    if errors:
        body["errors"] = errors
    return JSONResponse(
        status_code=status,
        content=body,
        media_type=PROBLEM_CONTENT_TYPE,
        headers={"Content-Language": locale, **(headers or {})},
    )


def domain_problem(request: Request, error: DomainError) -> JSONResponse:
    return problem_response(request, DOMAIN_STATUS[error.code], error.code.value, error.params)
