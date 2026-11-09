"""HTTP routes. Thin: validate input, call a use case, map Result to HTTP."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from report_engine.api.dependencies import ActorDep, ContainerDep
from report_engine.api.problems import domain_problem
from report_engine.api.schemas import (
    CreatedReportResponse,
    CreateReportRequest,
    DeliveryRequest,
    EmailDto,
    FailureRequest,
    FailureResponse,
    MetricsResponse,
    ReportResponse,
    SummarizedReportResponse,
    SummaryRequest,
)
from report_engine.domain.result import Err

router = APIRouter(prefix="/v1", tags=["reports"])
health_router = APIRouter(prefix="/health", tags=["health"])

_PROBLEM = {"model": None, "content": {"application/problem+json": {}}}


@router.post(
    "/reports",
    status_code=201,
    response_model=CreatedReportResponse,
    responses={409: _PROBLEM, 422: _PROBLEM},
)
def create_report(
    request: Request,
    body: CreateReportRequest,
    container: ContainerDep,
    actor: ActorDep,
) -> CreatedReportResponse | JSONResponse:
    """Compute metrics and anomalies for a day (default: yesterday) and build the LLM request."""
    result = container.generate_report.execute(body.report_date, actor)
    if isinstance(result, Err):
        return domain_problem(request, result.error)
    base = ReportResponse.of(result.value.report)
    return CreatedReportResponse(**base.model_dump(), llm_request=result.value.llm_request)


@router.post(
    "/reports/{report_id}/summary",
    response_model=SummarizedReportResponse,
    responses={404: _PROBLEM, 409: _PROBLEM},
)
def summarize_report(
    request: Request,
    report_id: str,
    body: SummaryRequest,
    container: ContainerDep,
    actor: ActorDep,
) -> SummarizedReportResponse | JSONResponse:
    """Attach Claude's summary (or the rule-based fallback) and render the e-mail."""
    result = container.summarize_report.execute(report_id, body.version, body.llm_response, actor)
    if isinstance(result, Err):
        return domain_problem(request, result.error)
    settings = container.settings
    rendered = result.value.rendered
    return SummarizedReportResponse(
        report=ReportResponse.of(result.value.report),
        email=EmailDto(
            sender=settings.report_sender,
            recipients=settings.split_addresses(settings.report_recipients),
            subject=rendered.subject,
            html=rendered.html,
            text=rendered.text,
        ),
    )


@router.post(
    "/reports/{report_id}/delivery",
    response_model=ReportResponse,
    responses={404: _PROBLEM, 409: _PROBLEM},
)
def mark_delivered(
    request: Request,
    report_id: str,
    body: DeliveryRequest,
    container: ContainerDep,
    actor: ActorDep,
) -> ReportResponse | JSONResponse:
    result = container.mark_delivered.execute(report_id, body.version, actor)
    if isinstance(result, Err):
        return domain_problem(request, result.error)
    return ReportResponse.of(result.value)


@router.get("/reports/{report_id}", response_model=ReportResponse, responses={404: _PROBLEM})
def get_report(
    request: Request, report_id: str, container: ContainerDep, actor: ActorDep
) -> ReportResponse | JSONResponse:
    result = container.get_report.execute(report_id)
    if isinstance(result, Err):
        return domain_problem(request, result.error)
    return ReportResponse.of(result.value)


@router.get("/reports", response_model=list[ReportResponse])
def list_reports(
    container: ContainerDep,
    actor: ActorDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> list[ReportResponse]:
    return [ReportResponse.of(item) for item in container.list_reports.execute(limit)]


@router.post(
    "/pipeline-failures",
    status_code=201,
    response_model=FailureResponse,
    responses={422: _PROBLEM},
)
def record_failure(
    request: Request, body: FailureRequest, container: ContainerDep, actor: ActorDep
) -> FailureResponse | JSONResponse:
    """Called by the n8n error workflow. Counts the failure and returns the alert e-mail."""
    result = container.record_failure.execute(
        body.workflow_name, body.node_name, body.message, body.execution_id, actor
    )
    if isinstance(result, Err):
        return domain_problem(request, result.error)
    settings = container.settings
    alert = result.value.alert
    return FailureResponse(
        id=result.value.failure.id,
        email=EmailDto(
            sender=settings.report_sender,
            recipients=settings.split_addresses(settings.alert_recipients),
            subject=alert.subject,
            html="",
            text=alert.text,
        ),
    )


@router.get("/metrics/summary", response_model=MetricsResponse)
def metrics_summary(container: ContainerDep, actor: ActorDep) -> MetricsResponse:
    """Measured results of the automation: volume, reliability, cost and hours saved."""
    return MetricsResponse.of(container.get_metrics.execute())


@health_router.get("/live")
def live() -> dict[str, str]:
    return {"status": "UP"}


@health_router.get("/ready")
def ready(container: ContainerDep, response: Response) -> dict[str, str]:
    try:
        container.ping_database()
    except Exception:
        response.status_code = 503
        return {"status": "DOWN"}
    return {"status": "UP"}
