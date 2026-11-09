"""Contract tests for the n8n workflows kept in Git.

They catch the mistakes that otherwise only show up at 07:00: a renamed node still referenced
by an expression, a route the service does not have, a secret pasted into a node.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from report_engine.api.main import create_app
from tests.conftest import PROJECT_ROOT

WORKFLOWS_DIR = PROJECT_ROOT / "n8n" / "workflows"
CREDENTIAL_IDS = {"OpsEngineApiKey1", "OpsAnthropicKey1", "OpsSmtpServer001"}
ENGINE_URL = re.compile(r"^=?http://report-engine:8000(?P<path>/[^\s]*)$")
NODE_REFERENCE = re.compile(r"\$\('([^']+)'\)")
SECRET_HINTS = re.compile(
    r"(sk-ant-|api[_-]?key\"\s*:\s*\"[^\"]{8,}|password\"\s*:\s*\"[^\"]+)", re.I
)


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((WORKFLOWS_DIR / name).read_text(encoding="utf-8"))
    return data


def _workflows() -> list[str]:
    return sorted(path.name for path in WORKFLOWS_DIR.glob("*.json"))


def _route_templates() -> set[tuple[str, str]]:
    paths: dict[str, dict[str, Any]] = create_app().openapi()["paths"]
    return {
        (method.upper(), re.sub(r"\{[^}]+\}", "{}", path))
        for path, operations in paths.items()
        for method in operations
    }


def test_workflow_files_exist() -> None:
    assert _workflows() == ["daily-report.json", "error-alert.json"]


@pytest.mark.parametrize("name", _workflows())
def test_connections_and_references_point_to_existing_nodes(name: str) -> None:
    workflow = _load(name)
    names = {node["name"] for node in workflow["nodes"]}
    assert len(names) == len(workflow["nodes"]), "node names must be unique"
    for source, outputs in workflow["connections"].items():
        assert source in names
        for branch in outputs["main"]:
            for target in branch:
                assert target["node"] in names
    raw = json.dumps(workflow)
    for referenced in NODE_REFERENCE.findall(raw):
        assert referenced in names, f"expression references missing node {referenced!r}"


@pytest.mark.parametrize("name", _workflows())
def test_no_secrets_and_known_credentials_only(name: str) -> None:
    workflow = _load(name)
    assert not SECRET_HINTS.search(json.dumps(workflow))
    for node in workflow["nodes"]:
        for credential in node.get("credentials", {}).values():
            assert credential["id"] in CREDENTIAL_IDS


@pytest.mark.parametrize("name", _workflows())
def test_engine_calls_match_service_routes(name: str) -> None:
    routes = _route_templates()
    for node in _load(name)["nodes"]:
        params = node["parameters"]
        match = ENGINE_URL.match(params.get("url", ""))
        if not match:
            continue
        path = re.sub(r"\{\{[^}]+\}\}", "{}", match.group("path"))
        assert (params["method"], path) in routes, f"{node['name']}: {params['method']} {path}"
        assert node.get("retryOnFail") is True


def test_daily_report_is_resilient() -> None:
    workflow = _load("daily-report.json")
    nodes = {node["name"]: node for node in workflow["nodes"]}
    assert workflow["settings"]["errorWorkflow"] == _load("error-alert.json")["id"]
    claude = nodes["Claude: write summary"]
    assert claude["onError"] == "continueErrorOutput"
    assert claude["parameters"]["url"] == "https://api.anthropic.com/v1/messages"
    # Both the success and the error output go to the summary step (fallback path).
    outputs = workflow["connections"]["Claude: write summary"]["main"]
    assert [branch[0]["node"] for branch in outputs] == ["Attach summary", "Attach summary"]
    assert nodes["On demand (webhook)"]["parameters"]["authentication"] == "headerAuth"


def test_bootstrap_publishes_every_workflow() -> None:
    # n8n 2.x refuses to run an error workflow that is not published.
    script = (PROJECT_ROOT / "n8n" / "init" / "bootstrap.sh").read_text(encoding="utf-8")
    for name in _workflows():
        assert f"publish:workflow --id={_load(name)['id']}" in script
    renderer = (PROJECT_ROOT / "n8n" / "init" / "render-credentials.js").read_text("utf-8")
    for credential_id in CREDENTIAL_IDS:
        assert credential_id in renderer
