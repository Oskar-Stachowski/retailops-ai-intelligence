"""Actual subprocess streaming and failed measurement retention, not a Project run."""

import io
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from scripts.ai09_live_progress import LiveProgress
from scripts.measure_ai09_development_capacity import monitor


class Stream(io.StringIO):
    def __init__(self):
        super().__init__()
        self.visible = threading.Event()

    def flush(self):
        if "source_event" in self.getvalue():
            self.visible.set()
        super().flush()


def event(sequence=1, **updates):
    return {
        "version": "source-progress-1.0.0",
        "sequence": sequence,
        "at_utc": "2026-10-09T00:00:00+00:00",
        "elapsed_seconds": 0.0,
        "stage": "controlled_generation",
        "event": "progress",
        "active_stages": ["controlled_generation"],
        "completed": 3,
        "total": 9,
        "unit": "control_records",
        **updates,
    }


def execute(
    tmp_path,
    stream,
    *,
    seconds=4,
    worker_seconds=0.5,
    value=None,
    progress_class=LiveProgress,
    newline=True,
):
    started = time.perf_counter()
    progress = progress_class(
        "generation",
        started=started,
        budget_seconds=4,
        artifact=tmp_path / "progress.jsonl",
        interval_seconds=0.05,
        stream=stream,
    )
    payload = "RETAILOPS_PROGRESS " + json.dumps(value or event())
    program = (
        "import time;print("
        + repr(payload)
        + ",flush=True,end="
        + repr("\n" if newline else "")
        + ");time.sleep("
        + str(worker_seconds)
        + ")"
    )
    return monitor(
        [sys.executable, "-c", program],
        cwd=tmp_path,
        env=dict(os.environ),
        log=tmp_path / "worker.log",
        roots=(tmp_path,),
        budgets={
            "tree_rss_bytes": 1024**3,
            "scratch_bytes": 1024**2,
            "minimum_free_disk_bytes": 1,
            "minimum_available_memory_bytes": 1,
            "sample_seconds": 0.01,
        },
        deadline=started + seconds,
        live_progress=progress,
    )


def test_actual_source_progress_is_flushed_before_child_finishes(tmp_path):
    stream = Stream()
    result = []
    thread = threading.Thread(target=lambda: result.append(execute(tmp_path, stream)))
    thread.start()
    try:
        assert stream.visible.wait(2)
        assert thread.is_alive()
        assert "control_records=3/9" in stream.getvalue()
        assert not result
    finally:
        thread.join(5)
    assert result[0]["status"] == "passed"
    saved = [json.loads(line) for line in (tmp_path / "progress.jsonl").read_text().splitlines()]
    heartbeats = [row for row in saved if row["kind"] == "heartbeat"]
    assert len(heartbeats) >= 2
    assert all(row["heartbeat_is_not_work_progress"] for row in heartbeats)
    assert all(
        row.get("last_reported_counter", {}).get("completed", 3) == 3
        for row in heartbeats
        if row.get("last_reported_counter") is not None
    )
    assert saved[-1]["kind"] == "phase_finished" and saved[-1]["status"] == "passed"
    assert (tmp_path / "progress.jsonl").stat().st_mode & 0o777 == 0o600


def test_timeout_preserves_live_counters_log_and_failed_resource_cost(tmp_path):
    result = execute(tmp_path, Stream(), seconds=0.25, worker_seconds=10)
    assert result["status"] == "failed" and result["reason"] == "wall_limit"
    assert result["samples"] > 1 and result["wall_seconds"] > 0
    assert "RETAILOPS_PROGRESS" in (tmp_path / "worker.log").read_text()
    saved = [json.loads(line) for line in (tmp_path / "progress.jsonl").read_text().splitlines()]
    assert any(row["kind"] == "source_event" for row in saved)
    assert saved[-1]["status"] == "failed" and saved[-1]["reason"] == "wall_limit"


def test_partial_lines_wait_for_real_completion_without_duplicate_or_invented_counter(tmp_path):
    stream = Stream()
    progress = LiveProgress(
        "generation",
        started=time.perf_counter(),
        budget_seconds=5,
        artifact=tmp_path / "progress.jsonl",
        stream=stream,
    )
    log = tmp_path / "worker.log"
    raw = ("RETAILOPS_PROGRESS " + json.dumps(event()) + "\n").encode()
    log.write_bytes(raw[:20])
    progress.observe(log, rss_bytes=10, cpu_seconds=0)
    assert not stream.visible.is_set()
    with log.open("ab") as output:
        output.write(raw[20:])
    progress.observe(log, rss_bytes=11, cpu_seconds=0.1)
    assert stream.visible.is_set()
    progress.observe(log, rss_bytes=12, cpu_seconds=0.2)
    progress.finish("passed", None)
    saved = [json.loads(line) for line in (tmp_path / "progress.jsonl").read_text().splitlines()]
    assert sum(row["kind"] == "source_event" for row in saved) == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"completed": -1},
        {"sequence": 0},
        {"private": "not-for-live-output"},
        {"completed": 10},
        {"elapsed_seconds": float("nan")},
        {"completed_business_days": 366, "total_business_days": 365},
        {"known_queued_remaining": -1},
        {"business_date": 17},
        {"stage": "newline\n::error::injected"},
        {"event": "completed", "completed": "3"},
    ],
)
def test_invalid_progress_is_rejected_and_failure_cost_is_retained(tmp_path, updates):
    stream = Stream()
    result = execute(tmp_path, stream, value=event(**updates))
    assert result["status"] == "failed" and result["samples"] >= 1
    assert "not-for-live-output" not in stream.getvalue()
    assert "not-for-live-output" not in (tmp_path / "progress.jsonl").read_text()


