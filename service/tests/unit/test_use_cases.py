from __future__ import annotations

import itertools
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from report_engine.application.use_cases import (
    GenerateReportUseCase,
    GetMetricsUseCase,
    GetReportUseCase,
    ListReportsUseCase,
    MarkDeliveredUseCase,
    RecordFailureUseCase,
    SummarizeReportUseCase,
)
from report_engine.domain.anomalies import AlertPolicy
from report_engine.domain.enums import AnomalyKind, ReportStatus, SummarySource
from report_engine.domain.errors import ErrorCode
from report_engine.domain.result import Err, Ok
from report_engine.infrastructure.claude_summary import ClaudeRequestBuilder, ClaudeSummaryParser
from report_engine.infrastructure.csv_log_source import CsvLogSource
from report_engine.infrastructure.persistence.repository import SqlReportRepository
from report_engine.infrastructure.prompts import load_prompt
from report_engine.infrastructure.reporting import EmailRenderer, RuleBasedSummarizer
from tests.conftest import NOW, REPORT_DAY, SERVICE_ROOT, csv_row, write_csv
from tests.unit.test_infrastructure import VALID, claude_message

_ids = itertools.count(1)


def _builder() -> ClaudeRequestBuilder:
    prompt = load_prompt(SERVICE_ROOT / "prompts" / "daily-report", "v1")
    return ClaudeRequestBuilder(prompt, "claude-opus-5-5", "low", 8000, "en", "Orbit")


def _generate(repository: SqlReportRepository, logs: Path) -> GenerateReportUseCase:
    return GenerateReportUseCase(
        source=CsvLogSource(logs),
        repository=repository,
        request_builder=_builder(),
        policy=AlertPolicy(),
        clock=lambda: NOW,
        id_factory=lambda: f"id-{next(_ids)}",
        timezone=ZoneInfo("America/Sao_Paulo"),
    )


def _write_day(logs: Path, day: date, errors: int = 0, ok: int = 200) -> None:
    rows = [csv_row(hour=h % 24) for h in range(ok)]
    rows += [csv_row(service="billing", status=500, message="Timeout") for _ in range(errors)]
    write_csv(logs / f"{day.isoformat()}.csv", rows)


def _summarize(repository: SqlReportRepository) -> SummarizeReportUseCase:
    return SummarizeReportUseCase(
        repository,
        ClaudeSummaryParser(Decimal("4"), Decimal("20")),
        RuleBasedSummarizer("en"),
        EmailRenderer("en", "Orbit"),
        lambda: NOW + timedelta(seconds=30),
    )


class TestGenerateReport:
    def test_defaults_to_yesterday_in_report_timezone(
        self, repository: SqlReportRepository, tmp_path: Path
    ) -> None:
        _write_day(tmp_path, REPORT_DAY)
        result = _generate(repository, tmp_path).execute(None, "n8n")
        assert isinstance(result, Ok)
        assert result.value.report.report_date == REPORT_DAY
        assert result.value.llm_request["model"] == "claude-opus-5-5"

    def test_uses_history_as_baseline(
        self, repository: SqlReportRepository, tmp_path: Path
    ) -> None:
        use_case = _generate(repository, tmp_path)
        for offset in range(3, 0, -1):
            day = REPORT_DAY - timedelta(days=offset)
            _write_day(tmp_path, day, errors=1, ok=2000)
            assert isinstance(use_case.execute(day, "n8n"), Ok)
        _write_day(tmp_path, REPORT_DAY, errors=20, ok=2000)
        result = use_case.execute(REPORT_DAY, "n8n")
        assert isinstance(result, Ok)
        kinds = {item.kind for item in result.value.report.anomalies}
        assert AnomalyKind.ERROR_RATE_SPIKE in kinds

    def test_business_errors(self, repository: SqlReportRepository, tmp_path: Path) -> None:
        use_case = _generate(repository, tmp_path)
        future = use_case.execute(date(2026, 11, 9), "n8n")
        assert isinstance(future, Err) and future.error.code is ErrorCode.REPORT_DATE_IN_FUTURE
        missing = use_case.execute(REPORT_DAY, "n8n")
        assert isinstance(missing, Err) and missing.error.code is ErrorCode.NO_LOG_DATA

    def test_rerun_regenerates_until_delivered(
        self, repository: SqlReportRepository, tmp_path: Path
    ) -> None:
        _write_day(tmp_path, REPORT_DAY)
        use_case = _generate(repository, tmp_path)
        first = use_case.execute(REPORT_DAY, "n8n")
        second = use_case.execute(REPORT_DAY, "n8n")
        assert isinstance(first, Ok) and isinstance(second, Ok)
        assert second.value.report.id == first.value.report.id
        assert second.value.report.version == first.value.report.version + 1

        report = second.value.report
        summarized = _summarize(repository).execute(report.id, report.version, None, "n8n")
        assert isinstance(summarized, Ok)
        MarkDeliveredUseCase(repository, lambda: NOW).execute(
            report.id, summarized.value.report.version, "n8n"
        )
        third = use_case.execute(REPORT_DAY, "n8n")
        assert isinstance(third, Err)
        assert third.error.code is ErrorCode.REPORT_ALREADY_DELIVERED


