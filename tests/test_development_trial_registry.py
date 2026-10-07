"""Persistent budget, pre-fit binding, concurrent reservations, crashes and retained failures."""

import json
import multiprocessing
import os
import shutil
import signal
from contextlib import contextmanager
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_development_comparison import protocol_for
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_tensorflow_challenger import development as development

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.trial_contract import TrialPlan
from retailops_ai.evaluation_campaign.trial_registry import (
    audit_code,
    finish,
    initialize,
    inspect,
    reserve,
    summary,
)
from retailops_ai.source_snapshot.files import SnapshotError


@pytest.fixture
def registered(development, tmp_path):
    fold, _, _ = development
    protocol = protocol_for(fold)
    root = tmp_path / "registry"
    plan = TrialPlan(
        registry_path=str(root),
        protocols=(protocol,),
        maximum_new_attempts=1,
        audit_code_sha256=audit_code(),
    )
    initialize(root, plan)
    return root, plan, canonical_sha256(protocol.model_dump(mode="json"))


def test_freeze_is_idempotent_and_cannot_reset_budget(registered, tmp_path):
    root, plan, digest = registered
    reserve(root, digest, tmp_path / "first")
    before = (root / "ledger.json").read_bytes()
    assert initialize(root, plan) == inspect(root)
    with pytest.raises(SnapshotError, match="already_frozen"):
        initialize(root, plan.model_copy(update={"maximum_attempts_per_protocol": 2}))
    with pytest.raises(SnapshotError, match="budget_exhausted"):
        reserve(root, digest, tmp_path / "second")
    assert (root / "ledger.json").read_bytes() == before
    receipt = summary(inspect(root))
    assert receipt["unresolved_new_attempts"] == 1 and receipt["charged_fit_slots"] == 4
    (root / "ledger.json").unlink()
    with pytest.raises(SnapshotError, match="cannot_reset_budget"):
        initialize(root, plan)


def test_failed_attempt_consumes_budget_and_terminal_receipt_cannot_change(registered, tmp_path):
    root, _, digest = registered
    start = reserve(root, digest, tmp_path / "first")
    result = {"outcome": "failed", "error_code": "controlled_failure", "wall_seconds": 1.0}
    terminal = finish(root, start.attempt_id, **result)
    assert finish(root, start.attempt_id, **result) == terminal
    before = (root / "ledger.json").read_bytes()
    with pytest.raises(SnapshotError, match="already_frozen"):
        finish(root, start.attempt_id, **(result | {"wall_seconds": 2.0}))
    with pytest.raises(SnapshotError, match="budget_exhausted"):
        reserve(root, digest, tmp_path / "second")
    assert (root / "ledger.json").read_bytes() == before
    assert summary(inspect(root))["failed_new_attempts"] == 1


def _race_reservation(root, digest, output, ready, results):
    ready.wait()
    try:
        reserve(Path(root), digest, Path(output))
        results.put("reserved")
    except SnapshotError as exc:
        results.put(str(exc))


def test_two_processes_cannot_spend_the_last_slot_twice(registered, tmp_path):
    root, _, digest = registered
    ctx = multiprocessing.get_context("fork")
    ready, results = ctx.Event(), ctx.Queue()
    workers = [
        ctx.Process(
            target=_race_reservation,
            args=(str(root), digest, str(tmp_path / f"output-{i}"), ready, results),
        )
        for i in range(2)
    ]
    for worker in workers:
        worker.start()
    ready.set()
    observed = sorted(results.get(timeout=10) for _ in workers)
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0
    assert observed == ["reserved", "trial_registry_attempt_budget_exhausted"]
    assert summary(inspect(root))["charged_fit_slots"] == 4


def _reserve_then_die(root, digest, output):
    reserve(Path(root), digest, Path(output))
    os.kill(os.getpid(), signal.SIGKILL)


def test_sigkill_after_reservation_is_durable_and_never_refunded(registered, tmp_path):
    root, plan, digest = registered
    worker = multiprocessing.get_context("fork").Process(
        target=_reserve_then_die, args=(str(root), digest, str(tmp_path / "crashed"))
    )
    worker.start()
    worker.join(timeout=10)
    assert worker.exitcode == -signal.SIGKILL
    assert summary(initialize(root, plan))["unresolved_new_attempts"] == 1
    with pytest.raises(SnapshotError, match="budget_exhausted"):
        reserve(root, digest, tmp_path / "retry")


