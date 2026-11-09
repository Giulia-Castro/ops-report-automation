from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from report_engine.api.main import create_app
from report_engine.infrastructure.container import build_container
from report_engine.infrastructure.persistence.database import build_engine
from report_engine.infrastructure.persistence.orm import Base
from report_engine.infrastructure.settings import Settings
from tests.conftest import API_KEY, NOW, SERVICE_ROOT, csv_row, write_csv
from tests.unit.test_infrastructure import VALID, claude_message

HEADERS = {"X-API-Key": API_KEY}


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    logs = tmp_path / "logs"
    logs.mkdir()
    rows = [csv_row(hour=h % 24) for h in range(200)]
    rows += [csv_row(service="orders", status=500, message="Timeout for ana@example.com")] * 12
    write_csv(logs / "2026-11-07.csv", rows)

    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'api.db'}",
        api_keys=f"n8n:{API_KEY}",
        logs_dir=logs,
        prompts_dir=SERVICE_ROOT / "prompts" / "daily-report",
        report_locale="en",
        report_recipients="a@example.com, b@example.com",
    )
    Base.metadata.create_all(build_engine(settings.database_url))
    clock_values = iter([NOW + timedelta(seconds=s) for s in range(0, 600, 5)])
    container = build_container(settings, clock=lambda: next(clock_values))
    with TestClient(create_app(container)) as test_client:
        yield test_client


def _create(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post(
        "/v1/reports", json=body or {"reportDate": "2026-11-07"}, headers=HEADERS
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_full_pipeline_as_n8n_calls_it(client: TestClient) -> None:
    created = _create(client)
    assert created["health"] == "RED"
    assert created["status"] == "GENERATED"
    assert created["kpis"]["totalRequests"] == 212
    assert created["llmRequest"]["output_config"]["format"]["type"] == "json_schema"
    assert "ana@example.com" not in str(created)

    summarized = client.post(
        f"/v1/reports/{created['id']}/summary",
        json={"version": created["version"], "llmResponse": claude_message(VALID)},
        headers=HEADERS,
    )
    assert summarized.status_code == 200, summarized.text
    body = summarized.json()
    assert body["report"]["summarySource"] == "AI"
    assert body["email"]["recipients"] == ["a@example.com", "b@example.com"]
    assert body["email"]["subject"].startswith("[CRITICAL] Orbit")
    assert "<html" in body["email"]["html"]

    delivered = client.post(
        f"/v1/reports/{created['id']}/delivery",
        json={"version": body["report"]["version"]},
        headers=HEADERS,
    )
    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["status"] == "DELIVERED"
    assert delivered.json()["pipelineSeconds"] > 0

    again = client.post("/v1/reports", json={"reportDate": "2026-11-07"}, headers=HEADERS)
    assert again.status_code == 409
    assert again.json()["code"] == "REPORT_ALREADY_DELIVERED"

    metrics = client.get("/v1/metrics/summary", headers=HEADERS).json()
    assert metrics["reportsDelivered"] == 1
    assert metrics["aiSummaries"] == 1
    assert metrics["estimatedHoursSaved"] == 1.5

    listed = client.get("/v1/reports?limit=5", headers=HEADERS).json()
    assert [item["id"] for item in listed] == [created["id"]]
    assert client.get(f"/v1/reports/{created['id']}", headers=HEADERS).status_code == 200


def test_fallback_when_claude_failed(client: TestClient) -> None:
    created = _create(client)
    response = client.post(
        f"/v1/reports/{created['id']}/summary",
        json={"version": created["version"], "llmResponse": None},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["report"]["summarySource"] == "FALLBACK"
    assert "Rule-based summary" in response.json()["email"]["text"]


def test_auth_is_required_everywhere_but_health(client: TestClient) -> None:
    for method, path in [
        ("post", "/v1/reports"),
        ("get", "/v1/reports"),
        ("get", "/v1/metrics/summary"),
        ("post", "/v1/pipeline-failures"),
    ]:
        response = getattr(client, method)(path, headers={"X-API-Key": "wrong-key-123456789"})
        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"
    assert client.post("/v1/reports").status_code == 401
    assert client.get("/health/live").json() == {"status": "UP"}
    assert client.get("/health/ready").json() == {"status": "UP"}


def test_problem_details_are_localized(client: TestClient) -> None:
    response = client.post(
        "/v1/reports",
        json={"reportDate": "2026-11-01"},
        headers={**HEADERS, "Accept-Language": "pt-BR", "X-Correlation-ID": "corr-123456"},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "NO_LOG_DATA"
    assert body["title"] == "Sem dados de log"
    assert body["correlationId"] == "corr-123456"
    assert response.headers["X-Correlation-ID"] == "corr-123456"

    future = client.post("/v1/reports", json={"reportDate": "2030-01-01"}, headers=HEADERS)
    assert future.json()["code"] == "REPORT_DATE_IN_FUTURE"
    missing = client.get("/v1/reports/nope", headers=HEADERS)
    assert missing.status_code == 404
    assert missing.json()["code"] == "REPORT_NOT_FOUND"
    invalid = client.post("/v1/reports", json={"reportDate": "not-a-date"}, headers=HEADERS)
    assert invalid.status_code == 422
    assert invalid.json()["errors"][0]["field"] == "reportDate"
    assert client.get("/v1/nothing", headers=HEADERS).json()["code"] == "RESOURCE_NOT_FOUND"


def test_version_conflict(client: TestClient) -> None:
    created = _create(client)
    response = client.post(
        f"/v1/reports/{created['id']}/summary",
        json={"version": 99, "llmResponse": None},
        headers=HEADERS,
    )
    assert response.status_code == 409
    assert response.json()["code"] == "REPORT_VERSION_CONFLICT"
    early = client.post(
        f"/v1/reports/{created['id']}/delivery", json={"version": 1}, headers=HEADERS
    )
    assert early.json()["code"] == "REPORT_NOT_SUMMARIZED"


def test_pipeline_failure_alert(client: TestClient) -> None:
    response = client.post(
        "/v1/pipeline-failures",
        json={
            "workflowName": "Daily ops report",
            "nodeName": "Send e-mail",
            "message": "Mailbox for ana@example.com unavailable",
            "executionId": "321",
        },
        headers=HEADERS,
    )
    assert response.status_code == 201
    email = response.json()["email"]
    assert email["recipients"] == ["ops-oncall@example.com"]
    assert "[EMAIL]" in email["text"]
    assert "ana@example.com" not in email["text"]
    metrics = client.get("/v1/metrics/summary", headers=HEADERS).json()
    assert metrics["pipelineFailures"] == 1
    assert metrics["successRate"] == 0.0

    blank = client.post("/v1/pipeline-failures", json={"workflowName": " "}, headers=HEADERS)
    assert blank.status_code == 422
    assert blank.json()["code"] == "FAILURE_WORKFLOW_REQUIRED"