class TestSummarizeAndDeliver:
    def _report_id(self, repository: SqlReportRepository, tmp_path: Path) -> tuple[str, int]:
        _write_day(tmp_path, REPORT_DAY, errors=15)
        result = _generate(repository, tmp_path).execute(REPORT_DAY, "n8n")
        assert isinstance(result, Ok)
        return result.value.report.id, result.value.report.version

    def test_ai_summary_path(self, repository: SqlReportRepository, tmp_path: Path) -> None:
        report_id, version = self._report_id(repository, tmp_path)
        result = _summarize(repository).execute(report_id, version, claude_message(VALID), "n8n")
        assert isinstance(result, Ok)
        report = result.value.report
        assert report.summary_source is SummarySource.AI
        assert report.usage is not None and report.usage.cost_usd > 0
        assert "[EMAIL]" in result.value.rendered.html

    def test_fallback_when_llm_failed(
        self, repository: SqlReportRepository, tmp_path: Path
    ) -> None:
        report_id, version = self._report_id(repository, tmp_path)
        result = _summarize(repository).execute(report_id, version, None, "n8n")
        assert isinstance(result, Ok)
        assert result.value.report.summary_source is SummarySource.FALLBACK
        assert result.value.report.usage is None

    def test_errors(self, repository: SqlReportRepository, tmp_path: Path) -> None:
        report_id, version = self._report_id(repository, tmp_path)
        summarize = _summarize(repository)
        missing = summarize.execute("nope", 1, None, "n8n")
        assert isinstance(missing, Err) and missing.error.code is ErrorCode.REPORT_NOT_FOUND
        stale = summarize.execute(report_id, version + 5, None, "n8n")
        assert isinstance(stale, Err)
        assert stale.error.code is ErrorCode.REPORT_VERSION_CONFLICT

        deliver = MarkDeliveredUseCase(repository, lambda: NOW)
        assert isinstance(deliver.execute("nope", 1, "n8n"), Err)
        early = deliver.execute(report_id, version, "n8n")
        assert isinstance(early, Err) and early.error.code is ErrorCode.REPORT_NOT_SUMMARIZED

    def test_full_cycle_feeds_metrics(
        self, repository: SqlReportRepository, tmp_path: Path
    ) -> None:
        report_id, version = self._report_id(repository, tmp_path)
        summarized = _summarize(repository).execute(
            report_id, version, claude_message(VALID), "n8n"
        )
        assert isinstance(summarized, Ok)
        delivered = MarkDeliveredUseCase(repository, lambda: NOW + timedelta(seconds=45)).execute(
            report_id, summarized.value.report.version, "n8n"
        )
        assert isinstance(delivered, Ok)
        assert delivered.value.status is ReportStatus.DELIVERED
        assert delivered.value.pipeline_seconds == 45.0

        RecordFailureUseCase(
            repository, EmailRenderer("en", "Orbit"), lambda: NOW, lambda: "f-1"
        ).execute("Daily ops report", "Send e-mail", "SMTP down", "9", "n8n")
        metrics = GetMetricsUseCase(repository, manual_minutes_per_report=90).execute()
        assert metrics.stats.generated == 1
        assert metrics.stats.delivered == 1
        assert metrics.stats.ai_summaries == 1
        assert metrics.stats.failures == 1
        assert metrics.stats.avg_pipeline_seconds == 45.0
        assert metrics.stats.total_llm_cost_usd == Decimal("0.018000")
        assert metrics.estimated_hours_saved == 1.5
        assert metrics.success_rate == 0.5

        assert isinstance(GetReportUseCase(repository).execute(report_id), Ok)
        assert isinstance(GetReportUseCase(repository).execute("nope"), Err)
        assert len(ListReportsUseCase(repository).execute(10)) == 1


def test_metrics_without_activity(repository: SqlReportRepository) -> None:
    metrics = GetMetricsUseCase(repository, 90).execute()
    assert metrics.success_rate is None
    assert metrics.stats.avg_pipeline_seconds is None
    assert metrics.estimated_hours_saved == 0


def test_record_failure_validation(repository: SqlReportRepository) -> None:
    result = RecordFailureUseCase(
        repository, EmailRenderer("en", "Orbit"), lambda: NOW, lambda: "f-2"
    ).execute(" ", None, None, None, "n8n")
    assert isinstance(result, Err)
