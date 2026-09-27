"""Bounded asynchronous checks shared by the HTTP adapter and future jobs."""

import asyncio
import math
from typing import Literal

from retailops_ai.domain.readiness import Dependency, DependencyResult, ReadinessResult


class Readiness:
    def __init__(self, dependencies: tuple[Dependency, ...], timeout_seconds: float) -> None:
        names = [dependency.name for dependency in dependencies]
        if len(names) > 8 or len(set(names)) != len(names):
            raise ValueError("at most eight uniquely named dependencies are supported")
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 5:
            raise ValueError("dependency timeout must be positive and at most five seconds")
        self.dependencies = dependencies
        self.timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()

    async def _check(self, dependency: Dependency) -> DependencyResult:
        status: Literal["up", "down", "timeout"] = "down"
        try:
            async with asyncio.timeout(self.timeout_seconds):
                available = await dependency.check()
            # Only an explicit True is success; arbitrary truthy responses fail closed.
            status = "up" if available is True else "down"
        except TimeoutError:
            status = "timeout"
        except Exception:
            # Provider exceptions can contain credentials and must never escape.
            status = "down"
        return DependencyResult(dependency.name, dependency.required, status)

    async def evaluate(self, *, started: bool) -> ReadinessResult:
        startup = DependencyResult("startup", True, "up" if started else "down")
        if not started:
            return ReadinessResult("not_ready", (startup,))
        # Bound concurrent probes during a health-check burst.
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with self._lock:
                    results = (
                        startup,
                        *await asyncio.gather(*(self._check(d) for d in self.dependencies)),
                    )
        except TimeoutError:
            results = (
                startup,
                *(DependencyResult(d.name, d.required, "timeout") for d in self.dependencies),
            )
        if any(d.required and d.status != "up" for d in results):
            return ReadinessResult("not_ready", results)
        if any(d.status != "up" for d in results):
            return ReadinessResult("degraded", results)
        return ReadinessResult("ready", results)
