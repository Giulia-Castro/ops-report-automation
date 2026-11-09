"""Minimal Result type: business outcomes are values, not exceptions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Ok[T]:
    value: T

    def is_ok(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class Err[E]:
    error: E

    def is_ok(self) -> bool:
        return False


type Result[T, E] = Ok[T] | Err[E]