@pytest.mark.parametrize("mutation", ["event", "head", "sequence", "delete", "noncanonical"])
def test_corrupt_or_partial_history_blocks_read_and_reservation(registered, tmp_path, mutation):
    root, _, digest = registered
    reserve(root, digest, tmp_path / "first")
    data = json.loads((root / "ledger.json").read_bytes())
    if mutation == "event":
        data["events"][0]["output"] += "-changed"
    elif mutation == "head":
        data["head_sha256"] = "a" * 64
    elif mutation == "sequence":
        data["events"][0]["sequence"] = 2
    elif mutation == "delete":
        data["events"] = []
    raw = json.dumps(data).encode() if mutation == "noncanonical" else canonical_bytes(data) + b"\n"
    (root / "ledger.json").write_bytes(raw)
    with pytest.raises(ValueError):
        inspect(root)
    with pytest.raises(ValueError):
        reserve(root, digest, tmp_path / "second")
    assert (root / "ledger.json").read_bytes() == raw


def test_failed_atomic_publication_keeps_previous_ledger(registered, tmp_path, monkeypatch):
    from retailops_ai.evaluation_campaign import trial_registry

    root, _, digest = registered
    before = (root / "ledger.json").read_bytes()

    def fail(*args, **kwargs):
        raise OSError("controlled_rename_failure")

    monkeypatch.setattr(trial_registry.os, "replace", fail)
    with pytest.raises(OSError, match="controlled_rename_failure"):
        reserve(root, digest, tmp_path / "first")
    assert (root / "ledger.json").read_bytes() == before
    assert summary(inspect(root))["reserved_new_attempts"] == 0
    assert not list(root.glob(".ledger-*"))


def test_unplanned_recipe_code_and_unsafe_paths_are_rejected(registered, tmp_path, monkeypatch):
    from retailops_ai.evaluation_campaign import trial_registry

    root, _, digest = registered
    with pytest.raises(SnapshotError, match="unplanned_protocol"):
        reserve(root, "0" * 64, tmp_path / "first")
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    with pytest.raises(SnapshotError, match="symlink"):
        inspect(link)
    with pytest.raises(SnapshotError, match="overlaps_registry"):
        reserve(root, digest, root / "output")
    monkeypatch.setattr(trial_registry, "audit_code", lambda: "0" * 64)
    with pytest.raises(SnapshotError, match="code_mismatch"):
        reserve(root, digest, tmp_path / "first")
    assert summary(inspect(root))["reserved_new_attempts"] == 0


@pytest.mark.parametrize(
    "flags",
    [
        {"final_test_access_authorized": True},
        {"promotion_allowed": True},
        {"maximum_attempts_per_protocol": 3},
        {"maximum_new_attempts": 2},
    ],
)
def test_plan_cannot_authorize_final_test_or_unreachable_budget(registered, flags):
    _, plan, _ = registered
    with pytest.raises(ValidationError):
        TrialPlan.model_validate_json(canonical_bytes(plan.model_dump(mode="json") | flags))


def test_audited_failure_is_reserved_before_worker_and_keeps_original_artifacts(
    registered, development, tmp_path, monkeypatch
):
    from retailops_ai.evaluation_campaign import development as benchmark
    from retailops_ai.evaluation_campaign.trial_runner import (
        run_registered_comparison,
        snapshot_attempt,
    )

    root, plan, digest = registered
    protocol = plan.protocols[0]
    fold, train, validation = development

    @contextmanager
    def parents(*args):
        yield None, None, fold, train, validation

    monkeypatch.setattr(benchmark, "development_parents", parents)
    monkeypatch.setattr(benchmark, "_protocol", lambda *args: protocol)

    def fail(*args, **kwargs):
        assert summary(inspect(root))["unresolved_new_attempts"] == 1
        raise SnapshotError("controlled_worker_failure")

    monkeypatch.setattr(benchmark, "_trees", fail)
    output = tmp_path / "attempt"
    with pytest.raises(SnapshotError, match="controlled_worker_failure"):
        run_registered_comparison(
            registry=root,
            protocol_sha256=digest,
            features=tmp_path / "features",
            split=tmp_path / "split",
            curated=tmp_path / "curated",
            output=output,
        )
    snapshot = snapshot_attempt(output)
    assert snapshot.status == "failed" and snapshot.model_starts == 0
    assert inspect(root).events[-1].snapshot == snapshot
    terminal = inspect(root).events[-1]
    assert (
        finish(
            root,
            terminal.attempt_id,
            outcome="failed",
            error_code=terminal.error_code,
            wall_seconds=terminal.wall_seconds,
            snapshot=snapshot,
        )
        == terminal
    )
    assert summary(inspect(root))["charged_fit_slots"] == 4
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    assert snapshot_attempt(output) == snapshot
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before

    def must_not_read(*args, **kwargs):
        raise AssertionError("parent access must follow budget reservation")

    monkeypatch.setattr(benchmark, "development_parents", must_not_read)
    with pytest.raises(SnapshotError, match="budget_exhausted"):
        run_registered_comparison(
            registry=root,
            protocol_sha256=digest,
            features=tmp_path / "features",
            split=tmp_path / "split",
            curated=tmp_path / "curated",
            output=tmp_path / "retry",
        )


