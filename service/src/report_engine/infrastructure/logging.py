"""Structured JSON logging with correlation id and personal data masking."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from report_engine.domain.masking import mask_personal_data

correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")


def _mask(value: Any) -> Any:
    if isinstance(value, str):
        return mask_personal_data(value).text
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _mask(record.getMessage()),
            "correlationId": correlation_id_var.get(),
        }
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            payload["context"] = {key: _mask(value) for key, value in context.items()}
        if record.exc_info:
            payload["exception"] = _mask(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    # The SDK and HTTP client log request details at DEBUG; keep them quiet by default.
    for noisy in ("httpx", "httpx2", "anthropic", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