def test_telemetry_failure_does_not_hide_timeout_cost_or_reason(tmp_path):
    class FailedFinish(LiveProgress):
        def finish(self, status, reason):
            self.artifact.close()
            raise OSError("controlled output failure")

    result = execute(
        tmp_path, Stream(), seconds=0.1, worker_seconds=10, progress_class=FailedFinish
    )
    assert result["status"] == "failed" and result["reason"] == "wall_limit"
    assert result["telemetry_errors"] == ["live_progress_error_OSError"]
    assert result["wall_seconds"] > 0 and result["samples"] > 1


@pytest.mark.parametrize("timeout", [False, True])
def test_unfinished_source_event_is_preserved_and_cannot_pass(tmp_path, timeout):
    result = execute(
        tmp_path,
        Stream(),
        seconds=0.1 if timeout else 4,
        worker_seconds=10 if timeout else 0.05,
        newline=False,
    )
    assert result["status"] == "failed"
    if timeout:
        assert result["reason"] == "wall_limit"
    else:
        assert result["reason"] == "live_progress_error_ValueError"
    saved = [json.loads(line) for line in (tmp_path / "progress.jsonl").read_text().splitlines()]
    assert saved[-1]["status"] == "failed" and saved[-1]["incomplete_source_event"]
    assert (tmp_path / "worker.log").read_text().startswith("RETAILOPS_PROGRESS ")


def test_truncated_log_is_rejected_without_repeating_earlier_counts(tmp_path):
    progress = LiveProgress(
        "generation",
        started=time.perf_counter(),
        budget_seconds=5,
        artifact=tmp_path / "progress.jsonl",
        stream=Stream(),
    )
    log = tmp_path / "worker.log"
    log.write_text("RETAILOPS_PROGRESS " + json.dumps(event()) + "\n")
    progress.observe(log, rss_bytes=1, cpu_seconds=0)
    log.write_bytes(b"")
    try:
        with pytest.raises(ValueError, match="log_truncated"):
            progress.observe(log, rss_bytes=1, cpu_seconds=0)
    finally:
        progress.finish("failed", "log_truncated")


def test_frozen_wall_budget_and_preparation_history_are_preserved():
    from scripts.measure_ai09_development_capacity import PLAN_PATH, read, validate_plan

    plan = read(PLAN_PATH)
    validate_plan(plan)
    assert plan["budgets"]["wall_seconds"] == 10800
    assert plan["budgets"]["tree_rss_bytes"] == 12 * 1024**3
    assert plan["superseded_preparation"]["workflow_dispatched"] is False
    assert plan["live_progress"]["actions_live_visibility_control_required"] is True


def test_pending_preparation_cannot_start_canonical_source_io(monkeypatch, tmp_path):
    from scripts import measure_ai09_development_capacity as probe

    monkeypatch.setattr(probe, "require_remote", lambda: None)
    with pytest.raises(ValueError, match="capacity_preparation_not_accepted"):
        probe.run(SimpleNamespace(source=tmp_path / "missing_source", output=tmp_path / "output"))
    assert not (tmp_path / "output").exists()
    plan = probe.read(probe.PLAN_PATH)
    plan["dispatch_enabled"] = True
    plan["preparation_pending"] = []
    with pytest.raises(ValueError, match="frozen_diagnostic_scope"):
        probe.validate_plan(plan)


@pytest.mark.parametrize("fail", [False, True])
def test_producer_reporting_context_wraps_actual_worker_and_exits_on_failure(monkeypatch, fail):
    from scripts import measure_ai09_development_capacity as probe

    calls = []

    @contextmanager
    def reporting(stream, *, interval_seconds):
        assert interval_seconds == 60
        calls.append("reporting_start")
        try:
            yield
        finally:
            calls.append("reporting_stop")

    def import_progress(name):
        assert name == "data.generator.progress"
        return SimpleNamespace(reporting=reporting)

    def worker(args, plan):
        assert calls == ["reporting_start"]
        calls.append("actual_worker")
        if fail:
            raise ValueError("controlled_failure")
        return {"status": "passed"}

    monkeypatch.setattr(probe.importlib, "import_module", import_progress)
    monkeypatch.setattr(probe, "producer_worker", worker)
    plan = probe.read(probe.PLAN_PATH)
    # Qualification is a real producer phase with no separate Python stack observer.
    args = SimpleNamespace(worker="qualification", source=probe.PLAN_PATH.parents[2])
    if fail:
        with pytest.raises(ValueError, match="controlled_failure"):
            probe.observed_worker(args, plan)
    else:
        assert probe.observed_worker(args, plan)["status"] == "passed"
    assert calls == ["reporting_start", "actual_worker", "reporting_stop"]


@pytest.mark.parametrize("seconds", [3600, 10799, 10801])
def test_wall_budget_cannot_drift_from_authorized_180_minutes(seconds):
    from scripts.measure_ai09_development_capacity import PLAN_PATH, read, validate_plan

    plan = read(PLAN_PATH)
    plan["budgets"]["wall_seconds"] = seconds
    with pytest.raises(ValueError, match="frozen_diagnostic_scope"):
        validate_plan(plan)
