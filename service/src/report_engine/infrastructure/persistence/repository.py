"""SQLAlchemy implementation of ReportRepository."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from statistics import fmean
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from report_engine.application.ports import ReportStats
from report_engine.domain.anomalies import Anomaly
from report_engine.domain.enums import (
    AlertLevel,
    AnomalyKind,
    ReportStatus,
    SummarySource,
)
from report_engine.domain.errors import DomainError, ErrorCode
from report_engine.domain.metrics import DailyMetrics
from report_engine.domain.report import (
    DailyReport,
    ExecutiveSummary,
    LlmUsage,
    PipelineFailure,
)
from report_engine.domain.result import Err, Ok, Result
from report_engine.infrastructure.persistence.orm import FailureRow, ReportRow


def utc_now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _anomalies_to_json(anomalies: tuple[Anomaly, ...]) -> str:
    return json.dumps(
        [
            {
                "kind": item.kind.value,
                "level": item.level.value,
                "scope": item.scope,
                "observed": item.observed,
                "reference": item.reference,
            }
            for item in anomalies
        ]
    )


def _anomalies_from_json(raw: str) -> tuple[Anomaly, ...]:
    return tuple(
        Anomaly(
            kind=AnomalyKind(item["kind"]),
            level=AlertLevel(item["level"]),
            scope=item["scope"],
            observed=item["observed"],
            reference=item["reference"],
        )
        for item in json.loads(raw)
    )


def _summary_to_json(summary: ExecutiveSummary | None) -> str | None:
    if summary is None:
        return None
    return json.dumps(
        {
            "headline": summary.headline,
            "highlights": list(summary.highlights),
            "risks": list(summary.risks),
            "actions": list(summary.actions),
        },
        ensure_ascii=False,
    )


def _summary_from_json(raw: str | None) -> ExecutiveSummary | None:
    if not raw:
        return None
    data: dict[str, Any] = json.loads(raw)
    return ExecutiveSummary(
        headline=data["headline"],
        highlights=tuple(data["highlights"]),
        risks=tuple(data["risks"]),
        actions=tuple(data["actions"]),
    )


def _to_domain(row: ReportRow) -> DailyReport:
    usage = None
    if row.llmmodel is not None:
        usage = LlmUsage(
            model_name=row.llmmodel,
            input_tokens=row.inputtokens or 0,
            output_tokens=row.outputtokens or 0,
            cost_usd=Decimal(row.costusd or 0),
        )
    return DailyReport(
        id=row.id,
        report_date=row.reportdate,
        status=ReportStatus(row.reportstatus),
        metrics=DailyMetrics.from_dict(json.loads(row.metricsjson)),
        anomalies=_anomalies_from_json(row.anomaliesjson),
        generation_ms=row.generationms,
        generated_at=_aware(row.generatedat),  # type: ignore[arg-type]
        created_at=_aware(row.createdat),  # type: ignore[arg-type]
        created_by=row.createdby,
        updated_at=_aware(row.updatedat),  # type: ignore[arg-type]
        updated_by=row.updatedby,
        summary=_summary_from_json(row.summaryjson),
        summary_source=SummarySource(row.summarysource) if row.summarysource else None,
        usage=usage,
        delivered_at=_aware(row.deliveredat),
        version=row.rowversion,
    )


def _copy_to_row(report: DailyReport, row: ReportRow) -> None:
    metrics = report.metrics
    row.reportdate = report.report_date
    row.reportstatus = report.status.value
    row.healthstatus = report.health.value
    row.summarysource = report.summary_source.value if report.summary_source else None
    row.totalrequests = metrics.total_requests
    row.servererrors = metrics.server_errors
    row.errorrate = Decimal(str(metrics.error_rate))
    row.p95latencyms = metrics.p95_latency_ms
    row.anomalycount = len(report.anomalies)
    row.metricsjson = json.dumps(metrics.to_dict(), ensure_ascii=False)
    row.anomaliesjson = _anomalies_to_json(report.anomalies)
    row.summaryjson = _summary_to_json(report.summary)
    row.llmmodel = report.usage.model_name if report.usage else None
    row.inputtokens = report.usage.input_tokens if report.usage else None
    row.outputtokens = report.usage.output_tokens if report.usage else None
    row.costusd = report.usage.cost_usd if report.usage else None
    row.generationms = report.generation_ms
    row.generatedat = report.generated_at
    row.deliveredat = report.delivered_at
    row.updatedat = report.updated_at
    row.updatedby = report.updated_by


class SqlReportRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sessions = session_factory

    def get(self, report_id: str) -> DailyReport | None:
        with self._sessions() as session:
            row = session.get(ReportRow, report_id)
            return _to_domain(row) if row else None

    def get_by_date(self, report_date: date) -> DailyReport | None:
        with self._sessions() as session:
            row = session.scalars(
                select(ReportRow).where(ReportRow.reportdate == report_date)
            ).first()
            return _to_domain(row) if row else None

    def add(self, report: DailyReport) -> DailyReport:
        with self._sessions() as session:
            row = ReportRow(id=report.id, createdat=report.created_at, createdby=report.created_by)
            _copy_to_row(report, row)
            session.add(row)
            session.commit()
            return _to_domain(row)

    def update(self, report: DailyReport) -> Result[DailyReport, DomainError]:
        with self._sessions() as session:
            row = session.get(ReportRow, report.id)
            if row is None:
                return Err(DomainError(ErrorCode.REPORT_NOT_FOUND, {"id": report.id}))
            if row.rowversion != report.version:
                return Err(
                    DomainError(
                        ErrorCode.REPORT_VERSION_CONFLICT,
                        {"expected": report.version, "current": row.rowversion},
                    )
                )
            _copy_to_row(report, row)
            try:
                session.commit()
            except StaleDataError:
                session.rollback()
                return Err(
                    DomainError(
                        ErrorCode.REPORT_VERSION_CONFLICT,
                        {"expected": report.version, "current": "?"},
                    )
                )
            return Ok(_to_domain(row))

    def history(self, before: date, days: int) -> list[DailyMetrics]:
        with self._sessions() as session:
            rows = session.scalars(
                select(ReportRow.metricsjson)
                .where(ReportRow.reportdate < before)
                .where(ReportRow.reportdate >= before - timedelta(days=days))
                .order_by(ReportRow.reportdate)
            ).all()
            return [DailyMetrics.from_dict(json.loads(raw)) for raw in rows]

    def list_recent(self, limit: int) -> list[DailyReport]:
        with self._sessions() as session:
            rows = session.scalars(
                select(ReportRow).order_by(ReportRow.reportdate.desc()).limit(limit)
            ).all()
            return [_to_domain(row) for row in rows]

    def stats(self) -> ReportStats:
        with self._sessions() as session:
            status_counts = dict(
                session.execute(
                    select(ReportRow.reportstatus, func.count()).group_by(ReportRow.reportstatus)
                ).all()
            )
            source_counts = dict(
                session.execute(
                    select(ReportRow.summarysource, func.count())
                    .where(ReportRow.summarysource.is_not(None))
                    .group_by(ReportRow.summarysource)
                ).all()
            )
            total_cost = session.scalar(select(func.sum(ReportRow.costusd)))
            # Duration in Python keeps the query portable across PostgreSQL, Oracle and SQL Server.
            durations = [
                (_aware(delivered) - _aware(generated)).total_seconds()  # type: ignore[operator]
                for generated, delivered in session.execute(
                    select(ReportRow.generatedat, ReportRow.deliveredat).where(
                        ReportRow.deliveredat.is_not(None)
                    )
                ).all()
            ]
            failures = session.scalar(select(func.count()).select_from(FailureRow)) or 0
        return ReportStats(
            generated=sum(status_counts.values()),
            delivered=status_counts.get(ReportStatus.DELIVERED.value, 0),
            ai_summaries=source_counts.get(SummarySource.AI.value, 0),
            fallback_summaries=source_counts.get(SummarySource.FALLBACK.value, 0),
            avg_pipeline_seconds=round(fmean(durations), 1) if durations else None,
            total_llm_cost_usd=Decimal(total_cost or 0).quantize(Decimal("0.000001")),
            failures=failures,
        )

    def add_failure(self, failure: PipelineFailure) -> None:
        with self._sessions() as session:
            session.add(
                FailureRow(
                    id=failure.id,
                    workflowname=failure.workflow_name,
                    nodename=failure.node_name,
                    failuremessage=failure.message,
                    executionid=failure.execution_id,
                    occurredat=failure.occurred_at,
                    createdat=failure.occurred_at,
                    createdby=failure.created_by,
                    updatedat=failure.occurred_at,
                    updatedby=failure.created_by,
                )
            )
            session.commit()
