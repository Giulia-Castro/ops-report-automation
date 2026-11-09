"""Request dependencies: container access and API key authentication (deny by default)."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader

from report_engine.infrastructure.container import Container

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


class UnauthorizedError(Exception):
    """Raised when the API key is missing or invalid; rendered as a 401 problem."""


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


def require_actor(
    container: Annotated[Container, Depends(get_container)],
    api_key: Annotated[str | None, Depends(_api_key_header)],
) -> str:
    """Return the audit label of the caller. No configured keys means nobody gets in."""
    if not api_key:
        raise UnauthorizedError
    for known_key, label in container.settings.parsed_api_keys().items():
        if hmac.compare_digest(known_key.encode(), api_key.encode()):
            return label
    raise UnauthorizedError


ContainerDep = Annotated[Container, Depends(get_container)]
ActorDep = Annotated[str, Depends(require_actor)]
