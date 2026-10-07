"""Durable reservations shared by output directories, with bounded hash-linked history."""

import fcntl
import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path
from typing import Any
from uuid import uuid4

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.trial_contract import (
    AttemptSnapshot,
    TrialEvent,
    TrialLedger,
    TrialPlan,
)
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    directory_fd,
    read_bytes,
)

MAX_LEDGER_BYTES = 4 * 1024**2


def audit_code() -> str:
    root = files("retailops_ai.evaluation_campaign")
    return canonical_sha256(
        {
            name: hashlib.sha256(root.joinpath(name).read_bytes()).hexdigest()
            for name in (
                "trial_contract.py",
                "trial_registry.py",
                "trial_runner.py",
                "trial_cli.py",
            )
        }
    )


@contextmanager
def _locked(root: Path) -> Iterator[None]:
    checked_directory(root)
    if stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise SnapshotError("trial_registry_private_directory_required")
    with directory_fd(root) as root_fd:
        descriptor = os.open(
            "registry.lock",
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
            0o600,
            dir_fd=root_fd,
        )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
                raise SnapshotError("trial_registry_private_regular_lock_required")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _read(root: Path) -> TrialLedger:
    path = root / "ledger.json"
    if stat.S_IMODE(path.lstat().st_mode) != 0o600:
        raise SnapshotError("trial_registry_private_ledger_required")
    raw = read_bytes(root, "ledger.json", MAX_LEDGER_BYTES)
    ledger = TrialLedger.model_validate_json(raw)
    if ledger.plan.registry_path != str(root.absolute()):
        raise SnapshotError("trial_registry_location_mismatch")
    if raw != canonical_bytes(ledger.model_dump(mode="json")) + b"\n":
        raise SnapshotError("trial_registry_noncanonical_ledger")
    return ledger


def _publish(root: Path, ledger: TrialLedger) -> None:
    raw = canonical_bytes(ledger.model_dump(mode="json")) + b"\n"
    if len(raw) > MAX_LEDGER_BYTES:
        raise SnapshotError("trial_registry_size_limit")
    temporary = ".ledger-" + uuid4().hex
    with directory_fd(root) as root_fd:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=root_fd,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, "ledger.json", src_dir_fd=root_fd, dst_dir_fd=root_fd)
            os.fsync(root_fd)
        finally:
            if (root / temporary).exists():
                os.unlink(temporary, dir_fd=root_fd)


def initialize(root: Path, plan: TrialPlan) -> TrialLedger:
    plan = TrialPlan.model_validate_json(canonical_bytes(plan.model_dump(mode="json")))
    checked_directory(root.parent)
    if plan.registry_path != str(root.absolute()):
        raise SnapshotError("trial_registry_location_mismatch")
    if plan.audit_code_sha256 != audit_code():
        raise SnapshotError("trial_registry_audit_code_mismatch")
    try:
        root.mkdir(mode=0o700)
        created = True
    except FileExistsError:
        created = False
    with _locked(root):
        if (root / "ledger.json").exists():
            ledger = _read(root)
            if ledger.plan != plan:
                raise SnapshotError("trial_registry_plan_already_frozen")
            return ledger
        if not created:
            raise SnapshotError("trial_registry_missing_ledger_cannot_reset_budget")
        ledger = TrialLedger(plan=plan, head_sha256=canonical_sha256(plan.model_dump(mode="json")))
        _publish(root, ledger)
        return ledger


def inspect(root: Path) -> TrialLedger:
    with _locked(root):
        return _read(root)


def _append(root: Path, ledger: TrialLedger, **values: Any) -> TrialLedger:
    event = TrialEvent(
        sequence=len(ledger.events) + 1,
        previous_sha256=ledger.head_sha256,
        at=datetime.now(UTC),
        **values,
    )
    payload = ledger.model_dump(mode="json") | {
        "events": [
            *(e.model_dump(mode="json") for e in ledger.events),
            event.model_dump(mode="json"),
        ],
        "head_sha256": canonical_sha256(event.model_dump(mode="json")),
    }
    next_ledger = TrialLedger.model_validate_json(canonical_bytes(payload))
    _publish(root, next_ledger)
    return next_ledger


