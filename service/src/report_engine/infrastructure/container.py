"""Composition root: wires settings, adapters and use cases."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from report_engine.application.ports import Clock
from report_engine.application.use_cases import (
    GenerateReportUseCase,
    GetMetricsUseCase,
    GetReportUseCase,
    ListReportsUseCase,
    MarkDeliveredUseCase,
    RecordFailureUseCase,
    SummarizeReportUseCase,
)
from report_engine.infrastructure.claude_summary import ClaudeRequestBuilder, ClaudeSummaryParser
from report_engine.infrastructure.csv_log_source import CsvLogSource
from report_engine.infrastructure.persistence.database import (
    build_engine,
    build_session_factory,
)
from report_engine.infrastructure.persistence.repository import SqlReportRepository, utc_now
from report_engine.infrastructure.prompts import load_prompt
from report_engine.infrastructure.reporting import EmailRenderer, RuleBasedSummarizer
from report_engine.infrastructure.settings import Settings


@dataclass(slots=True)
class Container:
    settings: Settings
    repository: SqlReportRepository
    generate_report: GenerateReportUseCase
    summarize_report: SummarizeReportUseCase
    mark_delivered: MarkDeliveredUseCase
    get_report: GetReportUseCase
    list_reports: ListReportsUseCase
    get_metrics: GetMetricsUseCase
    record_failure: RecordFailureUseCase

    def ping_database(self) -> None:
        self.repository.list_recent(1)


def _new_id() -> str:
    return str(uuid.uuid4())


def build_container(settings: Settings, clock: Clock = utc_now) -> Container:
    repository = SqlReportRepository(build_session_factory(build_engine(settings.database_url)))
    builder = ClaudeRequestBuilder(
        prompt=load_prompt(settings.prompts_dir, settings.prompt_version),
        model=settings.claude_model,
        effort=settings.claude_effort,
        max_tokens=settings.claude_max_tokens,
        locale=settings.report_locale,
        product=settings.product_name,
    )
    renderer = EmailRenderer(settings.report_locale, settings.product_name)
    return Container(
        settings=settings,
        repository=repository,
        generate_report=GenerateReportUseCase(
            source=CsvLogSource(settings.logs_dir),
            repository=repository,
            request_builder=builder,
            policy=settings.alert_policy(),
            clock=clock,
            id_factory=_new_id,
            timezone=ZoneInfo(settings.report_timezone),
            history_days=settings.history_days,
            top_errors=settings.top_errors,
        ),
        summarize_report=SummarizeReportUseCase(
            repository=repository,
            parser=ClaudeSummaryParser(
                settings.price_input_per_mtok, settings.price_output_per_mtok
            ),
            fallback=RuleBasedSummarizer(settings.report_locale),
            renderer=renderer,
            clock=clock,
        ),
        mark_delivered=MarkDeliveredUseCase(repository, clock),
        get_report=GetReportUseCase(repository),
        list_reports=ListReportsUseCase(repository),
        get_metrics=GetMetricsUseCase(repository, settings.manual_report_minutes),
        record_failure=RecordFailureUseCase(repository, renderer, clock, _new_id),
    )
