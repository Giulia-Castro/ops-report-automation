from __future__ import annotations

from datetime import timedelta

import pytest

from report_engine.domain.anomalies import (
    GLOBAL_SCOPE,
    AlertPolicy,
    Baseline,
    detect_anomalies,
    health_status,
)
from report_engine.domain.enums import (
    AlertLevel,
    AnomalyKind,
    HealthStatus,
    LogLevel,
    ReportStatus,
    SummarySource,
)
from report_engine.domain.errors import ErrorCode
from report_engine.domain.logs import LogBatch
from report_engine.domain.masking import mask_personal_data
from report_engine.domain.metrics import (
    DailyMetrics,
    compute_metrics,
    error_signature,
    percentile,
)
from report_engine.domain.report import DailyReport, ExecutiveSummary, PipelineFailure
from report_engine.domain.result import Err, Ok
from tests.conftest import NOW, REPORT_DAY, batch, event

POLICY = AlertPolicy()
SUMMARY = ExecutiveSummary("headline", ("h",), ("r",), ("a",))


class TestMetrics:
    def test_percentile_nearest_rank(self) -> None:
        assert percentile([], 95) == 0
        assert percentile([5], 95) == 5
        assert percentile(list(range(1, 101)), 95) == 95
        assert percentile(list(range(1, 101)), 50) == 50

    def test_error_signature_groups_ids_and_numbers(self) -> None:
        a = error_signature("Order 1234 failed for 550e8400-e29b-41d4-a716-446655440000")
        b = error_signature("Order 98 failed for 6ba7b810-9dad-11d1-80b4-00c04fd430c8")
        assert a == b == "Order # failed for <id>"
        assert error_signature("kid a1b2c3d4e5f6a7b8   end") == "kid <id> end"

    def test_compute_metrics_counts_and_rates(self) -> None:
        events = batch(ok=95, errors=5).events + (event(status=404),)
        metrics = compute_metrics(LogBatch(events=events, rejected_lines=2), top_errors=3)
        assert metrics.total_requests == 101
        assert metrics.server_errors == 5
        assert metrics.client_errors == 1
        assert metrics.error_rate == round(5 / 101, 4)
        assert metrics.affected_users == 5
        assert metrics.rejected_lines == 2
        assert metrics.top_errors[0].signature == "Timeout on order #"
        assert metrics.top_errors[0].count == 5
        assert metrics.service("orders") is not None
        assert metrics.service("missing") is None

    def test_error_level_counts_as_server_error(self) -> None:
        metrics = compute_metrics(LogBatch(events=(event(status=200, level=LogLevel.ERROR),)))
        assert metrics.server_errors == 1

    def test_round_trip_dict(self) -> None:
        metrics = compute_metrics(batch(ok=10, errors=1))
        assert DailyMetrics.from_dict(metrics.to_dict()) == metrics

    def test_empty_batch(self) -> None:
        metrics = compute_metrics(LogBatch(events=()))
        assert metrics.total_requests == 0
        assert metrics.error_rate == 0.0


class TestAnomalies:
    def test_healthy_day_has_no_anomalies(self) -> None:
        metrics = compute_metrics(batch(ok=1000, errors=2))
        assert detect_anomalies(metrics, None, POLICY) == ()

    def test_error_rate_slo_critical_and_warning(self) -> None:
        critical = detect_anomalies(compute_metrics(batch(ok=90, errors=10)), None, POLICY)
        assert {(a.kind, a.level, a.scope) for a in critical} == {
            (AnomalyKind.ERROR_RATE_SLO, AlertLevel.CRITICAL, GLOBAL_SCOPE),
            (AnomalyKind.ERROR_RATE_SLO, AlertLevel.CRITICAL, "orders"),
        }
        warning = detect_anomalies(compute_metrics(batch(ok=480, errors=10)), None, POLICY)
        assert all(a.level is AlertLevel.WARNING for a in warning)

    def test_few_errors_or_small_sample_are_ignored(self) -> None:
        assert detect_anomalies(compute_metrics(batch(ok=60, errors=3)), None, POLICY) == ()
        assert detect_anomalies(compute_metrics(batch(ok=20, errors=10)), None, POLICY) == ()

    def test_latency_slo(self) -> None:
        found = detect_anomalies(compute_metrics(batch(ok=100, latency=2500)), None, POLICY)
        assert {a.kind for a in found} == {AnomalyKind.LATENCY_SLO}
        assert all(a.level is AlertLevel.CRITICAL for a in found)

    def test_spikes_against_baseline(self) -> None:
        history = [compute_metrics(batch(ok=2000, errors=6, latency=100)) for _ in range(3)]
        baseline = Baseline.from_history(history)
        assert baseline is not None and baseline.days == 3
        today = compute_metrics(batch(ok=2000, errors=18, latency=400))
        kinds = {a.kind for a in detect_anomalies(today, baseline, POLICY)}
        assert kinds == {AnomalyKind.ERROR_RATE_SPIKE, AnomalyKind.LATENCY_SPIKE}

    def test_volume_drop(self) -> None:
        baseline = Baseline.from_history([compute_metrics(batch(ok=1000))])
        found = detect_anomalies(compute_metrics(batch(ok=300)), baseline, POLICY)
        assert [a.kind for a in found] == [AnomalyKind.VOLUME_DROP]

    def test_baseline_from_empty_history(self) -> None:
        assert Baseline.from_history([]) is None

    def test_sorted_critical_first_and_health(self) -> None:
        metrics = compute_metrics(batch(ok=90, errors=10, latency=900))
        found = detect_anomalies(metrics, None, POLICY)
        assert found[0].level is AlertLevel.CRITICAL
        assert found[-1].level is AlertLevel.WARNING
        assert health_status(found) is HealthStatus.RED
        assert health_status(found[-1:]) is HealthStatus.YELLOW
        assert health_status(()) is HealthStatus.GREEN


