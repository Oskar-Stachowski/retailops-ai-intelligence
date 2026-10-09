"""Real process/cost controls; synthetic sessions here are not native validation proof."""

import json
import os
import sys
from types import SimpleNamespace

import pytest
import yaml

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import preparation_checkpoint as checkpoints
from retailops_ai.evaluation_campaign import preparation_execution as execution
from scripts import ai09_preparation_worker as worker
from scripts import measure_ai09_development_capacity as probe
from scripts import run_ai09_preparation as controller


def prepared(tmp_path, *, seconds=8):
    plan = {
        "scope": checkpoints.SCOPE,
        "controlled_test_only": True,
        "budgets": {
            "tree_rss_bytes": 1024**3,
            "scratch_bytes": 1024**2,
            "wall_seconds": seconds,
            "minimum_free_disk_bytes": 1,
            "minimum_available_memory_bytes": 1,
            "sample_seconds": 0.02,
        },
    }
    identity = {
        "scope": checkpoints.SCOPE,
        **{
            name: "a" * (40 if name.endswith("_commit") else 64)
            for name in checkpoints.IDENTITY_FIELDS - {"scope", "validators_sha256"}
        },
        "validators_sha256": {phase: "b" * 64 for phase in checkpoints.PHASES},
    }
    identity["plan_sha256"] = canonical_sha256(plan)
    root = tmp_path / "execution"
    execution.initialize(root, plan=plan, identity=identity)
    return root


def run(
    root,
    *,
    phase="generation",
    kind="native",
    program="import time; time.sleep(0.04)",
    accept=None,
    monitor=None,
):
    return execution.operate(
        root,
        phase=phase,
        kind=kind,
        command=[sys.executable, "-c", program],
        cwd=root,
        env=dict(os.environ),
        roots=(root,),
        monitor=monitor or probe.monitor,
        accept=accept or (lambda measurement: None),
    )


def test_durable_start_precedes_real_process_and_cold_and_seal_costs_accumulate(tmp_path):
    root = prepared(tmp_path)
    for phase in checkpoints.PHASES:
        for kind in execution.KINDS:
            before = execution.inspect(root)
            path = root / "events" / f"{len(before['events']):03d}.json"
            program = (
                "import json,time;from pathlib import Path;p=Path("
                + repr(str(path))
                + ");assert json.loads(p.read_bytes())['event']=='started';time.sleep(0.04)"
            )
            measured = run(root, phase=phase, kind=kind, program=program)
            after = execution.inspect(root)
            assert measured["status"] == "passed"
            assert (
                after["charged_wall_seconds"]
                >= before["charged_wall_seconds"] + measured["wall_seconds"]
            )
            assert after["remaining_wall_seconds"] < before["remaining_wall_seconds"]
    result = execution.inspect(root)
    assert result["status"] == "complete"
    assert result["completed_phases"] == 5
    assert len(result["events"]) == 20
    assert not result["unmeasured_wall_cost_present"]
    assert not result["unmeasured_cpu_cost_present"]
    with pytest.raises(ValueError, match="retry_order_or_budget_blocked"):
        run(root)


def test_failed_actual_process_keeps_cost_and_refuses_unchanged_retry(tmp_path):
    root = prepared(tmp_path)
    measurement = run(root, program="import time;time.sleep(0.04);raise SystemExit(7)")
    state = execution.inspect(root)
    assert measurement["exit_code"] == 7
    assert state["status"] == "failed"
    assert state["charged_wall_seconds"] >= measurement["wall_seconds"] > 0.04
    with pytest.raises(ValueError, match="retry_order_or_budget_blocked"):
        run(root)
    assert len(execution.inspect(root)["events"]) == 2


def test_native_completion_rejection_does_not_erase_measured_process_cost(tmp_path):
    root = prepared(tmp_path)

    def reject(measurement):
        raise ValueError("native_witness_changed")

    with pytest.raises(ValueError, match="native_witness_changed"):
        run(root, accept=reject)
    state = execution.inspect(root)
    event = state["events"][-1]
    assert state["status"] == "failed"
    assert event["measurement"]["sampled_worker_cpu_seconds"] is not None
    assert event["measurement"]["exit_code"] == 0
    assert event["charged_wall_seconds"] >= event["measurement"]["wall_seconds"] > 0


def test_observer_exception_preserves_unknown_cpu_instead_of_zero(tmp_path):
    root = prepared(tmp_path)

    def broken(*args, **kwargs):
        raise OSError("monitor_unavailable")

    with pytest.raises(OSError, match="monitor_unavailable"):
        run(root, monitor=broken)
    state = execution.inspect(root)
    assert state["unmeasured_cpu_cost_present"]
    assert state["events"][-1]["measurement"]["sampled_worker_cpu_seconds"] is None
    assert state["status"] == "failed"


def test_unfinished_operation_blocks_resume_with_unknown_remaining_cost(tmp_path):
    root = prepared(tmp_path)
    # Model the durable bytes surviving an abrupt controller loss; no process is claimed.
    execution.write_once(
        root / "events/000.json",
        {
            "sequence": 0,
            "event": "started",
            "previous_sha256": None,
            "phase": "generation",
            "kind": "native",
            "remaining_wall_seconds": 8,
            "at_utc": "controlled_interruption_boundary",
        },
    )
    state = execution.inspect(root)
    assert state["status"] == "unfinished"
    assert state["remaining_wall_seconds"] is None
    assert state["unmeasured_wall_cost_present"] and state["unmeasured_cpu_cost_present"]
    with pytest.raises(ValueError, match="retry_order_or_budget_blocked"):
        run(root)