def reserve(root: Path, protocol_sha256: str, output: Path) -> TrialEvent:
    output = output.absolute()
    checked_directory(output.parent)
    if ".." in output.parts or output.is_symlink() or output.exists():
        raise SnapshotError("trial_registry_output_must_be_new")
    if output.is_relative_to(root.absolute()) or root.absolute().is_relative_to(output):
        raise SnapshotError("trial_registry_output_overlaps_registry")
    with _locked(root):
        ledger = _read(root)
        if ledger.plan.audit_code_sha256 != audit_code():
            raise SnapshotError("trial_registry_audit_code_mismatch")
        recipes = {canonical_sha256(p.model_dump(mode="json")) for p in ledger.plan.protocols}
        if protocol_sha256 not in recipes:
            raise SnapshotError("trial_registry_unplanned_protocol")
        starts = [e for e in ledger.events if e.kind == "reserved"]
        if (
            len(starts) >= ledger.plan.maximum_new_attempts
            or sum(e.protocol_sha256 == protocol_sha256 for e in starts)
            >= ledger.plan.maximum_attempts_per_protocol
        ):
            raise SnapshotError("trial_registry_attempt_budget_exhausted")
        if str(output) in {e.output for e in starts} | {
            h.output for h in ledger.plan.historical_attempts
        }:
            raise SnapshotError("trial_registry_output_already_reserved")
        ledger = _append(
            root,
            ledger,
            kind="reserved",
            attempt_id="attempt-" + uuid4().hex,
            protocol_sha256=protocol_sha256,
            output=str(output),
        )
        return ledger.events[-1]


def finish(root: Path, attempt_id: str, **result: Any) -> TrialEvent:
    with _locked(root):
        ledger = _read(root)
        start = next((e for e in ledger.events if e.attempt_id == attempt_id), None)
        if start is None or start.kind != "reserved":
            raise SnapshotError("trial_registry_unknown_reservation")
        for event in ledger.events:
            if event.attempt_id == attempt_id and event.kind == "finished":
                comparison = dict(result)
                if isinstance(comparison.get("snapshot"), AttemptSnapshot):
                    comparison["snapshot"] = comparison["snapshot"].model_dump(mode="json")
                expected = TrialEvent.model_validate_json(
                    canonical_bytes(event.model_dump(mode="json") | comparison)
                )
                if expected != event:
                    raise SnapshotError("trial_registry_result_already_frozen")
                return event
        ledger = _append(
            root,
            ledger,
            kind="finished",
            attempt_id=attempt_id,
            protocol_sha256=start.protocol_sha256,
            output=start.output,
            **result,
        )
        return ledger.events[-1]


def summary(ledger: TrialLedger) -> dict[str, Any]:
    starts = [e for e in ledger.events if e.kind == "reserved"]
    done = {e.attempt_id: e for e in ledger.events if e.kind == "finished"}
    return {
        "plan_sha256": canonical_sha256(ledger.plan.model_dump(mode="json")),
        "head_sha256": ledger.head_sha256,
        "historical_attempts": len(ledger.plan.historical_attempts),
        "historical_model_starts": sum(h.model_starts for h in ledger.plan.historical_attempts),
        "historical_model_completions": sum(
            h.model_completions for h in ledger.plan.historical_attempts
        ),
        "historical_status_counts": {
            status: sum(h.status == status for h in ledger.plan.historical_attempts)
            for status in (
                "completed_development_diagnostic",
                "failed",
                "interrupted",
                "unresolved",
            )
        },
        "reserved_new_attempts": len(starts),
        "charged_fit_slots": 4 * len(starts),
        "maximum_fit_slots": 4 * ledger.plan.maximum_new_attempts,
        "completed_new_attempts": sum(
            e.outcome == "completed_development_diagnostic" for e in done.values()
        ),
        "failed_new_attempts": sum(e.outcome == "failed" for e in done.values()),
        "unresolved_new_attempts": sum(e.attempt_id not in done for e in starts),
        "finished_attempt_wall_seconds": sum(e.wall_seconds or 0 for e in done.values()),
        "full_pipeline_peak_rss_bytes": None,
        "full_pipeline_cpu_seconds": None,
        "unresolved_cost": "unknown_not_zero",
        "evaluation_status": "not_ready",
        "final_test_access_authorized": False,
        "promotion_allowed": False,
    }
