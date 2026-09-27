import asyncio
from time import monotonic

import pytest

from retailops_ai.domain.readiness import Dependency
from retailops_ai.pipelines.readiness import Readiness


def test_burst_timeout_bounds_queue_and_probe_concurrency():
    async def run():
        active = 0
        maximum = 0

        async def slow():
            nonlocal active, maximum
            active += 1
            maximum = max(active, maximum)
            try:
                await asyncio.sleep(10)
            finally:
                active -= 1
            return True

        check = Readiness((Dependency("slow", slow),), 0.02)
        started = monotonic()
        result = await asyncio.gather(*(check.evaluate(started=True) for _ in range(20)))
        assert monotonic() - started < 1
        assert all(r.status == "not_ready" for r in result)
        assert maximum == 1 and active == 0

    asyncio.run(run())


def test_non_boolean_success_fails_closed_and_no_startup_does_not_probe():
    async def probe():
        return "healthy"

    check = Readiness((Dependency("provider", probe),), 0.1)
    assert asyncio.run(check.evaluate(started=False)).dependencies[0].status == "down"
    assert asyncio.run(check.evaluate(started=True)).status == "not_ready"


def test_invalid_dependency_registry_is_rejected():
    async def ok():
        return True

    with pytest.raises(ValueError):
        Dependency("not a safe name", ok)
    with pytest.raises(ValueError):
        Readiness((Dependency("same", ok), Dependency("same", ok)), 0.1)
    with pytest.raises(ValueError):
        Readiness(tuple(Dependency(f"dep{i}", ok) for i in range(9)), 0.1)
