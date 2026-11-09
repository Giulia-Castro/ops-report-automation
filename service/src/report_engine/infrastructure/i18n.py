"""User-facing text (API errors and report content) in en and pt-BR."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

SUPPORTED_LOCALES = ("en", "pt-BR")
DEFAULT_LOCALE = "en"
_LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"


@lru_cache
def catalog(locale: str) -> dict[str, Any]:
    if locale not in SUPPORTED_LOCALES:
        locale = DEFAULT_LOCALE
    data: dict[str, Any] = json.loads((_LOCALES_DIR / f"{locale}.json").read_text("utf-8"))
    return data


def resolve_locale(accept_language: str | None) -> str:
    if not accept_language:
        return DEFAULT_LOCALE
    for part in accept_language.split(","):
        tag = part.split(";")[0].strip().lower()
        if tag.startswith("pt"):
            return "pt-BR"
        if tag.startswith("en"):
            return "en"
    return DEFAULT_LOCALE


class _SafeDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def fill(template: str, params: dict[str, Any] | None = None) -> str:
    return template.format_map(_SafeDict(params or {}))


def translate_error(
    locale: str, code: str, field: str, params: dict[str, Any] | None = None
) -> str:
    entry = catalog(locale)["errors"].get(code) or catalog(DEFAULT_LOCALE)["errors"].get(code, {})
    return fill(str(entry.get(field, code)), params)
