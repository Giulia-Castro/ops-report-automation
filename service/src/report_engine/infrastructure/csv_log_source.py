"""Reads one CSV file per day from a drop folder (local disk, SFTP mount or bucket sync)."""

from __future__ import annotations

import csv
import hashlib
import logging
from datetime import UTC, date, datetime
from pathlib import Path

from report_engine.domain.enums import LogLevel
from report_engine.domain.logs import LogBatch, LogEvent
from report_engine.domain.masking import mask_personal_data

logger = logging.getLogger("report_engine.log_source")

REQUIRED_COLUMNS = frozenset(
    ["timestamp", "service", "level", "endpoint", "status_code", "latency_ms", "message"]
)
_MAX_FIELD = 2000


def _pseudonymize(value: str) -> str | None:
    value = value.strip().lower()
    if not value:
        return None
    return hashlib.blake2b(value.encode(), digest_size=8).hexdigest()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.strip())
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class CsvLogSource:
    """File name is derived from the date only, so callers cannot choose arbitrary paths."""

    def __init__(self, logs_dir: Path) -> None:
        self._dir = logs_dir

    def path_for(self, day: date) -> Path:
        return self._dir / f"{day.isoformat()}.csv"

    def read(self, day: date) -> LogBatch | None:
        path = self.path_for(day)
        if not path.is_file():
            return None
        events: list[LogEvent] = []
        rejected = 0
        masked_values = 0
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
            if missing:
                logger.warning(
                    "log_source.invalid_header",
                    extra={"context": {"file": path.name, "missing": sorted(missing)}},
                )
                return LogBatch(events=(), rejected_lines=0)
            for row in reader:
                try:
                    masked = mask_personal_data((row["message"] or "")[:_MAX_FIELD])
                    events.append(
                        LogEvent(
                            timestamp=_parse_timestamp(row["timestamp"]),
                            service=row["service"].strip()[:60],
                            level=LogLevel(row["level"].strip().upper()),
                            endpoint=mask_personal_data(row["endpoint"].strip()[:200]).text,
                            status_code=int(row["status_code"]),
                            latency_ms=max(0, int(row["latency_ms"])),
                            message=masked.text,
                            user_ref=_pseudonymize(row.get("user_email") or ""),
                        )
                    )
                    masked_values += masked.total
                except (ValueError, KeyError, AttributeError, TypeError):
                    rejected += 1
        return LogBatch(events=tuple(events), rejected_lines=rejected, masked_values=masked_values)
