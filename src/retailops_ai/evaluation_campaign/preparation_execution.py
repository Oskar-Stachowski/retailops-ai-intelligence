"""Durable costs for separate native and checkpoint operations of one diagnostic.

This is not a Project journal. The controller must authorize the recipe before
initialization. Each operation is charged before launch and retains its measured
cost on failure; an interrupted operation has unknown cost and cannot be retried
through this session. A trusted remote attempt history is required for resume.
"""

from __future__ import annotations

import fcntl
import math
import os
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeGuard

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.preparation_checkpoint import PHASES, validate_identity
from retailops_ai.source_snapshot.files import checked_directory, read_json, regular_file
from retailops_ai.source_snapshot.publish import fsync_tree

VERSION = "ai09-preparation-execution-1.0.0"
MAX_EVENTS = 20  # two durable records for each native and seal operation, five phases
KINDS = ("native", "seal")
LIMITS = {
    "tree_rss_bytes",
    "scratch_bytes",
    "wall_seconds",
    "minimum_free_disk_bytes",
    "minimum_available_memory_bytes",
    "sample_seconds",
}


def _positive(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _nonnegative(value: Any) -> TypeGuard[int | float]:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def write_once(path: Path, value: dict[str, Any]) -> None:
    """Private immutable receipt, including the directory entry, durable before return."""
    checked_directory(path.parent)
    raw = canonical_bytes(value) + b"\n"
    if len(raw) > 4 * 1024**2:
        raise ValueError("preparation_execution_receipt_budget")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def locked(root: Path) -> Iterator[None]:
    root = checked_directory(root)
    if stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise ValueError("preparation_execution_private_session_required")
    fd = os.open(root / "execution.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("preparation_execution_private_regular_lock_required")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("preparation_execution_owned_operation_still_active") from error
        yield
    finally:
        os.close(fd)


def initialize(root: Path, *, plan: dict[str, Any], identity: dict[str, Any]) -> None:
    validate_identity(identity)
    if canonical_sha256(plan) != identity["plan_sha256"]:
        raise ValueError("preparation_execution_plan_identity")
    budgets = plan.get("budgets")
    if (
        not isinstance(budgets, dict)
        or set(budgets) != LIMITS
        or any(not _positive(value) for value in budgets.values())
    ):
        raise ValueError("preparation_execution_budgets")
    if root.exists() or not root.is_absolute() or any(p.is_symlink() for p in root.parents):
        raise ValueError("preparation_execution_fresh_owned_directory_required")
    root.mkdir(mode=0o700, parents=True)
    write_once(root / "preparation-plan.json", plan)
    write_once(root / "preparation-identity.json", identity)
    (root / "events").mkdir(mode=0o700)
    write_once(
        root / "execution.json",
        {
            "version": VERSION,
            "identity_sha256": canonical_sha256(identity),
            "plan_sha256": canonical_sha256(plan),
            "created_at_utc": datetime.now(UTC).isoformat(),
            "project_journal_initialized": False,
            "final_test_authorized": False,
        },
    )
    fsync_tree(root)


def inspect(root: Path) -> dict[str, Any]:
    """Read the complete immutable event chain; incomplete cost is explicitly unknown."""
    root = checked_directory(root)
    plan = read_json(root, "preparation-plan.json")
    identity = read_json(root, "preparation-identity.json")
    header = read_json(root, "execution.json")
    validate_identity(identity)
    if (
        header.get("version") != VERSION
        or header.get("identity_sha256") != canonical_sha256(identity)
        or header.get("plan_sha256") != canonical_sha256(plan)
        or identity["plan_sha256"] != canonical_sha256(plan)
    ):
        raise ValueError("preparation_execution_configuration_changed")
    events_root = checked_directory(root / "events")
    names = sorted(p.name for p in events_root.iterdir())
    if len(names) > MAX_EVENTS or names != [f"{i:03d}.json" for i in range(len(names))]:
        raise ValueError("preparation_execution_missing_or_extra_event")
    events: list[dict[str, Any]] = []
    previous = None
    charged = 0.0
    measured_cpu = 0.0
    unknown_cpu = False
    failed = False
    for index, name in enumerate(names):
        event = read_json(events_root, name)
        with regular_file(events_root, name) as stream:
            if stream.read() != canonical_bytes(event) + b"\n":
                raise ValueError("preparation_execution_noncanonical_event")
        operation = index // 2
        if (
            failed
            or event.get("sequence") != index
            or type(event.get("sequence")) is not int
            or event.get("previous_sha256") != previous
            or event.get("phase") != PHASES[operation // 2]
            or event.get("kind") != KINDS[operation % 2]
            or event.get("event") != ("started" if index % 2 == 0 else "finished")
        ):
            raise ValueError("preparation_execution_event_chain")
        if index % 2:
            measurement = event.get("measurement", {})
            cost = event.get("charged_wall_seconds")
            if (
                not _nonnegative(cost)
                or not _nonnegative(measurement.get("wall_seconds"))
                or cost < measurement["wall_seconds"]
                or measurement.get("status") not in {"passed", "failed"}
                or measurement.get("phase") != event["phase"]
            ):
                raise ValueError("preparation_execution_invalid_measured_cost")
            if measurement["status"] == "passed" and (
                measurement.get("reason") is not None
                or type(measurement.get("exit_code")) is not int
                or measurement["exit_code"] != 0
            ):
                raise ValueError("preparation_execution_false_success")
            cpu = measurement.get("sampled_worker_cpu_seconds")
            if cpu is None:
                unknown_cpu = True
                if measurement["status"] == "passed":
                    raise ValueError("preparation_execution_success_without_measured_cpu")
            elif not _nonnegative(cpu):
                raise ValueError("preparation_execution_invalid_measured_cpu")
            else:
                measured_cpu += cpu
            charged += float(cost)
            failed = measurement["status"] == "failed"
        elif event.get("remaining_wall_seconds") != max(
            0, plan["budgets"]["wall_seconds"] - charged
        ):
            raise ValueError("preparation_execution_remaining_budget_changed")
        previous = canonical_sha256(event)
        events.append(event)
    incomplete = len(events) % 2 == 1
    return {
        "events": events,
        "charged_wall_seconds": charged,
        "worker_cpu_seconds_lower_bound": measured_cpu,
        "unmeasured_cpu_cost_present": incomplete or unknown_cpu,
        "unmeasured_wall_cost_present": incomplete,
        "remaining_wall_seconds": None
        if incomplete
        else max(0, plan["budgets"]["wall_seconds"] - charged),
        "status": "unfinished"
        if incomplete
        else "failed"
        if failed
        else "complete"
        if len(events) == MAX_EVENTS
        else "prepared",
        "completed_phases": sum(
            e["event"] == "finished"
            and e["kind"] == "seal"
            and e["measurement"]["status"] == "passed"
            for e in events
        ),
        "identity": identity,
        "plan": plan,
    }


def operate(
    root: Path,
    *,
    phase: str,
    kind: str,
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    roots: tuple[Path, ...],
    monitor: Callable[..., dict[str, Any]],
    accept: Callable[[dict[str, Any]], None],
    live_progress: Any = None,
) -> dict[str, Any]:
    """One separately guarded process; its start is durable before any child is launched."""
    with locked(root):
        state = inspect(root)
        index = len(state["events"])
        if (
            state["status"] != "prepared"
            or state["remaining_wall_seconds"] <= 0
            or phase != PHASES[index // 4]
            or kind != KINDS[(index // 2) % 2]
        ):
            raise ValueError("preparation_execution_retry_order_or_budget_blocked")
        before = {
            "sequence": index,
            "event": "started",
            "previous_sha256": canonical_sha256(state["events"][-1]) if index else None,
            "phase": phase,
            "kind": kind,
            "remaining_wall_seconds": state["remaining_wall_seconds"],
            "at_utc": datetime.now(UTC).isoformat(),
        }
        write_once(root / "events" / f"{index:03d}.json", before)
        started = time.perf_counter()
        measurement: dict[str, Any] | None = None
        failure: BaseException | None = None
        try:
            measurement = monitor(
                command,
                cwd=cwd,
                env=env,
                log=root / f"{phase}.{kind}.log",
                roots=roots,
                budgets=state["plan"]["budgets"],
                deadline=started + state["remaining_wall_seconds"],
                live_progress=live_progress,
            )
            measurement["phase"] = phase
            if measurement["status"] == "passed":
                accept(measurement)
        except BaseException as error:
            failure = error
            if measurement is None:
                measurement = {
                    "phase": phase,
                    "status": "failed",
                    "reason": "control_error_" + type(error).__name__,
                    "wall_seconds": time.perf_counter() - started,
                    "sampled_worker_cpu_seconds": None,
                    "exit_code": None,
                    "cpu_cost_unknown_not_zero": True,
                }
            else:
                measurement.update(
                    status="failed", reason="completion_error_" + type(error).__name__
                )
        finally:
            if measurement is not None:
                elapsed = time.perf_counter() - started
                write_once(
                    root / "events" / f"{index + 1:03d}.json",
                    {
                        "sequence": index + 1,
                        "event": "finished",
                        "previous_sha256": canonical_sha256(before),
                        "phase": phase,
                        "kind": kind,
                        "at_utc": datetime.now(UTC).isoformat(),
                        "measurement": measurement,
                        "charged_wall_seconds": max(elapsed, measurement["wall_seconds"]),
                    },
                )
        if failure is not None:
            raise failure
        if measurement is None:
            raise ValueError("preparation_execution_missing_measurement")
        return measurement
