"""Real isolated process costs and failure containment; no canonical data generation."""

import copy
import gc
import io
import os
import sys
import threading
import time
import weakref
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


@pytest.mark.parametrize(
    "change", ["producer", "producer_lock", "entrypoint", "failure_digest", "failure_flag"]
)
def test_cached_revision_preserves_exact_producer_and_prior_failed_attempts(change: str) -> None:
    plan = probe.read(probe.PLAN_PATH)
    if change == "producer":
        plan["producer_commit"] = "1" * 40
    elif change == "producer_lock":
        plan["producer_lock_sha256"] = "0" * 64
    elif change == "entrypoint":
        plan["generation_entrypoint"] = "data.inventory.run_source_dataset.run"
    elif change == "failure_digest":
        plan["previous_attempt"]["resource_receipt_sha256"] = "0" * 64
    else:
        plan["previous_attempt"]["previous_failure_preserved"] = False
    with pytest.raises(ValueError, match="frozen_diagnostic_scope"):
        probe.validate_plan(plan)


@pytest.mark.parametrize("change", ["interval", "phase", "locals", "classification"])
def test_stack_observation_cannot_change_scope_or_claim_allocation_measurement(change: str) -> None:
    plan = probe.read(probe.PLAN_PATH)
    if change == "interval":
        plan["worker_stack_observation"]["interval_seconds"] = 1
    elif change == "phase":
        plan["worker_stack_observation"]["phases"] = ["import"]
    elif change == "locals":
        plan["worker_stack_observation"]["locals_dumped"] = True
    else:
        plan["worker_stack_observation"]["periodic_observation_not_allocation_measurement"] = False
    with pytest.raises(ValueError, match="frozen_diagnostic_scope"):
        probe.validate_plan(plan)


def test_all_six_prior_plan_bytes_and_failure_chain_are_retained() -> None:
    path = probe.PLAN_PATH
    for version, run_id in (
        ("1.5", 37765329151),
        ("1.4", 37731200719),
        ("1.3", 37714051649),
        ("1.2", 37676033214),
        ("1.1", 37613368332),
        ("1.0", 37611605538),
    ):
        current = probe.read(path)
        previous = path.with_name(
            "ai09-development-capacity.json"
            if version == "1.0"
            else "ai09-development-capacity-v" + version + ".json"
        )
        assert current["previous_attempt"]["plan_sha256"] == probe.sha(previous)
        assert current["previous_attempt"]["workflow_run"] == run_id
        if version == "1.0":
            assert current["previous_attempt"]["status"] == "failed_tree_rss_limit"
            assert current["previous_attempt"]["completed_phases"] == 0
            assert current["previous_attempt"]["wall_seconds"] > 0
        else:
            assert current["previous_attempt"]["previous_failure_preserved"] is True
        path = previous


def test_actual_periodic_generation_stack_is_bounded_and_cancelled_without_locals(
    tmp_path: Path,
) -> None:
    # Shortened timing applies only to this isolated stdlib control; the frozen
    # full-profile plan above rejects this value and always requires120 seconds.
    program = """
import sys,time
from types import SimpleNamespace
sys.path.insert(0, ROOT)
from scripts import measure_ai09_development_capacity as probe
def controlled_generation(args,plan):
    local_value = 'CONTROL_LOCAL_VALUE_MUST_NOT_APPEAR'
    time.sleep(0.11)
    return {'length':len(local_value)}
probe.producer_worker = controlled_generation
plan = probe.read(probe.PLAN_PATH)
plan['worker_stack_observation']['interval_seconds'] = 0.025
probe.observed_worker(SimpleNamespace(worker='generation'),plan)
print('CONTROL_COMPLETED',flush=True)
time.sleep(0.08)
""".replace("ROOT", repr(str(probe.PLAN_PATH.parents[2])))
    result = run(tmp_path, program)
    assert result["status"] == "passed"
    log = (tmp_path / "worker.log").read_text()
    before, after = log.split("CONTROL_COMPLETED", 1)
    assert "AI09_PYTHON_STACK_SNAPSHOT" in before and "controlled_generation" in before
    assert "CONTROL_LOCAL_VALUE_MUST_NOT_APPEAR" not in log
    assert "AI09_PYTHON_STACK_SNAPSHOT" not in after and len(log.encode()) < 16384


