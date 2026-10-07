"""Real isolated process costs and failure containment; no canonical data generation."""

import copy
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from scripts import measure_ai09_development_capacity as probe


def budgets() -> dict:
    return {
        "tree_rss_bytes": 512 * 1024**2,
        "scratch_bytes": 1024**2,
        "wall_seconds": 5,
        "minimum_free_disk_bytes": 1,
        "minimum_available_memory_bytes": 1,
        "sample_seconds": 0.02,
    }


def run(tmp_path: Path, program: str, *, limits: dict | None = None, seconds: float = 5) -> dict:
    return probe.monitor(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        env=dict(os.environ),
        log=tmp_path / "worker.log",
        roots=(tmp_path,),
        budgets=limits or budgets(),
        deadline=time.perf_counter() + seconds,
    )


def test_actual_worker_success_cost_is_measured(tmp_path: Path) -> None:
    receipt = run(tmp_path, "import time; time.sleep(0.15)")
    assert receipt["status"] == "passed"
    assert receipt["exit_code"] == 0
    assert receipt["wall_seconds"] >= 0.15
    assert receipt["samples"] > 1
    assert receipt["sampled_tree_peak_rss_bytes"] >= psutil.Process().memory_info().rss * 0.8
    assert receipt["minimum_free_disk_bytes"] > 0


def test_actual_nonzero_exit_retains_failure_and_cost(tmp_path: Path) -> None:
    receipt = run(tmp_path, "import time; time.sleep(0.05); raise SystemExit(7)")
    assert receipt["status"] == "failed"
    assert receipt["reason"] == "worker_exit"
    assert receipt["exit_code"] == 7
    assert receipt["wall_seconds"] >= 0.05
    assert receipt["sampled_tree_peak_rss_bytes"] > 0


def test_wall_limit_kills_only_owned_process_group(tmp_path: Path) -> None:
    foreign = psutil.Process()
    program = (
        "import subprocess, sys, time; from pathlib import Path; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "Path('child.pid').write_text(str(child.pid)); time.sleep(30)"
    )
    receipt = run(tmp_path, program, seconds=0.4)
    assert receipt["reason"] == "wall_limit"
    assert receipt["exit_code"] != 0
    pid = int((tmp_path / "child.pid").read_text())
    # The adopted child can remain a zombie until the host's reaper observes it.
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    assert foreign.is_running()


def test_tree_limit_includes_actual_descendant_allocation(tmp_path: Path) -> None:
    limits = budgets()
    limits["tree_rss_bytes"] = psutil.Process().memory_info().rss + 40 * 1024**2
    program = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', "
        "'import time; allocation = bytearray(64 * 1024**2); time.sleep(30)']); "
        "time.sleep(30)"
    )
    receipt = run(tmp_path, program, limits=limits)
    assert receipt["reason"] == "tree_rss_limit"
    assert receipt["sampled_tree_peak_rss_bytes"] > limits["tree_rss_bytes"]


def test_actual_scratch_overflow_stops_worker(tmp_path: Path) -> None:
    receipt = run(
        tmp_path,
        "from pathlib import Path; import time; Path('large').write_bytes(b'x' * 2 * 1024**2); time.sleep(30)",
    )
    assert receipt["reason"] == "scratch_limit"
    assert receipt["sampled_peak_scratch_logical_bytes"] >= 2 * 1024**2
    assert (tmp_path / "large").exists()


def test_scratch_symlink_is_fail_closed(tmp_path: Path) -> None:
    (tmp_path / "link").symlink_to(tmp_path.parent)
    receipt = run(tmp_path, "import time; time.sleep(30)")
    assert receipt["reason"] == "monitor_error_ValueError"
    assert receipt["exit_code"] != 0


def test_capacity_probe_refuses_local_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(ValueError, match="requires_isolated_github_runner"):
        probe.require_remote()


@pytest.mark.parametrize(
    "field,value", [("products", 4), ("profile", "ai-training"), ("seed", 137)]
)
def test_smaller_or_final_generation_cannot_inherit_canonical_scope(
    field: str, value: object
) -> None:
    plan = copy.deepcopy(probe.read(probe.PLAN_PATH))
    plan["generation"][field] = value
    with pytest.raises(ValueError, match="frozen_diagnostic_scope"):
        probe.validate_plan(plan)


