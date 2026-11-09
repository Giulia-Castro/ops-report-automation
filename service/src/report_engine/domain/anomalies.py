"""Deterministic anomaly detection. The code decides the health status; the LLM only writes."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean

from report_engine.domain.enums import AlertLevel, AnomalyKind, HealthStatus
from report_engine.domain.metrics import DailyMetrics

GLOBAL_SCOPE = "ALL"


@dataclass(frozen=True, slots=True)
class AlertPolicy:
    error_rate_warning: float = 0.01
    error_rate_critical: float = 0.05
    p95_warning_ms: int = 800
    p95_critical_ms: int = 2000
    spike_factor: float = 2.0
    min_error_rate_delta: float = 0.005
    min_latency_delta_ms: int = 200
    volume_drop_ratio: float = 0.5
    # Small samples are noise: 1 error in 60 requests is 1.7% but says nothing.
    min_requests: int = 50
    min_errors: int = 5


@dataclass(frozen=True, slots=True)
class Reference:
    error_rate: float
    p95_latency_ms: float
    requests: float


@dataclass(frozen=True, slots=True)
class Baseline:
    days: int
    overall: Reference
    services: dict[str, Reference]

    @classmethod
    def from_history(cls, history: list[DailyMetrics]) -> Baseline | None:
        if not history:
            return None
        names = {item.service for day in history for item in day.services}
        services: dict[str, Reference] = {}
        for name in names:
            rows = [row for day in history if (row := day.service(name)) is not None]
            services[name] = Reference(
                error_rate=fmean(row.error_rate for row in rows),
                p95_latency_ms=fmean(row.p95_latency_ms for row in rows),
                requests=fmean(row.requests for row in rows),
            )
        overall = Reference(
            error_rate=fmean(day.error_rate for day in history),
            p95_latency_ms=fmean(day.p95_latency_ms for day in history),
            requests=fmean(day.total_requests for day in history),
        )
        return cls(days=len(history), overall=overall, services=services)


@dataclass(frozen=True, slots=True)
class Anomaly:
    kind: AnomalyKind
    level: AlertLevel
    scope: str
    observed: float
    reference: float


def _slo_level(value: float, warning: float, critical: float) -> AlertLevel | None:
    if value >= critical:
        return AlertLevel.CRITICAL
    if value >= warning:
        return AlertLevel.WARNING
    return None


def _check_scope(
    scope: str,
    requests: int,
    errors: int,
    error_rate: float,
    p95: int,
    reference: Reference | None,
    policy: AlertPolicy,
) -> list[Anomaly]:
    if requests < policy.min_requests:
        return []
    found: list[Anomaly] = []

    enough_errors = errors >= policy.min_errors
    error_level = _slo_level(error_rate, policy.error_rate_warning, policy.error_rate_critical)
    if error_level and enough_errors:
        found.append(
            Anomaly(
                AnomalyKind.ERROR_RATE_SLO,
                error_level,
                scope,
                error_rate,
                policy.error_rate_warning,
            )
        )
    elif (
        reference
        and enough_errors
        and not error_level
        and error_rate >= reference.error_rate * policy.spike_factor
        and error_rate - reference.error_rate >= policy.min_error_rate_delta
    ):
        found.append(
            Anomaly(
                AnomalyKind.ERROR_RATE_SPIKE,
                AlertLevel.WARNING,
                scope,
                error_rate,
                round(reference.error_rate, 4),
            )
        )

    latency_level = _slo_level(p95, policy.p95_warning_ms, policy.p95_critical_ms)
    if latency_level:
        found.append(
            Anomaly(AnomalyKind.LATENCY_SLO, latency_level, scope, p95, policy.p95_warning_ms)
        )
    elif (
        reference
        and p95 >= reference.p95_latency_ms * policy.spike_factor
        and p95 - reference.p95_latency_ms >= policy.min_latency_delta_ms
    ):
        found.append(
            Anomaly(
                AnomalyKind.LATENCY_SPIKE,
                AlertLevel.WARNING,
                scope,
                p95,
                round(reference.p95_latency_ms),
            )
        )
    return found


def detect_anomalies(
    metrics: DailyMetrics, baseline: Baseline | None, policy: AlertPolicy
) -> tuple[Anomaly, ...]:
    found = _check_scope(
        GLOBAL_SCOPE,
        metrics.total_requests,
        metrics.server_errors,
        metrics.error_rate,
        metrics.p95_latency_ms,
        baseline.overall if baseline else None,
        policy,
    )
    for service in metrics.services:
        found += _check_scope(
            service.service,
            service.requests,
            service.server_errors,
            service.error_rate,
            service.p95_latency_ms,
            baseline.services.get(service.service) if baseline else None,
            policy,
        )
    if baseline and metrics.total_requests < baseline.overall.requests * policy.volume_drop_ratio:
        found.append(
            Anomaly(
                AnomalyKind.VOLUME_DROP,
                AlertLevel.WARNING,
                GLOBAL_SCOPE,
                metrics.total_requests,
                round(baseline.overall.requests),
            )
        )
    return tuple(
        sorted(found, key=lambda item: (-item.level.rank, item.scope != GLOBAL_SCOPE, item.scope))
    )


def health_status(anomalies: tuple[Anomaly, ...]) -> HealthStatus:
    if any(item.level is AlertLevel.CRITICAL for item in anomalies):
        return HealthStatus.RED
    if anomalies:
        return HealthStatus.YELLOW
    return HealthStatus.GREEN
