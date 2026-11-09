"""Application log events, already parsed and masked."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from report_engine.domain.enums import LogLevel


@dataclass(frozen=True, slots=True)
class LogEvent:
    timestamp: datetime
    service: str
    level: LogLevel
    endpoint: str
    status_code: int
    latency_ms: int
    message: str
    # Pseudonymous user reference, used only to count affected users. Never persisted.
    user_ref: str | None = None

    @property
    def is_server_error(self) -> bool:
        return self.status_code >= 500 or self.level is LogLevel.ERROR

    @property
    def is_client_error(self) -> bool:
        return 400 <= self.status_code < 500


@dataclass(frozen=True, slots=True)
class LogBatch:
    events: tuple[LogEvent, ...]
    rejected_lines: int = 0
    masked_values: int = 0