def test_concurrent_controller_cannot_launch_a_second_owned_operation(tmp_path):
    root = prepared(tmp_path)
    with execution.locked(root):
        with pytest.raises(ValueError, match="owned_operation_still_active"):
            run(root)
    assert execution.inspect(root)["events"] == []


def test_remaining_budget_applies_to_sealing_and_its_timeout_is_retained(tmp_path):
    root = prepared(tmp_path, seconds=0.4)
    run(root, program="import time;time.sleep(0.08)")
    remaining = execution.inspect(root)["remaining_wall_seconds"]
    measurement = run(root, kind="seal", program="import time;time.sleep(2)")
    state = execution.inspect(root)
    assert measurement["reason"] == "wall_limit"
    assert measurement["wall_seconds"] < remaining + 0.5
    assert state["status"] == "failed"
    assert state["completed_phases"] == 0
    assert state["remaining_wall_seconds"] == 0


@pytest.mark.parametrize("change", ["missing", "extra", "cost", "phase", "budget", "chain"])
def test_event_chain_damage_does_not_allow_further_work(tmp_path, change):
    root = prepared(tmp_path)
    run(root)
    path = root / "events/001.json"
    event = json.loads(path.read_bytes())
    if change == "missing":
        (root / "events/000.json").unlink()
    elif change == "extra":
        (root / "events/extra.json").write_bytes(b"{}")
    else:
        if change == "cost":
            event["charged_wall_seconds"] = -1
        elif change == "phase":
            event["phase"] = "export"
        elif change == "chain":
            event["previous_sha256"] = "c" * 64
        elif change == "budget":
            path = root / "events/000.json"
            event = json.loads(path.read_bytes())
            event["remaining_wall_seconds"] += 1
        path.write_bytes(canonical_bytes(event) + b"\n")
    with pytest.raises(ValueError):
        run(root, kind="seal")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, -1, 0])
def test_invalid_budget_cannot_create_a_session(tmp_path, value):
    with pytest.raises((ValueError, TypeError)):
        prepared(tmp_path, seconds=value)
    assert not (tmp_path / "execution").exists()


@pytest.mark.parametrize("entry", [worker, controller])
def test_public_canonical_entrypoints_remain_closed_before_any_source_access(
    monkeypatch, tmp_path, entry
):
    monkeypatch.setattr(probe, "require_remote", lambda: None)
    if entry is worker:
        monkeypatch.setattr(worker, "load", lambda *args: probe)
        action = ["--worker", "generation"]
    else:
        monkeypatch.setattr(controller, "modules", lambda: (worker, probe))
        action = ["--initialize"]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "control",
            "--source",
            str(tmp_path / "missing-source"),
            "--output",
            str(tmp_path / "output"),
            *action,
        ],
    )
    with pytest.raises(ValueError, match="capacity_preparation_not_accepted"):
        entry.main()
    assert list(tmp_path.iterdir()) == []


def test_runtime_pin_changes_with_real_code_and_package_metadata(tmp_path, monkeypatch):
    (tmp_path / "data").mkdir()
    code = tmp_path / "data/module.py"
    code.write_text("version = 1\n")
    monkeypatch.setattr(worker.importlib.metadata, "distributions", lambda: [])
    first = worker.runtime(tmp_path, producer=True)
    code.write_text("version = 2\n")
    assert worker.digest(worker.runtime(tmp_path, producer=True)) != worker.digest(first)
    code.write_text("version = 1\n")
    distribution = SimpleNamespace(
        metadata={"Name": "controlled"},
        version="2",
        read_text=lambda name: "actual-record-metadata",
    )
    monkeypatch.setattr(worker.importlib.metadata, "distributions", lambda: [distribution])
    changed = worker.runtime(tmp_path, producer=True)
    assert changed["packages"][0]["version"] == "2"
    assert changed["code_sha256"] == first["code_sha256"]
    assert worker.digest(changed) != worker.digest(first)


def test_actions_uploads_each_phase_before_starting_the_next_and_retains_terminal_costs():
    workflow = yaml.safe_load(
        (worker.ROOT / ".github/workflows/ai09-development-capacity.yml").read_text()
    )
    job = workflow["jobs"]["measure"]
    assert job["timeout-minutes"] == 210
    steps = job["steps"]
    for index, phase in enumerate(checkpoints.PHASES):
        position = next(i for i, step in enumerate(steps) if step.get("id") == phase)
        assert "--phase " + phase in steps[position]["run"]
        upload = steps[position + 1]
        assert upload["uses"] == "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02"
        assert upload["if"] == "${{ always() && steps." + phase + ".outcome != 'skipped' }}"
        assert "diagnostic/checkpoints/" + phase + "/" in upload["with"]["path"]
        assert "diagnostic/events/*.json" in upload["with"]["path"]
        assert upload["with"]["overwrite"] is False
        if index < len(checkpoints.PHASES) - 1:
            assert steps[position + 2]["id"] == checkpoints.PHASES[index + 1]
    assert steps[-2]["if"] == steps[-1]["if"] == "always()"
    assert "--finalize" in steps[-2]["run"]
    assert "diagnostic-preflight/*.json" in steps[-1]["with"]["path"]