def test_stack_timer_is_cancelled_before_propagating_generation_failure(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(probe.StackObserver, "start", lambda *a, **k: calls.append("start"))
    monkeypatch.setattr(probe.StackObserver, "stop", lambda *a: calls.append("cancel"))

    def failed(*args):
        raise ValueError("controlled_generation_failure")

    monkeypatch.setattr(probe, "producer_worker", failed)
    with pytest.raises(ValueError, match="controlled_generation_failure"):
        probe.observed_worker(SimpleNamespace(worker="generation"), probe.read(probe.PLAN_PATH))
    assert calls == ["start", "cancel"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("implementation", "native_watchdog"),
        ("maximum_threads", 65),
        ("maximum_frames_per_thread", 65),
        ("maximum_snapshot_bytes", 65537),
        ("maximum_filename_characters", 257),
        ("maximum_function_characters", 97),
        ("join_timeout_seconds", 6),
        ("may_be_delayed_without_GIL", False),
        ("truncation_explicit", False),
    ],
)
def test_python_observation_caps_and_limitations_are_frozen(field, value) -> None:
    plan = probe.read(probe.PLAN_PATH)
    plan["worker_stack_observation"][field] = value
    with pytest.raises(ValueError, match="frozen_diagnostic_scope"):
        probe.validate_plan(plan)


def test_actual_python_observer_handles_churning_code_and_threads_without_locals(tmp_path) -> None:
    program = """
import sys, threading, time, types
sys.path.insert(0, ROOT)
from scripts import measure_ai09_development_capacity as probe
def template():
    local_value = 'DYNAMIC_LOCAL_VALUE_MUST_NOT_APPEAR'
    return len(local_value)
stop = threading.Event()
def churn():
    while not stop.is_set():
        for _ in range(100):
            function = types.FunctionType(template.__code__.replace(), {})
            function()
            function = None
policy = probe.read(probe.PLAN_PATH)['worker_stack_observation']
policy['interval_seconds'] = 0.002
observer = probe.StackObserver(policy, sys.stderr)
worker = threading.Thread(target=churn)
worker.start()
observer.start()
try:
    for _ in range(12):
        temporary = threading.Thread(target=lambda: time.sleep(0.002))
        temporary.start()
        temporary.join()
finally:
    stop.set()
    worker.join()
    observer.stop()
assert observer.snapshots > 1
print('CONTROL_COMPLETED', flush=True)
time.sleep(0.03)
""".replace("ROOT", repr(str(probe.PLAN_PATH.parents[2])))
    result = run(tmp_path, program)
    assert result["status"] == "passed"
    before, after = (tmp_path / "worker.log").read_text().split("CONTROL_COMPLETED", 1)
    assert before.count("AI09_PYTHON_STACK_SNAPSHOT") > 1
    assert "churn" in before and '"truncated":false' in before
    assert "DYNAMIC_LOCAL_VALUE_MUST_NOT_APPEAR" not in before
    assert "AI09_PYTHON_STACK_SNAPSHOT" not in after


def test_frame_depth_and_bytes_truncation_explicit_and_payload_references_released() -> None:
    ready, release = threading.Event(), threading.Event()
    references = []

    class Payload:
        pass

    def nested(depth):
        if depth:
            nested(depth - 1)
        else:
            local_payload = Payload()
            references.append(weakref.ref(local_payload))
            ready.set()
            release.wait(timeout=5)

    worker = threading.Thread(target=nested, args=(80,))
    worker.start()
    try:
        assert ready.wait(timeout=2)
        policy = probe.read(probe.PLAN_PATH)["worker_stack_observation"]
        observer = probe.StackObserver(policy, io.StringIO())
        snapshot = observer._snapshot()
        assert '"truncated":true' in snapshot
        assert 0 < snapshot.count('"function":"nested"') <= 64
        assert len(snapshot.encode()) <= 65536
        small = dict(policy, maximum_snapshot_bytes=512)
        short = probe.StackObserver(small, io.StringIO())._snapshot()
        assert len(short.encode()) <= 512 and '"truncated":true' in short
    finally:
        release.set()
        worker.join(timeout=2)
    assert not worker.is_alive()
    gc.collect()
    assert references[0]() is None