def test_probe_cannot_authorize_fits_or_project_campaign() -> None:
    plan = probe.read(probe.PLAN_PATH)
    probe.validate_plan(plan)
    for field in (
        "model_fit_authorized",
        "project_campaign_authorized",
        "final_generation_authorized",
    ):
        changed = copy.deepcopy(plan)
        changed[field] = True
        with pytest.raises(ValueError, match="frozen_diagnostic_scope"):
            probe.validate_plan(changed)


def test_diagnostic_receipt_is_private_and_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    probe.write(path, {"failed": True})
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        probe.write(path, {"failed": False})
    assert probe.read(path) == {"failed": True}


@pytest.mark.parametrize(
    "self_peak,reason",
    [(16 * 1024**3, "worker_self_rss_limit"), (None, "worker_self_rss_measurement_invalid")],
)
def test_system_peak_rejects_phase_even_if_sample_missed_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, self_peak: int | None, reason: str
) -> None:
    # Exercise only supervisory control flow; no source generation or canonical data.
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    plan = probe.read(probe.PLAN_PATH)
    monkeypatch.setattr(probe, "require_remote", lambda: None)
    monkeypatch.setattr(probe, "clean_pin", lambda *a: None)
    monkeypatch.setattr(probe, "git", lambda *a: "a" * 40)
    original_sha = probe.sha
    monkeypatch.setattr(
        probe,
        "sha",
        lambda p: (
            plan["consumer_lock_sha256"]
            if p.name == "uv.lock"
            else plan["exporter_lock_sha256"]
            if p.name == "requirements-parquet.txt"
            else original_sha(p)
        ),
    )
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=10 * 1024**3))
    monkeypatch.setattr(probe.shutil, "disk_usage", lambda p: SimpleNamespace(free=32 * 1024**3))
    calls = []

    def missed_peak(command: list[str], **kwargs: object) -> dict:
        calls.append(command)
        probe.write(output / "generation.json", {"worker_peak_self_rss_bytes": self_peak})
        return {"status": "passed", "reason": None, "sampled_tree_peak_rss_bytes": 1024}

    monkeypatch.setattr(probe, "monitor", missed_peak)
    with pytest.raises(SystemExit) as stopped:
        probe.run(SimpleNamespace(source=source, output=output))
    assert stopped.value.code == 1
    receipt = probe.read(output / "resource.json")
    assert receipt["reason"] == reason
    assert receipt["phases"][0]["status"] == "failed"
    assert receipt["completed_phases"] == 0
    assert len(calls) == 1
    assert receipt["project_journal_initialized"] is False


@pytest.mark.parametrize("change", ["parent_limit", "budget", "prior_digest"])
def test_resource_revision_cannot_change_parent_caps_or_erase_previous_failure(change: str) -> None:
    plan = probe.read(probe.PLAN_PATH)
    if change == "parent_limit":
        plan["parent_limits"]["max_bytes"] *= 2
    elif change == "budget":
        plan["budgets"]["minimum_available_memory_bytes"] = 1
    else:
        plan["previous_attempt"]["plan_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="frozen_diagnostic_scope"):
        probe.validate_plan(plan)


@pytest.mark.parametrize("change", ["producer", "entrypoint", "failure_digest", "failure_flag"])
def test_cached_revision_preserves_exact_producer_and_both_failed_attempts(change: str) -> None:
    plan = probe.read(probe.PLAN_PATH)
    if change == "producer":
        plan["producer_commit"] = "1" * 40
    elif change == "entrypoint":
        plan["generation_entrypoint"] = "data.inventory.run_source_dataset.run"
    elif change == "failure_digest":
        plan["previous_attempt"]["resource_receipt_sha256"] = "0" * 64
    else:
        plan["previous_attempt"]["previous_failure_preserved"] = False
    with pytest.raises(ValueError, match="frozen_diagnostic_scope"):
        probe.validate_plan(plan)
