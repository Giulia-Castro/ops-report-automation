"""Claude Messages API: request body built here, sent by n8n, response validated here.

The service never holds the Anthropic key. n8n stores it as an encrypted credential and
forwards the body as-is, so prompt and schema stay versioned and tested in this repository.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from report_engine.application.ports import ParsedSummary
from report_engine.domain.masking import mask_personal_data
from report_engine.domain.report import DailyReport, ExecutiveSummary, LlmUsage
from report_engine.domain.result import Err, Ok, Result
from report_engine.infrastructure.prompts import Prompt

ANTHROPIC_VERSION = "2026-10-21"
_LANGUAGE_NAMES = {"en": "English", "pt-BR": "Brazilian Portuguese"}
_MILLION = Decimal(1_000_000)

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "highlights", "risks", "actions"],
    "properties": {
        "headline": {"type": "string", "description": "One sentence, at most 160 characters."},
        "highlights": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "array", "items": {"type": "string"}},
    },
}


def _facts(report: DailyReport, product: str) -> dict[str, Any]:
    metrics = report.metrics
    return {
        "product": product,
        "reportDate": report.report_date.isoformat(),
        "health": report.health.value,
        "totals": {
            "requests": metrics.total_requests,
            "serverErrors": metrics.server_errors,
            "clientErrors": metrics.client_errors,
            "errorRate": metrics.error_rate,
            "p50LatencyMs": metrics.p50_latency_ms,
            "p95LatencyMs": metrics.p95_latency_ms,
            "affectedUsers": metrics.affected_users,
        },
        "anomalies": [
            {
                "kind": item.kind.value,
                "level": item.level.value,
                "scope": item.scope,
                "observed": item.observed,
                "reference": item.reference,
            }
            for item in report.anomalies
        ],
        "services": [
            {
                "service": item.service,
                "requests": item.requests,
                "errorRate": item.error_rate,
                "p95LatencyMs": item.p95_latency_ms,
            }
            for item in metrics.services
        ],
        "topErrors": [
            {"service": item.service, "signature": item.signature, "count": item.count}
            for item in metrics.top_errors
        ],
    }


def render_facts(report: DailyReport, product: str) -> str:
    """JSON with '<' escaped, so log text can never close the <facts> tag."""
    return json.dumps(_facts(report, product), ensure_ascii=False, indent=1).replace("<", "\\u003c")


class ClaudeRequestBuilder:
    def __init__(
        self,
        prompt: Prompt,
        model: str,
        effort: str,
        max_tokens: int,
        locale: str,
        product: str,
    ) -> None:
        self._prompt = prompt
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens
        self._locale = locale
        self._product = product

    @property
    def prompt_version(self) -> str:
        return self._prompt.version

    def build(self, report: DailyReport) -> dict[str, Any]:
        system = self._prompt.text.replace("{product}", self._product).replace(
            "{language}", _LANGUAGE_NAMES.get(self._locale, "English")
        )
        user = (
            f"<facts>\n{render_facts(report, self._product)}\n</facts>\n\n"
            f"Write the executive summary for {report.report_date.isoformat()}."
        )
        return {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {
                "effort": self._effort,
                "format": {"type": "json_schema", "schema": SUMMARY_SCHEMA},
            },
        }


_Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class _SummaryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    highlights: list[_Line] = Field(max_length=6)
    risks: list[_Line] = Field(max_length=6)
    actions: list[_Line] = Field(max_length=6)


def _masked(lines: list[str]) -> tuple[str, ...]:
    return tuple(mask_personal_data(line).text for line in lines)


class ClaudeSummaryParser:
    def __init__(self, price_input_per_mtok: Decimal, price_output_per_mtok: Decimal) -> None:
        self._price_in = price_input_per_mtok
        self._price_out = price_output_per_mtok

    def parse(self, raw: Mapping[str, Any] | None) -> Result[ParsedSummary, str]:
        if not raw:
            return Err("llm_call_failed")
        if raw.get("type") != "message":
            return Err("unexpected_payload")
        stop_reason = raw.get("stop_reason")
        if stop_reason != "end_turn":
            return Err(f"stop_reason_{stop_reason}")
        content = raw.get("content")
        if not isinstance(content, list):
            return Err("unexpected_payload")
        text = "".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, Mapping) and block.get("type") == "text"
        )
        if not text.strip():
            return Err("empty_text")
        try:
            payload = _SummaryPayload.model_validate(json.loads(text))
        except json.JSONDecodeError:
            return Err("invalid_json")
        except ValidationError:
            return Err("schema_violation")

        usage = raw.get("usage") if isinstance(raw.get("usage"), Mapping) else {}
        input_tokens = int(usage.get("input_tokens") or 0)  # type: ignore[union-attr]
        output_tokens = int(usage.get("output_tokens") or 0)  # type: ignore[union-attr]
        cost = (
            Decimal(input_tokens) * self._price_in + Decimal(output_tokens) * self._price_out
        ) / _MILLION
        return Ok(
            ParsedSummary(
                summary=ExecutiveSummary(
                    headline=mask_personal_data(payload.headline).text,
                    highlights=_masked(payload.highlights),
                    risks=_masked(payload.risks),
                    actions=_masked(payload.actions),
                ),
                usage=LlmUsage(
                    model_name=str(raw.get("model") or "unknown")[:60],
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cost_usd=cost.quantize(Decimal("0.000001")),
                ),
            )
        )