def test_observer_errors_cannot_return_successful_worker_result(monkeypatch) -> None:
    class BrokenStream(io.StringIO):
        def write(self, value):
            raise OSError("ARBITRARY_ERROR_PAYLOAD_MUST_NOT_APPEAR")

    monkeypatch.setattr(probe.sys, "stderr", BrokenStream())
    policy = probe.read(probe.PLAN_PATH)
    policy["worker_stack_observation"]["interval_seconds"] = 0.001

    def wait_for_observation(*args):
        time.sleep(0.02)
        return {"status": "passed"}

    monkeypatch.setattr(probe, "producer_worker", wait_for_observation)
    with pytest.raises(ValueError, match="capacity_stack_observation_failure_OSError"):
        probe.observed_worker(SimpleNamespace(worker="generation"), policy)


def test_successful_worker_reports_observer_shutdown_and_cost_metadata(monkeypatch) -> None:
    monkeypatch.setattr(probe.sys, "stderr", io.StringIO())
    policy = probe.read(probe.PLAN_PATH)
    policy["worker_stack_observation"]["interval_seconds"] = 0.001
    monkeypatch.setattr(
        probe, "producer_worker", lambda *a: time.sleep(0.02) or {"status": "passed"}
    )
    result = probe.observed_worker(SimpleNamespace(worker="generation"), policy)
    assert result["worker_stack_observation"]["snapshots"] > 1
    assert result["worker_stack_observation"]["cancelled_and_joined"] is True
    assert result["worker_stack_observation"]["not_continuous_or_allocation_measurement"] is True
    assert not any(t.name == "ai09-stack-observer" for t in threading.enumerate())


def test_observer_thread_cap_is_explicit_and_only_emits_owned_thread_frames(monkeypatch) -> None:
    frame = sys._getframe()
    monkeypatch.setattr(probe.sys, "_current_frames", lambda: {i: frame for i in range(65)})
    policy = probe.read(probe.PLAN_PATH)["worker_stack_observation"]
    snapshot = probe.StackObserver(policy, io.StringIO())._snapshot()
    # Controlled identifiers exercise the cap; no foreign process is inspected.
    assert snapshot.count('"thread":') <= 64
    assert '"threads_seen":65' in snapshot and '"truncated":true' in snapshot
    assert len(snapshot.encode()) <= 65536


def test_observer_join_timeout_cannot_return_success(monkeypatch) -> None:
    observer = probe.StackObserver(
        probe.read(probe.PLAN_PATH)["worker_stack_observation"], io.StringIO()
    )
    monkeypatch.setattr(observer.thread, "join", lambda **k: None)
    monkeypatch.setattr(observer.thread, "is_alive", lambda: True)
    with pytest.raises(ValueError, match="capacity_stack_observer_join_timeout"):
        observer.stop()
    assert observer.done.is_set()


def test_unicode_code_metadata_truncation_is_explicit_and_encoded_bytes_are_bounded(
    monkeypatch,
) -> None:
    frames = {
        0: SimpleNamespace(
            f_code=SimpleNamespace(co_filename="ą" * 300, co_name="ź" * 150),
            f_lineno=42,
            f_back=None,
        )
    }
    monkeypatch.setattr(probe.sys, "_current_frames", lambda: frames)
    policy = probe.read(probe.PLAN_PATH)["worker_stack_observation"]
    snapshot = probe.StackObserver(policy, io.StringIO())._snapshot()
    assert '"truncated":true' in snapshot and len(snapshot.encode()) <= 65536
    assert snapshot.count("\\u0105") == 256 and snapshot.count("\\u017a") == 96
    assert frames == {}


@pytest.mark.parametrize("change", ["signal", "reason", "cost", "peak", "completed"])
def test_fifth_failed_cost_and_rss_stop_cannot_be_rewritten(change) -> None:
    plan = probe.read(probe.PLAN_PATH)
    if change == "signal":
        plan["previous_attempt"]["exit_code"] = -11
    elif change == "reason":
        plan["previous_attempt"]["reason"] = "worker_exit"
    elif change == "cost":
        plan["previous_attempt"]["wall_seconds"] = 0
    elif change == "peak":
        plan["previous_attempt"]["sampled_tree_peak_rss_bytes"] = 0
    else:
        plan["previous_attempt"]["completed_phases"] = 1
    with pytest.raises(ValueError, match="frozen_diagnostic_scope"):
        probe.validate_plan(plan)
