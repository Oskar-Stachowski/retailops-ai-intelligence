"""Cold credential-free computation, pinned images and lost leases; mechanics only."""

import hashlib

import pytest
from test_stockout_batch import backend as backend
from test_stockout_batch import conditional as conditional
from test_stockout_batch import context as context
from test_stockout_batch import job as job
from test_stockout_batch import records as records
from test_stockout_batch import source as source

from retailops_ai.forecast_jobs.execution_contracts import ExecutionLimits
from retailops_ai.forecast_jobs.queue import LeaseLost
from retailops_ai.source_snapshot.protocol import resource_bytes
from retailops_ai.stockout_jobs.execution import StockoutExecution, supervise
from retailops_ai.stockout_jobs.queue import StockoutClaim
from retailops_ai.stockout_jobs.worker import run_attempt


def execution(job, **changes):
    run, inputs, release, now = job
    args = dict(
        environment="test",
        run=run,
        inputs=inputs,
        release=release,
        image_digest=release.image_digest,
        dependency_lock_sha256=hashlib.sha256(resource_bytes("dependencies.lock")).hexdigest(),
        generated_at=now,
        limits=ExecutionLimits(wall_seconds=30.0, cpu_seconds=15, rss_bytes=512 * 1024**2),
    )
    args.update(changes)
    return StockoutExecution(**args)


def test_cold_stockout_numeric_child_replays_exact_output_and_receives_no_credentials(
    job, monkeypatch
):
    import retailops_ai.stockout_jobs.execution as module

    original = module.subprocess.Popen
    captured = []

    def launch(*args, **kwargs):
        captured.append(kwargs["env"])
        return original(*args, **kwargs)

    monkeypatch.setattr(module.subprocess, "Popen", launch)
    monkeypatch.setenv("AWS_SESSION_TOKEN", "explicit_mechanics_sentinel_never_forward")
    monkeypatch.setenv("DATABASE_URL", "explicit_mechanics_sentinel_never_forward")
    monkeypatch.setenv("GITHUB_TOKEN", "explicit_mechanics_sentinel_never_forward")
    ticks = []
    result = supervise(execution(job), on_tick=lambda: ticks.append(True))
    assert ticks and len(captured) == 1
    assert (
        not {"AWS_SESSION_TOKEN", "DATABASE_URL", "GITHUB_TOKEN", "PYTHONPATH"} & captured[0].keys()
    )
    assert result.output.run_id == job[0].run_id
    assert result.output.release == job[2]
    assert result.output.model_refits == 0
    assert result.peak_rss_bytes <= 512 * 1024**2


@pytest.mark.parametrize("change", ["image", "environment", "release", "status"])
def test_invalid_execution_pins_are_rejected_before_launch(job, change):
    if change == "image":
        args = dict(image_digest="sha256:" + "f" * 64)
    elif change == "environment":
        args = dict(environment="local")
    elif change == "release":
        args = dict(release=job[2].model_copy(update={"image_digest": "sha256:" + "f" * 64}))
    else:
        args = dict(run=job[0].model_copy(update={"status": "queued"}))
    with pytest.raises(ValueError):
        execution(job, **args)


def test_wrong_dependency_lock_is_rejected_inside_isolated_child(job):
    with pytest.raises(ValueError, match="child"):
        supervise(execution(job, dependency_lock_sha256="f" * 64))


def test_lost_lease_interrupts_child_and_cannot_return_a_complete_result(job):
    def tick():
        raise LeaseLost("explicit_stockout_test_lost_lease")

    with pytest.raises(LeaseLost):
        supervise(execution(job), on_tick=tick)


class Queue:
    environment = "test"

    def __init__(self):
        self.completed, self.failed, self.heartbeats = [], [], 0

    def execution_budget(self, claim):
        return 30.0

    def heartbeat(self, claim):
        self.heartbeats += 1

    def complete(self, claim, output, *, tick):
        tick()
        self.completed.append(output)

    def fail(self, claim, *, reason, retryable):
        self.failed.append((reason, retryable))


def started_claim(job):
    run, inputs, release, now = job
    # The base batch fixture uses relative future timestamps. A worker claim
    # represents a task already started by the real DB clock.
    reviewed_at = release.binding.approval.reviewed_at
    run = run.model_copy(update={"requested_at": reviewed_at, "started_at": reviewed_at})
    return StockoutClaim(run, inputs, release, "test-private-token", now)


def test_worker_checks_registry_again_before_publication_and_preserves_lease_failures(job):
    claim = started_claim(job)
    release = claim.release
    queue, guards = Queue(), []
    assert (
        run_attempt(
            queue, claim, image_digest=release.image_digest, guard=lambda full: guards.append(full)
        )
        == "succeeded"
    )
    assert queue.heartbeats >= 3 and True in guards and len(queue.completed) == 1
    queue = Queue()

    def lost(full):
        raise LeaseLost("explicit_stockout_lost_lease")

    assert run_attempt(queue, claim, image_digest=release.image_digest, guard=lost) == "lease_lost"
    assert not queue.completed and not queue.failed


def test_registry_or_image_failure_closes_attempt_without_publishing(job):
    claim = started_claim(job)
    release = claim.release
    queue = Queue()
    assert (
        run_attempt(queue, claim, image_digest="sha256:" + "f" * 64, guard=lambda full: None)
        == "failed"
    )
    assert not queue.completed and queue.failed == [("stockout_executor_failed", True)]
    queue = Queue()

    def rejected(full):
        raise ValueError("explicit_stockout_registry_rejected")

    assert run_attempt(queue, claim, image_digest=release.image_digest, guard=rejected) == "failed"
    assert not queue.completed and len(queue.failed) == 1
