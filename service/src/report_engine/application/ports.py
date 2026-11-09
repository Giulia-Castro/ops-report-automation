"""Ports: what the use cases need from the outside world."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from report_engine.domain.errors import DomainError
from report_engine.domain.logs import LogBatch
from report_engine.domain.metrics import DailyMetrics
from report_engine.domain.report import (
    DailyReport,
    ExecutiveSummary,
    LlmUsage,
    PipelineFailure,
)
from report_engine.domain.result import Result

type Clock = Callable[[], datetime]
type IdFactory = Callable[[], str]


class LogSource(Protocol):
    def read(self, day: date) -> LogBatch | None:
        """Return the day's events, or None when there is no log file for that day."""
        ...


@dataclass(frozen=True, slots=True)
class ReportStats:
    generated: int
    delivered: int
    ai_summaries: int
    fallback_summaries: int
    avg_pipeline_seconds: float | None
    total_llm_cost_usd: Decimal
    failures: int


class ReportRepository(Protocol):
    def get(self, report_id: str) -> DailyReport | None: ...

    def get_by_date(self, report_date: date) -> DailyReport | None: ...

    def add(self, report: DailyReport) -> DailyReport: ...

    def update(self, report: DailyReport) -> Result[DailyReport, DomainError]:
        """Persist with optimistic locking on report.version; returns the new version."""
        ...

    def history(self, before: date, days: int) -> list[DailyMetrics]: ...

    def list_recent(self, limit: int) -> list[DailyReport]: ...

    def stats(self) -> ReportStats: ...

    def add_failure(self, failure: PipelineFailure) -> None: ...


class SummaryRequestBuilder(Protocol):
    def build(self, report: DailyReport) -> dict[str, Any]:
        """Build the LLM request body. The orchestrator (n8n) sends it with its own credential."""
        ...


@dataclass(frozen=True, slots=True)
class ParsedSummary:
    summary: ExecutiveSummary
    usage: LlmUsage


class SummaryParser(Protocol):
    def parse(self, raw: Mapping[str, Any] | None) -> Result[ParsedSummary, str]:
        """Validate the raw LLM response. The error value is a short reason code for logs."""
        ...


class FallbackSummarizer(Protocol):
    def summarize(self, report: DailyReport) -> ExecutiveSummary: ...


@dataclass(frozen=True, slots=True)
class RenderedReport:
    subject: str
    html: str
    text: str


@dataclass(frozen=True, slots=True)
class RenderedAlert:
    subject: str
    text: str


class ReportRenderer(Protocol):
    def render(self, report: DailyReport) -> RenderedReport: ...

    def render_failure(self, failure: PipelineFailure) -> RenderedAlert: ...
