"""Daily metrics computed from a batch of log events. Pure and deterministic."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from report_engine.domain.logs import LogBatch, LogEvent

_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_HEX_ID = re.compile(r"\b[0-9a-fA-F]{12,}\b")
_NUMBER = re.compile(r"\d+")
_SPACES = re.compile(r"\s+")
_SIGNATURE_MAX = 120


@dataclass(frozen=True, slots=True)
class ServiceMetrics:
    service: str
    requests: int
    server_errors: int
    error_rate: float
    p95_latency_ms: int


@dataclass(frozen=True, slots=True)
class ErrorSignature:
    service: str
    signature: str
    count: int


@dataclass(frozen=True, slots=True)
class DailyMetrics:
    total_requests: int
    server_errors: int
    client_errors: int
    error_rate: float
    p50_latency_ms: int
    p95_latency_ms: int
    affected_users: int
    rejected_lines: int
    services: tuple[ServiceMetrics, ...]
    top_errors: tuple[ErrorSignature, ...]

    def service(self, name: str) -> ServiceMetrics | None:
        return next((item for item in self.services if item.service == name), None)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DailyMetrics:
        return cls(
            **{key: data[key] for key in data if key not in ("services", "top_errors")},
            services=tuple(ServiceMetrics(**item) for item in data["services"]),
            top_errors=tuple(ErrorSignature(**item) for item in data["top_errors"]),
        )


def percentile(values: Sequence[int], pct: float) -> int:
    """Nearest-rank percentile. Returns 0 for an empty sequence."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def error_signature(message: str) -> str:
    """Group messages that differ only by ids and numbers."""
    text = _UUID.sub("<id>", message)
    text = _HEX_ID.sub("<id>", text)
    text = _NUMBER.sub("#", text)
    return _SPACES.sub(" ", text).strip()[:_SIGNATURE_MAX]


def _rate(part: int, total: int) -> float:
    return round(part / total, 4) if total else 0.0


def _service_metrics(name: str, events: list[LogEvent]) -> ServiceMetrics:
    errors = sum(1 for event in events if event.is_server_error)
    return ServiceMetrics(
        service=name,
        requests=len(events),
        server_errors=errors,
        error_rate=_rate(errors, len(events)),
        p95_latency_ms=percentile([event.latency_ms for event in events], 95),
    )


def compute_metrics(batch: LogBatch, top_errors: int = 5) -> DailyMetrics:
    events = batch.events
    by_service: dict[str, list[LogEvent]] = defaultdict(list)
    for event in events:
        by_service[event.service].append(event)

    server_errors = [event for event in events if event.is_server_error]
    signatures: Counter[tuple[str, str]] = Counter(
        (event.service, error_signature(event.message)) for event in server_errors
    )
    latencies = [event.latency_ms for event in events]

    return DailyMetrics(
        total_requests=len(events),
        server_errors=len(server_errors),
        client_errors=sum(1 for event in events if event.is_client_error),
        error_rate=_rate(len(server_errors), len(events)),
        p50_latency_ms=percentile(latencies, 50),
        p95_latency_ms=percentile(latencies, 95),
        affected_users=len({event.user_ref for event in server_errors if event.user_ref}),
        rejected_lines=batch.rejected_lines,
        services=tuple(_service_metrics(name, items) for name, items in sorted(by_service.items())),
        top_errors=tuple(
            ErrorSignature(service=service, signature=signature, count=count)
            for (service, signature), count in sorted(
                signatures.items(), key=lambda item: (-item[1], item[0])
            )[:top_errors]
        ),
    )
