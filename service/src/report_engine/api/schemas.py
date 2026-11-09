"""HTTP DTOs (camelCase on the wire). No raw log text and no personal data leave the service."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from report_engine.application.use_cases import PipelineMetrics
from report_engine.domain.anomalies import Anomaly
from report_engine.domain.enums import (
    AlertLevel,
    AnomalyKind,
    HealthStatus,
    ReportStatus,
    SummarySource,
)
from report_engine.domain.report import DailyReport


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class CreateReportRequest(CamelModel):
    report_date: date | None = Field(default=None, description="Defaults to yesterday.")


class SummaryRequest(CamelModel):
    version: int = Field(ge=1)
    llm_response: dict[str, Any] | None = Field(
        default=None, description="Raw Claude Messages API response, or null if the call failed."
    )


class DeliveryRequest(CamelModel):
    version: int = Field(ge=1)


class FailureRequest(CamelModel):
    workflow_name: str = Field(min_length=1, max_length=200)
    node_name: str | None = Field(default=None, max_length=200)
    message: str | None = Field(default=None, max_length=4000)
    execution_id: str | None = Field(default=None, max_length=64)


class AnomalyDto(CamelModel):
    kind: AnomalyKind
    level: AlertLevel
    scope: str
    observed: float
    reference: float

    @classmethod
    def of(cls, item: Anomaly) -> AnomalyDto:
        return cls(
            kind=item.kind,
            level=item.level,
            scope=item.scope,
            observed=item.observed,
            reference=item.reference,
        )


class KpiDto(CamelModel):
    total_requests: int
    server_errors: int
    error_rate: float
    p95_latency_ms: int
    affected_users: int
    rejected_lines: int


class UsageDto(CamelModel):
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal


class ReportResponse(CamelModel):
    id: str
    report_date: date
    status: ReportStatus
    health: HealthStatus
    summary_source: SummarySource | None
    kpis: KpiDto
    anomalies: list[AnomalyDto]
    usage: UsageDto | None
    generation_ms: int
    pipeline_seconds: float | None
    generated_at: datetime
    delivered_at: datetime | None
    version: int

    @classmethod
    def of(cls, report: DailyReport) -> ReportResponse:
        metrics = report.metrics
        usage = report.usage
        return cls(
            id=report.id,
            report_date=report.report_date,
            status=report.status,
            health=report.health,
            summary_source=report.summary_source,
            kpis=KpiDto(
                total_requests=metrics.total_requests,
                server_errors=metrics.server_errors,
                error_rate=metrics.error_rate,
                p95_latency_ms=metrics.p95_latency_ms,
                affected_users=metrics.affected_users,
                rejected_lines=metrics.rejected_lines,
            ),
            anomalies=[AnomalyDto.of(item) for item in report.anomalies],
            usage=UsageDto(
                model=usage.model_name,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=usage.cost_usd,
            )
            if usage
            else None,
            generation_ms=report.generation_ms,
            pipeline_seconds=report.pipeline_seconds,
            generated_at=report.generated_at,
            delivered_at=report.delivered_at,
            version=report.version,
        )


class CreatedReportResponse(ReportResponse):
    llm_request: dict[str, Any] = Field(
        description="Claude Messages API body. n8n sends it with its own encrypted credential."
    )


class EmailDto(CamelModel):
    sender: str
    recipients: list[str]
    subject: str
    html: str
    text: str


class SummarizedReportResponse(CamelModel):
    report: ReportResponse
    email: EmailDto


class FailureResponse(CamelModel):
    id: str
    email: EmailDto


class MetricsResponse(CamelModel):
    reports_generated: int
    reports_delivered: int
    ai_summaries: int
    fallback_summaries: int
    pipeline_failures: int
    success_rate: float | None
    avg_pipeline_seconds: float | None
    total_llm_cost_usd: Decimal
    manual_minutes_per_report: int
    estimated_hours_saved: float

    @classmethod
    def of(cls, metrics: PipelineMetrics) -> MetricsResponse:
        stats = metrics.stats
        return cls(
            reports_generated=stats.generated,
            reports_delivered=stats.delivered,
            ai_summaries=stats.ai_summaries,
            fallback_summaries=stats.fallback_summaries,
            pipeline_failures=stats.failures,
            success_rate=metrics.success_rate,
            avg_pipeline_seconds=stats.avg_pipeline_seconds,
            total_llm_cost_usd=stats.total_llm_cost_usd,
            manual_minutes_per_report=metrics.manual_minutes_per_report,
            estimated_hours_saved=metrics.estimated_hours_saved,
        )
