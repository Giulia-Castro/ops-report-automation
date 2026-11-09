from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from report_engine.domain.enums import LogLevel
from report_engine.domain.logs import LogBatch, LogEvent
from report_engine.infrastructure.persistence.database import build_engine, build_session_factory
from report_engine.infrastructure.persistence.orm import Base
from report_engine.infrastructure.persistence.repository import SqlReportRepository

SERVICE_ROOT = Path(__file__).resolve().parent.parent
PROJECT_ROOT = SERVICE_ROOT.parent
NOW = datetime(2026, 11, 8, 10, 0, tzinfo=UTC)
REPORT_DAY = date(2026, 11, 7)
API_KEY = "test-key-0123456789abcdef"

CSV_COLUMNS = [
    "timestamp",
    "service",
    "level",
    "endpoint",
    "status_code",
    "latency_ms",
    "user_email",
    "message",
]


def event(
    service: str = "orders",
    status: int = 200,
    latency: int = 100,
    message: str = "OK",
    user: str | None = "u1",
    level: LogLevel | None = None,
) -> LogEvent:
    return LogEvent(
        timestamp=NOW,
        service=service,
        level=level or (LogLevel.ERROR if status >= 500 else LogLevel.INFO),
        endpoint="/v1/orders",
        status_code=status,
        latency_ms=latency,
        message=message,
        user_ref=user,
    )


def batch(ok: int = 100, errors: int = 0, service: str = "orders", latency: int = 100) -> LogBatch:
    events = [event(service, 200, latency, user=f"u{i}") for i in range(ok)]
    events += [
        event(service, 500, latency, f"Timeout on order {i}", user=f"e{i}") for i in range(errors)
    ]
    return LogBatch(events=tuple(events))


def write_csv(path: Path, rows: list[dict[str, object]], extra_lines: str = "") -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
        handle.write(extra_lines)


def csv_row(
    service: str = "orders",
    status: int = 200,
    latency: int = 100,
    message: str = "OK",
    email: str = "user1@example.com",
    hour: int = 12,
) -> dict[str, object]:
    return {
        "timestamp": (datetime(2026, 11, 7, tzinfo=UTC) + timedelta(hours=hour)).isoformat(),
        "service": service,
        "level": "ERROR" if status >= 500 else "INFO",
        "endpoint": "/v1/orders",
        "status_code": status,
        "latency_ms": latency,
        "user_email": email,
        "message": message,
    }


@pytest.fixture
def session_factory(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    engine = build_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    yield build_session_factory(engine)
    engine.dispose()


@pytest.fixture
def repository(session_factory: sessionmaker[Session]) -> SqlReportRepository:
    return SqlReportRepository(session_factory)
