"""Fixed codes. Persisted as strings so the database stays readable and portable."""

from __future__ import annotations

from enum import StrEnum


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


class AlertLevel(StrEnum):
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return 2 if self is AlertLevel.CRITICAL else 1


class AnomalyKind(StrEnum):
    ERROR_RATE_SLO = "ERROR_RATE_SLO"
    ERROR_RATE_SPIKE = "ERROR_RATE_SPIKE"
    LATENCY_SLO = "LATENCY_SLO"
    LATENCY_SPIKE = "LATENCY_SPIKE"
    VOLUME_DROP = "VOLUME_DROP"


class HealthStatus(StrEnum):
    GREEN = "GREEN"
    YELLOW = "YELLOW"
    RED = "RED"


class ReportStatus(StrEnum):
    GENERATED = "GENERATED"
    SUMMARIZED = "SUMMARIZED"
    DELIVERED = "DELIVERED"


class SummarySource(StrEnum):
    AI = "AI"
    FALLBACK = "FALLBACK"