def _report() -> DailyReport:
    metrics = compute_metrics(batch(ok=100))
    return DailyReport.generate("r1", REPORT_DAY, metrics, (), 12, "n8n", NOW)


class TestDailyReport:
    def test_lifecycle(self) -> None:
        report = _report()
        assert report.status is ReportStatus.GENERATED
        assert report.pipeline_seconds is None
        assert isinstance(report.attach_summary(SUMMARY, SummarySource.AI, None, 1, "n8n", NOW), Ok)
        assert report.status is ReportStatus.SUMMARIZED
        later = NOW + timedelta(seconds=42)
        assert isinstance(report.mark_delivered(1, "n8n", later), Ok)
        assert report.status is ReportStatus.DELIVERED
        assert report.pipeline_seconds == 42.0
        assert report.updated_at == later

    def test_version_conflicts(self) -> None:
        report = _report()
        result = report.attach_summary(SUMMARY, SummarySource.AI, None, 7, "n8n", NOW)
        assert isinstance(result, Err)
        assert result.error.code is ErrorCode.REPORT_VERSION_CONFLICT
        report.attach_summary(SUMMARY, SummarySource.AI, None, 1, "n8n", NOW)
        conflict = report.mark_delivered(2, "n8n", NOW)
        assert isinstance(conflict, Err)
        assert conflict.error.code is ErrorCode.REPORT_VERSION_CONFLICT

    def test_cannot_deliver_without_summary(self) -> None:
        result = _report().mark_delivered(1, "n8n", NOW)
        assert isinstance(result, Err)
        assert result.error.code is ErrorCode.REPORT_NOT_SUMMARIZED

    def test_delivered_report_is_final(self) -> None:
        report = _report()
        report.attach_summary(SUMMARY, SummarySource.FALLBACK, None, 1, "n8n", NOW)
        report.mark_delivered(1, "n8n", NOW)
        for result in (
            report.mark_delivered(1, "n8n", NOW),
            report.attach_summary(SUMMARY, SummarySource.AI, None, 1, "n8n", NOW),
            report.regenerate(report.metrics, (), 1, "n8n", NOW),
        ):
            assert isinstance(result, Err)
            assert result.error.code is ErrorCode.REPORT_ALREADY_DELIVERED

    def test_regenerate_clears_summary(self) -> None:
        report = _report()
        report.attach_summary(SUMMARY, SummarySource.AI, None, 1, "n8n", NOW)
        later = NOW + timedelta(hours=1)
        assert isinstance(report.regenerate(report.metrics, (), 5, "ops", later), Ok)
        assert report.status is ReportStatus.GENERATED
        assert report.summary is None
        assert report.generated_at == later
        assert report.created_at == NOW


class TestPipelineFailure:
    def test_masks_and_truncates(self) -> None:
        result = PipelineFailure.record(
            "f1",
            "  Daily report ",
            "Send",
            "Failed for ana@example.com " + "x" * 600,
            "77",
            "n8n",
            NOW,
        )
        assert isinstance(result, Ok)
        failure = result.value
        assert failure.workflow_name == "Daily report"
        assert failure.message is not None and "[EMAIL]" in failure.message
        assert len(failure.message) == 500
        assert failure.masked_values == 1

    def test_requires_workflow_name(self) -> None:
        result = PipelineFailure.record("f1", "   ", None, None, None, "n8n", NOW)
        assert isinstance(result, Err)
        assert result.error.code is ErrorCode.FAILURE_WORKFLOW_REQUIRED


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("contato joao@example.com", "EMAIL"),
        ("CPF 123.456.789-09", "CPF"),
        ("CNPJ 12.345.678/0001-95", "CNPJ"),
        ("cartão 4111 1111 1111 1111", "CARD"),
        ("tel +55 11 98765-4321", "PHONE"),
        ("placa ABC1D23", "PLATE"),
    ],
)
def test_masking(text: str, label: str) -> None:
    result = mask_personal_data(text)
    assert f"[{label}]" in result.text
    assert result.total == 1