def test_preregistered_protocol_mismatch_stops_before_fit_or_output(
    registered, development, tmp_path, monkeypatch
):
    from retailops_ai.evaluation_campaign import development as benchmark
    from retailops_ai.evaluation_campaign.trial_runner import run_registered_comparison

    root, plan, digest = registered
    fold, train, validation = development

    @contextmanager
    def parents(*args):
        yield None, None, fold, train, validation

    monkeypatch.setattr(benchmark, "development_parents", parents)
    monkeypatch.setattr(
        benchmark,
        "_protocol",
        lambda *args: plan.protocols[0].model_copy(update={"implementation_sha256": "0" * 64}),
    )

    def must_not_fit(*args, **kwargs):
        raise AssertionError("must not fit")

    monkeypatch.setattr(benchmark, "_trees", must_not_fit)
    output = tmp_path / "attempt"
    with pytest.raises(SnapshotError, match="preregistered_protocol_mismatch"):
        run_registered_comparison(
            registry=root,
            protocol_sha256=digest,
            features=tmp_path / "features",
            split=tmp_path / "split",
            curated=tmp_path / "curated",
            output=output,
        )
    assert not output.exists()
    event = inspect(root).events[-1]
    assert event.outcome == "failed" and event.snapshot is None


def test_individual_recipe_limit_applies_before_global_limit(registered, tmp_path):
    _, plan, digest = registered
    second = plan.protocols[0].model_copy(update={"implementation_sha256": "0" * 64})
    root = tmp_path / "two-recipes"
    expanded = TrialPlan(
        registry_path=str(root),
        protocols=(*plan.protocols, second),
        maximum_new_attempts=2,
        audit_code_sha256=audit_code(),
    )
    initialize(root, expanded)
    reserve(root, digest, tmp_path / "first")
    with pytest.raises(SnapshotError, match="budget_exhausted"):
        reserve(root, digest, tmp_path / "retry")
    reserve(root, canonical_sha256(second.model_dump(mode="json")), tmp_path / "second")
    assert summary(inspect(root))["reserved_new_attempts"] == 2


def test_registry_location_is_bound_and_copy_cannot_create_a_new_budget(registered, tmp_path):
    root, plan, digest = registered
    copied = tmp_path / "copied-registry"
    shutil.copytree(root, copied)
    with pytest.raises(SnapshotError, match="location_mismatch"):
        inspect(copied)
    with pytest.raises(SnapshotError, match="location_mismatch"):
        initialize(copied, plan)
    with pytest.raises(SnapshotError, match="location_mismatch"):
        reserve(copied, digest, tmp_path / "new-output")
    assert summary(inspect(root))["reserved_new_attempts"] == 0


@pytest.mark.parametrize(
    "interruption",
    [
        {"status": "interrupted"},
        {"status": "externally_interrupted_before_completed_comparison"},
        {"exit_code": -9, "error": "whole_comparison_budget_exceeded"},
        None,
    ],
)
def test_historical_interruption_formats_are_read_without_rewriting(
    registered, tmp_path, interruption
):
    from retailops_ai.evaluation_campaign.trial_runner import snapshot_attempt

    _, plan, digest = registered
    output = tmp_path / "historical"
    output.mkdir()
    (output / "protocol.json").write_bytes(
        canonical_bytes(plan.protocols[0].model_dump(mode="json"))
    )
    (output / "attempt.json").write_bytes(
        canonical_bytes(
            {"status": "running", "final_test_accessed": False, "promotion_allowed": False}
        )
    )
    (output / "trials.jsonl").write_bytes(
        canonical_bytes({"event": "protocol_frozen", "protocol_sha256": digest}) + b"\n"
    )
    if interruption is not None:
        (output / "external_interruption.json").write_bytes(
            canonical_bytes(
                interruption | {"final_test_accessed": False, "promotion_allowed": False}
            )
        )
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    snapshot = snapshot_attempt(output)
    assert snapshot.status == ("unresolved" if interruption is None else "interrupted")
    assert snapshot.model_starts == 0
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before
