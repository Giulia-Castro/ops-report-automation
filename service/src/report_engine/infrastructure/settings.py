"""Configuration from environment variables. Secrets never have defaults in code."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from report_engine.domain.anomalies import AlertPolicy


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"

    database_url: str = "sqlite:///./reports.db"

    # "label:key" pairs, comma separated. The label becomes the audit actor (CREATEDBY).
    api_keys: SecretStr = SecretStr("")

    logs_dir: Path = Path("../data/logs")
    report_timezone: str = "America/Sao_Paulo"
    report_locale: Literal["en", "pt-BR"] = "pt-BR"
    product_name: str = "Orbit"
    history_days: int = Field(default=7, ge=1, le=30)
    top_errors: int = Field(default=5, ge=1, le=20)

    # Distribution lists. Kept in configuration, never in the workflow or the repository.
    report_sender: str = "ops-reports@example.com"
    report_recipients: str = "ops-team@example.com"
    alert_recipients: str = "ops-oncall@example.com"

    # Alert thresholds (SLOs).
    error_rate_warning: float = Field(default=0.01, gt=0, lt=1)
    error_rate_critical: float = Field(default=0.05, gt=0, lt=1)
    p95_warning_ms: int = Field(default=800, gt=0)
    p95_critical_ms: int = Field(default=2000, gt=0)

    # Claude request built by the service and sent by n8n with its own credential.
    prompts_dir: Path = Path("prompts/daily-report")
    prompt_version: str = "v1"
    claude_model: str = "claude-opus-5-5"
    claude_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    claude_max_tokens: int = Field(default=8000, ge=1024, le=32000)
    # USD per million tokens, used to report cost per report. Check current pricing.
    price_input_per_mtok: Decimal = Decimal("4.00")
    price_output_per_mtok: Decimal = Decimal("20.00")

    # Hypothesis used for "hours saved". Measure your own manual report time and set it here.
    manual_report_minutes: int = Field(default=90, ge=0, le=1440)

    def parsed_api_keys(self) -> dict[str, str]:
        """Return {key: label}. Malformed entries are ignored (deny by default)."""
        keys: dict[str, str] = {}
        for entry in self.api_keys.get_secret_value().split(","):
            label, sep, key = entry.strip().partition(":")
            if sep and label and len(key) >= 16:
                keys[key] = label
        return keys

    def alert_policy(self) -> AlertPolicy:
        return AlertPolicy(
            error_rate_warning=self.error_rate_warning,
            error_rate_critical=self.error_rate_critical,
            p95_warning_ms=self.p95_warning_ms,
            p95_critical_ms=self.p95_critical_ms,
        )

    @staticmethod
    def split_addresses(value: str) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
