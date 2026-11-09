"""Versioned prompts stored as Markdown files with a small front matter header."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_FRONT_MATTER = re.compile(r"\A---\s*\n(?P<header>.*?)\n---\s*\n(?P<body>.*)\Z", re.DOTALL)
_VERSION = re.compile(r"^v\d+$")


@dataclass(frozen=True, slots=True)
class Prompt:
    version: str
    description: str
    text: str


def load_prompt(prompts_dir: Path, version: str) -> Prompt:
    if not _VERSION.match(version):
        raise ValueError(f"invalid prompt version: {version!r}")
    path = prompts_dir / f"{version}.md"
    content = path.read_text(encoding="utf-8")
    match = _FRONT_MATTER.match(content)
    if match is None:
        raise ValueError(f"prompt {path} is missing the front matter header")
    header: dict[str, str] = {}
    for line in match.group("header").splitlines():
        key, sep, value = line.partition(":")
        if sep:
            header[key.strip()] = value.strip()
    if header.get("version") != version:
        raise ValueError(f"prompt {path} declares version {header.get('version')!r}")
    return Prompt(
        version=version,
        description=header.get("description", ""),
        text=match.group("body").strip(),
    )
