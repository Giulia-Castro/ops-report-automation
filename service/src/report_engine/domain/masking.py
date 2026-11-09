"""Personal data masking (LGPD data minimization).

Log messages are masked before they reach the LLM, the database, the e-mail or the logs.
The rules prefer over-masking: a false positive costs a little context,
a false negative leaks personal data.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("CNPJ", re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")),
    ("CPF", re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")),
    # Payment cards: 13-16 digits in groups of four ("4111 1111 1111 1111").
    ("CARD", re.compile(r"\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{1,4}\b")),
    (
        "PHONE",
        re.compile(r"(?<![\w-])(?:\+?55\s?)?(?:\(?\d{2}\)?\s?)?9?\d{4}[-\s]?\d{4}(?![\w-])"),
    ),
    # Brazilian vehicle plates: old format (ABC-1234) and Mercosul (ABC1D23).
    ("PLATE", re.compile(r"\b[A-Z]{3}-?\d[A-Z0-9]\d{2}\b")),
)


@dataclass(frozen=True, slots=True)
class MaskResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def mask_personal_data(text: str) -> MaskResult:
    counts: Counter[str] = Counter()
    masked = text
    for label, pattern in _RULES:
        masked, replaced = pattern.subn(f"[{label}]", masked)
        if replaced:
            counts[label] += replaced
    return MaskResult(text=masked, counts=dict(counts))
