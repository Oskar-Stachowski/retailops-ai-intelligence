"""Dependency ports; no HTTP framework or provider SDK in the domain."""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class Dependency:
    name: str
    check: Callable[[], Awaitable[bool]]
    required: bool = True

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", self.name):
            raise ValueError("dependency name must be a bounded static identifier")


@dataclass(frozen=True)
class DependencyResult:
    name: str
    required: bool
    status: Literal["up", "down", "timeout"]


@dataclass(frozen=True)
class ReadinessResult:
    status: Literal["ready", "degraded", "not_ready"]
    dependencies: tuple[DependencyResult, ...]
