"""Business errors. Returned inside Result values, never raised."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    REPORT_DATE_IN_FUTURE = "REPORT_DATE_IN_FUTURE"
    NO_LOG_DATA = "NO_LOG_DATA"
    REPORT_NOT_FOUND = "REPORT_NOT_FOUND"
    REPORT_ALREADY_DELIVERED = "REPORT_ALREADY_DELIVERED"
    REPORT_NOT_SUMMARIZED = "REPORT_NOT_SUMMARIZED"
    REPORT_VERSION_CONFLICT = "REPORT_VERSION_CONFLICT"
    FAILURE_WORKFLOW_REQUIRED = "FAILURE_WORKFLOW_REQUIRED"


@dataclass(frozen=True, slots=True)
class DomainError:
    code: ErrorCode
    params: dict[str, Any] = field(default_factory=dict)
