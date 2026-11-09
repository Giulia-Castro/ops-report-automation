"""DailyReport aggregate and PipelineFailure entity."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from report_engine.domain.anomalies import Anomaly, health_status
from report_engine.domain.enums import HealthStatus, ReportStatus, SummarySource
from report_engine.domain.errors import DomainError, ErrorCode
from report_engine.domain.masking import mask_personal_data
from report_engine.domain.metrics import DailyMetrics
from report_engine.domain.result import Err, Ok, Result

FAILURE_MESSAGE_MAX = 500
FAILURE_NAME_MAX = 100


@dataclass(frozen=True, slots=True)
class ExecutiveSummary:
    headline: str
    highlights: tuple[str, ...]
    risks: tuple[str, ...]
    actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LlmUsage:
    model_name: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


@dataclass(slots=True)
class DailyReport:
    id: str
    report_date: date
    status: ReportStatus
    metrics: DailyMetrics
    anomalies: tuple[Anomaly, ...]
    generation_ms: int
    generated_at: datetime
    created_at: datetime
    created_by: str
    updated_at: datetime
    updated_by: str
    summary: ExecutiveSummary | None = None
    summary_source: SummarySource | None = None
    usage: LlmUsage | None = None
    delivered_at: datetime | None = None
    version: int = 1

    @property
    def health(self) -> HealthStatus:
        return health_status(self.anomalies)

    @property
    def pipeline_seconds(self) -> float | None:
        if self.delivered_at is None:
            return None
        return round((self.delivered_at - self.generated_at).total_seconds(), 1)

    @classmethod
    def generate(
        cls,
        report_id: str,
        report_date: date,
        metrics: DailyMetrics,
        anomalies: tuple[Anomaly, ...],
        generation_ms: int,
        actor: str,
        now: datetime,
    ) -> DailyReport:
        return cls(
            id=report_id,
            report_date=report_date,
            status=ReportStatus.GENERATED,
            metrics=metrics,
            anomalies=anomalies,
            generation_ms=generation_ms,
            generated_at=now,
            created_at=now,
            created_by=actor,
            updated_at=now,
            updated_by=actor,
        )

    def regenerate(
        self,
        metrics: DailyMetrics,
        anomalies: tuple[Anomaly, ...],
        generation_ms: int,
        actor: str,
        now: datetime,
    ) -> Result[None, DomainError]:
        """Re-run for the same day (e.g. late logs). A delivered report is final."""
        if self.status is ReportStatus.DELIVERED:
            return Err(_already_delivered(self))
        self.metrics = metrics
        self.anomalies = anomalies
        self.generation_ms = generation_ms
        self.status = ReportStatus.GENERATED
        self.summary = None
        self.summary_source = None
        self.usage = None
        self.generated_at = now
        self._touch(actor, now)
        return Ok(None)

    def attach_summary(
        self,
        summary: ExecutiveSummary,
        source: SummarySource,
        usage: LlmUsage | None,
        expected_version: int,
        actor: str,
        now: datetime,
    ) -> Result[None, DomainError]:
        if self.status is ReportStatus.DELIVERED:
            return Err(_already_delivered(self))
        if expected_version != self.version:
            return Err(_conflict(self, expected_version))
        self.summary = summary
        self.summary_source = source
        self.usage = usage
        self.status = ReportStatus.SUMMARIZED
        self._touch(actor, now)
        return Ok(None)

    def mark_delivered(
        self, expected_version: int, actor: str, now: datetime
    ) -> Result[None, DomainError]:
        if self.status is ReportStatus.DELIVERED:
            return Err(_already_delivered(self))
        if self.status is not ReportStatus.SUMMARIZED:
            return Err(DomainError(ErrorCode.REPORT_NOT_SUMMARIZED, {"id": self.id}))
        if expected_version != self.version:
            return Err(_conflict(self, expected_version))
        self.status = ReportStatus.DELIVERED
        self.delivered_at = now
        self._touch(actor, now)
        return Ok(None)

    def _touch(self, actor: str, now: datetime) -> None:
        self.updated_at = now
        self.updated_by = actor


def _already_delivered(report: DailyReport) -> DomainError:
    return DomainError(
        ErrorCode.REPORT_ALREADY_DELIVERED, {"reportDate": report.report_date.isoformat()}
    )


def _conflict(report: DailyReport, expected: int) -> DomainError:
    return DomainError(
        ErrorCode.REPORT_VERSION_CONFLICT, {"expected": expected, "current": report.version}
    )


@dataclass(frozen=True, slots=True)
class PipelineFailure:
    id: str
    workflow_name: str
    node_name: str | None
    message: str | None
    execution_id: str | None
    occurred_at: datetime
    created_by: str
    masked_values: int = field(default=0, compare=False)

    @classmethod
    def record(
        cls,
        failure_id: str,
        workflow_name: str,
        node_name: str | None,
        message: str | None,
        execution_id: str | None,
        actor: str,
        now: datetime,
    ) -> Result[PipelineFailure, DomainError]:
        name = workflow_name.strip()
        if not name:
            return Err(DomainError(ErrorCode.FAILURE_WORKFLOW_REQUIRED))
        masked = mask_personal_data(message or "")
        return Ok(
            cls(
                id=failure_id,
                workflow_name=name[:FAILURE_NAME_MAX],
                node_name=node_name.strip()[:FAILURE_NAME_MAX] if node_name else None,
                message=masked.text[:FAILURE_MESSAGE_MAX] or None,
                execution_id=execution_id,
                occurred_at=now,
                created_by=actor,
                masked_values=masked.total,
            )
        )
