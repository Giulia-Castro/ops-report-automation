"""Use cases. Orchestrate domain rules through ports; no framework or I/O details here."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from report_engine.application.ports import (
    Clock,
    FallbackSummarizer,
    IdFactory,
    LogSource,
    RenderedAlert,
    RenderedReport,
    ReportRenderer,
    ReportRepository,
    ReportStats,
    SummaryParser,
    SummaryRequestBuilder,
)
from report_engine.domain.anomalies import AlertPolicy, Baseline, detect_anomalies
from report_engine.domain.enums import SummarySource
from report_engine.domain.errors import DomainError, ErrorCode
from report_engine.domain.metrics import compute_metrics
from report_engine.domain.report import DailyReport, PipelineFailure
from report_engine.domain.result import Err, Ok, Result

logger = logging.getLogger("report_engine.use_cases")


def _not_found(report_id: str) -> DomainError:
    return DomainError(ErrorCode.REPORT_NOT_FOUND, {"id": report_id})


@dataclass(frozen=True, slots=True)
class GeneratedReport:
    report: DailyReport
    llm_request: dict[str, Any]


class GenerateReportUseCase:
    def __init__(
        self,
        source: LogSource,
        repository: ReportRepository,
        request_builder: SummaryRequestBuilder,
        policy: AlertPolicy,
        clock: Clock,
        id_factory: IdFactory,
        timezone: ZoneInfo,
        history_days: int = 7,
        top_errors: int = 5,
    ) -> None:
        self._source = source
        self._repository = repository
        self._builder = request_builder
        self._policy = policy
        self._clock = clock
        self._ids = id_factory
        self._tz = timezone
        self._history_days = history_days
        self._top_errors = top_errors

    def execute(self, report_date: date | None, actor: str) -> Result[GeneratedReport, DomainError]:
        now = self._clock()
        today = now.astimezone(self._tz).date()
        day = report_date or today - timedelta(days=1)
        if day > today:
            return Err(
                DomainError(ErrorCode.REPORT_DATE_IN_FUTURE, {"reportDate": day.isoformat()})
            )

        existing = self._repository.get_by_date(day)
        if existing is not None and existing.delivered_at is not None:
            return Err(
                DomainError(ErrorCode.REPORT_ALREADY_DELIVERED, {"reportDate": day.isoformat()})
            )

        started = time.perf_counter()
        batch = self._source.read(day)
        if batch is None or not batch.events:
            return Err(DomainError(ErrorCode.NO_LOG_DATA, {"reportDate": day.isoformat()}))
        metrics = compute_metrics(batch, self._top_errors)
        baseline = Baseline.from_history(self._repository.history(day, self._history_days))
        anomalies = detect_anomalies(metrics, baseline, self._policy)
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        if existing is None:
            report = self._repository.add(
                DailyReport.generate(self._ids(), day, metrics, anomalies, elapsed_ms, actor, now)
            )
        else:
            regenerated = existing.regenerate(metrics, anomalies, elapsed_ms, actor, now)
            if isinstance(regenerated, Err):
                return regenerated
            updated = self._repository.update(existing)
            if isinstance(updated, Err):
                return updated
            report = updated.value

        logger.info(
            "report.generated",
            extra={
                "context": {
                    "reportId": report.id,
                    "reportDate": day.isoformat(),
                    "events": metrics.total_requests,
                    "rejectedLines": batch.rejected_lines,
                    "maskedValues": batch.masked_values,
                    "anomalies": len(anomalies),
                    "health": report.health.value,
                    "baselineDays": baseline.days if baseline else 0,
                }
            },
        )
        return Ok(GeneratedReport(report=report, llm_request=self._builder.build(report)))


@dataclass(frozen=True, slots=True)
class SummarizedReport:
    report: DailyReport
    rendered: RenderedReport


class SummarizeReportUseCase:
    """Attach the LLM summary, or a deterministic fallback when the LLM call failed."""

    def __init__(
        self,
        repository: ReportRepository,
        parser: SummaryParser,
        fallback: FallbackSummarizer,
        renderer: ReportRenderer,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._parser = parser
        self._fallback = fallback
        self._renderer = renderer
        self._clock = clock

    def execute(
        self,
        report_id: str,
        expected_version: int,
        llm_response: Mapping[str, Any] | None,
        actor: str,
    ) -> Result[SummarizedReport, DomainError]:
        report = self._repository.get(report_id)
        if report is None:
            return Err(_not_found(report_id))

        parsed = self._parser.parse(llm_response)
        if isinstance(parsed, Ok):
            summary, source, usage = parsed.value.summary, SummarySource.AI, parsed.value.usage
        else:
            summary, source, usage = self._fallback.summarize(report), SummarySource.FALLBACK, None
            logger.warning(
                "report.summary.fallback",
                extra={"context": {"reportId": report_id, "reason": parsed.error}},
            )

        attached = report.attach_summary(
            summary, source, usage, expected_version, actor, self._clock()
        )
        if isinstance(attached, Err):
            return attached
        updated = self._repository.update(report)
        if isinstance(updated, Err):
            return updated
        logger.info(
            "report.summarized",
            extra={
                "context": {
                    "reportId": report_id,
                    "source": source.value,
                    "costUsd": str(usage.cost_usd) if usage else "0",
                }
            },
        )
        return Ok(SummarizedReport(updated.value, self._renderer.render(updated.value)))


class MarkDeliveredUseCase:
    def __init__(self, repository: ReportRepository, clock: Clock) -> None:
        self._repository = repository
        self._clock = clock

    def execute(
        self, report_id: str, expected_version: int, actor: str
    ) -> Result[DailyReport, DomainError]:
        report = self._repository.get(report_id)
        if report is None:
            return Err(_not_found(report_id))
        delivered = report.mark_delivered(expected_version, actor, self._clock())
        if isinstance(delivered, Err):
            return delivered
        updated = self._repository.update(report)
        if isinstance(updated, Ok):
            logger.info(
                "report.delivered",
                extra={
                    "context": {
                        "reportId": report_id,
                        "pipelineSeconds": updated.value.pipeline_seconds,
                    }
                },
            )
        return updated


class GetReportUseCase:
    def __init__(self, repository: ReportRepository) -> None:
        self._repository = repository

    def execute(self, report_id: str) -> Result[DailyReport, DomainError]:
        report = self._repository.get(report_id)
        return Ok(report) if report else Err(_not_found(report_id))


class ListReportsUseCase:
    def __init__(self, repository: ReportRepository) -> None:
        self._repository = repository

    def execute(self, limit: int) -> list[DailyReport]:
        return self._repository.list_recent(limit)


@dataclass(frozen=True, slots=True)
class PipelineMetrics:
    stats: ReportStats
    manual_minutes_per_report: int

    @property
    def estimated_hours_saved(self) -> float:
        return round(self.stats.delivered * self.manual_minutes_per_report / 60, 1)

    @property
    def success_rate(self) -> float | None:
        attempts = self.stats.delivered + self.stats.failures
        return round(self.stats.delivered / attempts, 4) if attempts else None


class GetMetricsUseCase:
    def __init__(self, repository: ReportRepository, manual_minutes_per_report: int) -> None:
        self._repository = repository
        self._manual_minutes = manual_minutes_per_report

    def execute(self) -> PipelineMetrics:
        return PipelineMetrics(self._repository.stats(), self._manual_minutes)


@dataclass(frozen=True, slots=True)
class RecordedFailure:
    failure: PipelineFailure
    alert: RenderedAlert


class RecordFailureUseCase:
    """Called by the n8n error workflow: count the failure and build the alert text."""

    def __init__(
        self,
        repository: ReportRepository,
        renderer: ReportRenderer,
        clock: Clock,
        id_factory: IdFactory,
    ) -> None:
        self._repository = repository
        self._renderer = renderer
        self._clock = clock
        self._ids = id_factory

    def execute(
        self,
        workflow_name: str,
        node_name: str | None,
        message: str | None,
        execution_id: str | None,
        actor: str,
    ) -> Result[RecordedFailure, DomainError]:
        recorded = PipelineFailure.record(
            self._ids(), workflow_name, node_name, message, execution_id, actor, self._clock()
        )
        if isinstance(recorded, Err):
            return recorded
        failure = recorded.value
        self._repository.add_failure(failure)
        logger.error(
            "pipeline.failure",
            extra={
                "context": {
                    "failureId": failure.id,
                    "workflow": failure.workflow_name,
                    "node": failure.node_name,
                    "executionId": failure.execution_id,
                }
            },
        )
        return Ok(RecordedFailure(failure, self._renderer.render_failure(failure)))
