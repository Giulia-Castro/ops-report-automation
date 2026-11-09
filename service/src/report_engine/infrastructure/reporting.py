"""Report text: rule-based fallback summary and the HTML/plain-text e-mail."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from report_engine.application.ports import RenderedAlert, RenderedReport
from report_engine.domain.anomalies import GLOBAL_SCOPE, Anomaly
from report_engine.domain.enums import AnomalyKind, HealthStatus, SummarySource
from report_engine.domain.report import DailyReport, ExecutiveSummary, PipelineFailure
from report_engine.infrastructure.i18n import catalog, fill

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
_RATE_KINDS = {AnomalyKind.ERROR_RATE_SLO, AnomalyKind.ERROR_RATE_SPIKE}
_LATENCY_KINDS = {AnomalyKind.LATENCY_SLO, AnomalyKind.LATENCY_SPIKE}
_HEALTH_COLORS = {
    HealthStatus.GREEN: "#1a7f37",
    HealthStatus.YELLOW: "#9a6700",
    HealthStatus.RED: "#cf222e",
}


class Formatter:
    def __init__(self, locale: str) -> None:
        self.locale = locale
        self.text = catalog(locale)

    def number(self, value: float) -> str:
        formatted = f"{value:,.0f}"
        return formatted.replace(",", ".") if self.locale == "pt-BR" else formatted

    def percent(self, value: float) -> str:
        formatted = f"{value * 100:.2f}%"
        return formatted.replace(".", ",") if self.locale == "pt-BR" else formatted

    def ms(self, value: float) -> str:
        return f"{self.number(value)} ms"

    def scope(self, scope: str) -> str:
        return self.text["report"]["allServices"] if scope == GLOBAL_SCOPE else scope

    def _value(self, kind: AnomalyKind, value: float) -> str:
        if kind in _RATE_KINDS:
            return self.percent(value)
        if kind in _LATENCY_KINDS:
            return self.ms(value)
        return self.number(value)

    def anomaly(self, item: Anomaly) -> str:
        return fill(
            self.text["anomalies"][item.kind.value],
            {
                "observed": self._value(item.kind, item.observed),
                "reference": self._value(item.kind, item.reference),
                "scope": self.scope(item.scope),
            },
        )

    def action(self, item: Anomaly) -> str:
        return fill(self.text["actions"][item.kind.value], {"scope": self.scope(item.scope)})


class RuleBasedSummarizer:
    """Used when the LLM is unavailable: same facts, plainer words. The report still ships."""

    def __init__(self, locale: str) -> None:
        self._fmt = Formatter(locale)

    def summarize(self, report: DailyReport) -> ExecutiveSummary:
        text = self._fmt.text["fallback"]
        metrics = report.metrics
        highlights = [
            fill(
                text["volume"],
                {
                    "requests": self._fmt.number(metrics.total_requests),
                    "errorRate": self._fmt.percent(metrics.error_rate),
                    "p95": self._fmt.ms(metrics.p95_latency_ms),
                },
            )
        ]
        if metrics.affected_users:
            highlights.append(fill(text["users"], {"users": metrics.affected_users}))
        if metrics.top_errors:
            top = metrics.top_errors[0]
            highlights.append(
                fill(
                    text["topError"],
                    {"signature": top.signature, "service": top.service, "count": top.count},
                )
            )
        actions = list(dict.fromkeys(self._fmt.action(item) for item in report.anomalies))
        return ExecutiveSummary(
            headline=fill(
                text["headline"][report.health.value],
                {"date": report.report_date.isoformat(), "count": len(report.anomalies)},
            ),
            highlights=tuple(highlights),
            risks=tuple(self._fmt.anomaly(item) for item in report.anomalies[:6])
            or (text["noRisks"],),
            actions=tuple(actions[:6]) or (text["noActions"],),
        )


class EmailRenderer:
    def __init__(self, locale: str, product: str, templates_dir: Path = _TEMPLATES_DIR) -> None:
        self._fmt = Formatter(locale)
        self._product = product
        self._env = Environment(
            loader=FileSystemLoader(templates_dir),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def _context(self, report: DailyReport) -> dict[str, Any]:
        fmt = self._fmt
        labels = fmt.text["report"]
        metrics = report.metrics
        usage = report.usage
        if report.summary_source is SummarySource.AI and usage is not None:
            source_note = fill(
                labels["footerAi"], {"model": usage.model_name, "cost": f"{usage.cost_usd:.4f}"}
            )
        else:
            source_note = labels["footerFallback"]
        return {
            "labels": labels,
            "lang": fmt.locale,
            "product": self._product,
            "date": report.report_date.isoformat(),
            "health_label": labels["health"][report.health.value],
            "health_color": _HEALTH_COLORS[report.health],
            "summary": report.summary,
            "kpis": [
                (labels["requests"], fmt.number(metrics.total_requests)),
                (labels["errorRate"], fmt.percent(metrics.error_rate)),
                (labels["p95"], fmt.ms(metrics.p95_latency_ms)),
                (labels["affectedUsers"], fmt.number(metrics.affected_users)),
            ],
            "anomalies": [
                (labels["level"][item.level.value], fmt.anomaly(item), item.level.value)
                for item in report.anomalies
            ],
            "services": [
                (
                    item.service,
                    fmt.number(item.requests),
                    fmt.percent(item.error_rate),
                    fmt.ms(item.p95_latency_ms),
                )
                for item in metrics.services
            ],
            "top_errors": [
                (item.service, item.signature, fmt.number(item.count))
                for item in metrics.top_errors
            ],
            "rejected_note": fill(labels["rejected"], {"count": metrics.rejected_lines})
            if metrics.rejected_lines
            else "",
            "source_note": source_note,
        }

    def _subject(self, report: DailyReport) -> str:
        return fill(
            self._fmt.text["report"]["subject"],
            {
                "health": self._fmt.text["report"]["health"][report.health.value].upper(),
                "product": self._product,
                "date": report.report_date.isoformat(),
            },
        )

    def _plain_text(self, context: dict[str, Any]) -> str:
        labels = context["labels"]
        summary: ExecutiveSummary | None = context["summary"]
        lines = [
            f"{context['product']} | {labels['title']} | {context['date']}",
            f"{context['health_label']}",
            "",
        ]
        if summary:
            lines += [summary.headline, ""]
            for title, items in (
                (labels["highlights"], summary.highlights),
                (labels["risks"], summary.risks),
                (labels["actions"], summary.actions),
            ):
                lines.append(f"{title}:")
                lines += [f"- {item}" for item in items]
                lines.append("")
        lines.append(f"{labels['kpis']}:")
        lines += [f"- {name}: {value}" for name, value in context["kpis"]]
        lines += ["", f"{labels['anomalies']}:"]
        lines += [f"- [{level}] {text}" for level, text, _ in context["anomalies"]] or [
            f"- {labels['noAnomalies']}"
        ]
        lines += ["", context["source_note"], labels["footer"]]
        return "\n".join(lines)

    def render(self, report: DailyReport) -> RenderedReport:
        context = self._context(report)
        html = self._env.get_template("report.html").render(**context)
        return RenderedReport(
            subject=self._subject(report), html=html, text=self._plain_text(context)
        )

    def render_failure(self, failure: PipelineFailure) -> RenderedAlert:
        text = self._fmt.text["alert"]
        unknown = text["unknown"]
        params = {
            "workflow": failure.workflow_name,
            "node": failure.node_name or unknown,
            "message": failure.message or unknown,
            "execution": failure.execution_id or unknown,
            "id": failure.id,
        }
        return RenderedAlert(subject=fill(text["subject"], params), text=fill(text["body"], params))
