"""Resource refusal, fail-closed receipts and cleanup of an owned process group."""

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

SPEC = importlib.util.spec_from_file_location(
    "stockout_resource_probe", Path(__file__).parents[1] / "scripts/measure_stockout_pipeline.py"
)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_concurrently_removed_scratch_directories_do_not_crash_monitor(tmp_path):
    done = threading.Event()
    (tmp_path / "stable").write_bytes(b"keep")
    errors = []

    def writer():
        try:
            for i in range(150):
                directory = tmp_path / ("temporary-" + str(i))
                directory.mkdir()
                (directory / "payload").write_bytes(b"transient")
                time.sleep(0.0005)
                shutil.rmtree(directory)
        except BaseException as error:
            errors.append(error)
        finally:
            done.set()

    worker = threading.Thread(target=writer)
    worker.start()
    try:
        while not done.is_set():
            observed = probe.tree_size(tmp_path)
            assert observed["logical_bytes"] >= 4
    finally:
        worker.join(timeout=5)
    assert not errors
    assert probe.tree_size(tmp_path)["logical_bytes"] == 4


def test_permission_error_is_not_silently_counted_as_empty_scratch(tmp_path, monkeypatch):
    def walk(root, onerror, **kwargs):
        onerror(PermissionError("cannot measure scratch"))
        return []

    monkeypatch.setattr(probe.os, "walk", walk)
    with pytest.raises(PermissionError):
        probe.tree_size(tmp_path)


def signed(document, key, prefix):
    from retailops_ai.source_snapshot.files import json_sha256

    document[key] = prefix + json_sha256(document["descriptor"])
    return document


@pytest.fixture
def copies():
    label = dict(
        descriptor=dict(
            source_dataset_id="same",
            policy="frozen",
            points_sha256="same",
            input_seal=dict(
                snapshot_manifest_sha256="a" * 64,
                tables=[dict(rows=1, content_sha256="same")],
                windows_sha256="same",
            ),
        ),
        report=dict(rows=1),
    )
    signed(label, "label_bundle_id", "label-partitions-sha256-")
    temporal = dict(
        descriptor=dict(
            parents=dict(
                feature_bundle_id="feature",
                upstream_bundle_id="upstream",
                label_bundle_id=label["label_bundle_id"],
            ),
            rows_sha256="same",
            policy="frozen",
            input_seal=dict(
                parent_files_sha256="a" * 64,
                indexed_payloads_sha256="same",
                rows=dict(features=1, labels=1, upstream=1),
            ),
        ),
        report=dict(final_test_outcomes_evaluated=False),
    )
    signed(temporal, "temporal_bundle_id", "temporal-partitions-sha256-")
    result = dict(
        descriptor=dict(
            parents=dict(
                **temporal["descriptor"]["parents"],
                temporal_bundle_id=temporal["temporal_bundle_id"],
                source_dataset_id="source",
                curated_dataset_id="curated",
            ),
            development_labels="same",
            policy="frozen",
        ),
        pipelines=dict(model="same"),
        results=dict(brier=0.1),
        report=dict(model_ready=False),
    )
    signed(result, "development_id", "development-sha256-")
    new_result, new_label, new_temporal = deepcopy((result, label, temporal))
    new_label["descriptor"]["input_seal"]["snapshot_manifest_sha256"] = "b" * 64
    new_temporal["descriptor"]["input_seal"]["parent_files_sha256"] = "b" * 64
    return result, label, temporal, new_result, new_label, new_temporal


def reseal(result, label, temporal):
    signed(label, "label_bundle_id", "label-partitions-sha256-")
    temporal["descriptor"]["parents"]["label_bundle_id"] = label["label_bundle_id"]
    signed(temporal, "temporal_bundle_id", "temporal-partitions-sha256-")
    result["descriptor"]["parents"].update(
        label_bundle_id=label["label_bundle_id"], temporal_bundle_id=temporal["temporal_bundle_id"]
    )
    signed(result, "development_id", "development-sha256-")


def test_regeneration_retains_real_new_ids_with_equal_semantics(copies):
    old, old_label, old_temporal, actual, label, temporal = copies
    reseal(actual, label, temporal)
    before = deepcopy((actual, label, temporal))
    result = probe.smoke_parity(actual, old, label, old_label, temporal, old_temporal)
    assert result["semantic_equal"]
    assert not result["development_id_equal"]
    assert result["actual_parent_ids"]["label_bundle_id"] == label["label_bundle_id"]
    assert (actual, label, temporal) == before


@pytest.mark.parametrize(
    "change,gate",
    [
        ("label", "label_semantics_equal"),
        ("outcomes", "label_content_seal_equal"),
        ("payload", "temporal_indexed_content_seal_equal"),
        ("membership", "temporal_semantics_equal"),
        ("metrics", "results_equal"),
        ("model", "models_equal"),
        ("parent", "unchanged_parent_ids_equal"),
    ],
)
def test_semantic_change_is_rejected_even_with_valid_new_ids(copies, change, gate):
    old, old_label, old_temporal, actual, label, temporal = copies
    if change == "label":
        label["descriptor"]["policy"] = "changed"
    elif change == "outcomes":
        label["descriptor"]["input_seal"]["windows_sha256"] = "changed"
    elif change == "payload":
        temporal["descriptor"]["input_seal"]["indexed_payloads_sha256"] = "changed"
    elif change == "membership":
        temporal["descriptor"]["rows_sha256"] = "changed"
    elif change == "metrics":
        actual["results"]["brier"] = 0.0
    elif change == "model":
        actual["pipelines"]["model"] = "changed"
    else:
        actual["descriptor"]["parents"]["feature_bundle_id"] = "changed"
    reseal(actual, label, temporal)
    result = probe.smoke_parity(actual, old, label, old_label, temporal, old_temporal)
    assert result["checks"]["identities_valid"]
    assert not result["checks"][gate]
    assert not result["semantic_equal"]


def test_old_label_id_cannot_be_substituted_into_new_parent_chain(copies):
    old, old_label, old_temporal, actual, label, temporal = copies
    reseal(actual, label, temporal)
    actual["descriptor"]["parents"]["label_bundle_id"] = old_label["label_bundle_id"]
    signed(actual, "development_id", "development-sha256-")
    result = probe.smoke_parity(actual, old, label, old_label, temporal, old_temporal)
    assert not result["checks"]["actual_new_parents_bound"]
    assert not result["semantic_equal"]


def test_owned_scratch_keeps_successful_pilot_and_cleans_failure(tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    with probe.owned_scratch(tmp_path, retain=True) as (accepted, state):
        (accepted / "data").write_bytes(b"accepted")
        state["success"] = True
    assert (accepted / "data").read_bytes() == b"accepted"
    with pytest.raises(ValueError), probe.owned_scratch(tmp_path, retain=True) as (failed, _):
        raise ValueError("failure")
    assert not failed.exists()
    assert existing.exists()


@pytest.fixture
def baseline():
    return dict(
        status="passed",
        consumer=dict(
            parity=dict(semantic_equal=True),
            input_stats=dict(
                private=dict(rows=88906, bytes=5318059, ledger_rows=7070),
                qualification_bytes=927742,
            ),
        ),
        producer=dict(effective_config=dict(days=102, products=8, warehouses=2, stores=3)),
        budgets=dict(
            wall_seconds=600,
            tree_rss_bytes=1024**3,
            scratch_bytes=512 * 1024**2,
            minimum_free_bytes=50 * 1024**3,
        ),
        measurement=dict(
            wall_seconds=450,
            sampled_tree_peak_rss_bytes=433 * 1024**2,
            sampled_peak_scratch_logical_bytes=160 * 1024**2,
            sampled_peak_scratch_allocated_bytes=166 * 1024**2,
            minimum_free_bytes=53 * 1024**3,
        ),
    )


def profile(name="stockout-resource-pilot-1.1.json"):
    return json.loads((Path(__file__).parents[1] / "docs/reference" / name).read_text())


def test_preflight_rejects_4480_origins_and_only_permits_smaller_attempt(baseline):
    large = probe.pilot_preflight(profile("stockout-resource-pilot-1.json"), baseline, 53 * 1024**3)
    assert not large["ready_to_attempt"]
    assert not large["checks"]["tree_rss_bytes"]
    small = probe.pilot_preflight(profile(), baseline, 53 * 1024**3)
    assert small["ready_to_attempt"]
    assert not small["larger_profile_qualified"]
    assert not small["quality_qualified"]


def test_preflight_preserves_50gib_plus_declared_scratch(baseline):
    result = probe.pilot_preflight(profile(), baseline, 52 * 1024**3 - 1)
    assert not result["ready_to_attempt"]
    assert not result["checks"]["free_disk"]


def qualified_pilot(baseline):
    baseline.update(
        whole_pilot_budget_passed=True,
        larger_profile_qualified=True,
        producer_commit=probe.PRODUCER_COMMIT,
        consumer_commit=probe.CONSUMER_COMMIT,
        final_test_outcomes_evaluated=False,
        model_promoted=False,
        phases=[dict(phase="producer", exit_code=0), dict(phase="consumer", exit_code=0)],
    )
    baseline["consumer"].update(parity=None, final_test_outcomes_evaluated=False)
    return baseline


def test_actual_qualified_pilot_can_supply_new_resource_estimate(baseline):
    result = probe.pilot_preflight(profile(), qualified_pilot(baseline), 53 * 1024**3)
    assert result["ready_to_attempt"]
    assert result["measured_baseline"] == "qualified_intermediate_pilot"
    assert not result["quality_qualified"]


@pytest.mark.parametrize(
    "change", ["phase", "pin", "test", "promotion", "resource", "qualification"]
)
def test_incomplete_or_changed_pilot_cannot_authorize_generation(baseline, change):
    qualified_pilot(baseline)
    if change == "phase":
        baseline["phases"][1]["exit_code"] = 1
    elif change == "pin":
        baseline["producer_commit"] = "0" * 40
    elif change == "test":
        baseline["consumer"]["final_test_outcomes_evaluated"] = True
    elif change == "promotion":
        baseline["model_promoted"] = True
    elif change == "resource":
        baseline["measurement"]["sampled_tree_peak_rss_bytes"] = 1024**3 + 1
    else:
        baseline["larger_profile_qualified"] = False
    with pytest.raises(ValueError, match="qualified_smoke"):
        probe.pilot_preflight(profile(), baseline, 53 * 1024**3)


@pytest.mark.parametrize("change", ["failed", "semantics", "over_budget"])
def test_unqualified_smoke_cannot_authorize_larger_generation(baseline, change):
    if change == "failed":
        baseline["status"] = "failed"
    elif change == "semantics":
        baseline["consumer"]["parity"]["semantic_equal"] = False
    else:
        baseline["measurement"]["sampled_tree_peak_rss_bytes"] = 1024**3 + 1
    with pytest.raises(ValueError, match="qualified_smoke"):
        probe.pilot_preflight(profile(), baseline, 53 * 1024**3)


@pytest.mark.parametrize("change", ["boolean", "dates", "grid", "reserve", "memory"])
def test_bad_config_or_relaxed_limits_are_refused_before_generation(baseline, change):
    candidate = profile()
    if change == "boolean":
        candidate["generation"]["products"] = True
    elif change == "dates":
        candidate["generation"]["start_date"] = "2026-04-20"
    elif change == "grid":
        candidate["generation"]["max_daily_rows"] = 1
    elif change == "reserve":
        candidate["budgets"]["minimum_free_bytes"] = 49 * 1024**3
    else:
        candidate["budgets"]["tree_rss_bytes"] = 2 * 1024**3
    with pytest.raises(ValueError):
        probe.pilot_preflight(candidate, baseline, 53 * 1024**3)


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    spawned, stopped = [], []
    monkeypatch.setattr(probe.subprocess, "check_output", lambda *a, **kw: probe.PRODUCER_COMMIT)
    monkeypatch.setattr(probe.subprocess, "check_call", lambda *a, **kw: 0)
    monkeypatch.setattr(
        probe.psutil,
        "Process",
        lambda: SimpleNamespace(
            children=lambda **kw: [], memory_info=lambda: SimpleNamespace(rss=1)
        ),
    )
    monkeypatch.setattr(
        probe.shutil,
        "disk_usage",
        lambda p: SimpleNamespace(free=probe.FREE_BYTES + probe.SCRATCH_BYTES + 1024),
    )

    class Child:
        pid = -1  # The stub is never passed to a signal API.
        returncode = 0

        def __init__(self, *args, **kwargs):
            spawned.append(kwargs["cwd"])
            (kwargs["cwd"] / "payload.bin").write_bytes(b"simulated output")

        def poll(self):
            return self.returncode

        def wait(self, **kwargs):
            return self.returncode

    monkeypatch.setattr(probe.subprocess, "Popen", Child)

    def stop(child):
        stopped.append(child)
        child.returncode = -15

    monkeypatch.setattr(probe, "stop_owned", stop)
    return tmp_path / "producer", tmp_path / "receipt.json", spawned, stopped


def test_disk_preflight_refuses_without_starting_any_process(isolated, monkeypatch):
    producer, receipt, spawned, _ = isolated
    monkeypatch.setattr(
        probe.shutil,
        "disk_usage",
        lambda p: SimpleNamespace(free=probe.FREE_BYTES + probe.SCRATCH_BYTES - 1),
    )
    with pytest.raises(ValueError, match="insufficient_free_space"):
        probe.run_probe(producer, receipt)
    assert not spawned
    assert not receipt.exists()


def test_zero_exit_without_complete_receipts_is_not_passed(isolated):
    producer, receipt, spawned, stopped = isolated
    assert probe.run_probe(producer, receipt) == 1
    document = json.loads(receipt.read_text())
    assert document["failure"] == "whole_pipeline_receipt_missing"
    assert not document["whole_smoke_budget_passed"]
    assert not document["larger_profile_qualified"]
    assert not document["final_test_outcomes_evaluated"]
    assert len(spawned) == 2
    assert not stopped
    assert all(not directory.exists() for directory in spawned)


@pytest.mark.parametrize("gate", ["rss", "scratch", "disk", "wall"])
def test_budget_violation_stops_before_consumer_and_removes_only_own_scratch(
    isolated, monkeypatch, gate
):
    producer, receipt, spawned, stopped = isolated
    unrelated = receipt.parent / "keep-existing-input"
    unrelated.write_bytes(b"immutable")
    if gate == "rss":
        monkeypatch.setattr(probe, "RSS_BYTES", 0)
        failure = "whole_pipeline_rss_budget_exceeded"
    elif gate == "scratch":
        monkeypatch.setattr(probe, "SCRATCH_BYTES", 1)
        failure = "whole_pipeline_scratch_budget_exceeded"
    elif gate == "wall":
        monkeypatch.setattr(probe, "WALL_SECONDS", -1)
        failure = "whole_pipeline_wall_budget_exceeded"
    else:
        calls = []

        def disk(path):
            calls.append(path)
            return SimpleNamespace(
                free=probe.FREE_BYTES + probe.SCRATCH_BYTES + 1
                if len(calls) == 1
                else probe.FREE_BYTES - 1
            )

        monkeypatch.setattr(probe.shutil, "disk_usage", disk)
        failure = "whole_pipeline_free_disk_reserve_exceeded"
    assert probe.run_probe(producer, receipt) == 1
    document = json.loads(receipt.read_text())
    assert document["failure"] == failure
    assert len(spawned) == len(stopped) == 1
    assert not spawned[0].exists()
    assert unrelated.read_bytes() == b"immutable"
    assert not document["ai08_ready"]


def test_stop_owned_kills_descendant_that_ignores_term_when_leader_exits():
    grandchild = (
        "import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "print('ready',flush=True);time.sleep(60)"
    )
    leader = (
        "import subprocess,sys,time;"
        f"p=subprocess.Popen([sys.executable,'-c',{grandchild!r}],stdout=subprocess.PIPE,text=True);"
        "p.stdout.readline();print(p.pid,flush=True);time.sleep(60)"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", leader], stdout=subprocess.PIPE, text=True, start_new_session=True
    )
    try:
        assert child.stdout is not None
        pid = int(child.stdout.readline())
        probe.stop_owned(child)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                if psutil.Process(pid).status() == psutil.STATUS_ZOMBIE:
                    break
            except psutil.NoSuchProcess:
                break
            time.sleep(0.02)
        else:
            pytest.fail("owned descendant survived cleanup")
        assert child.poll() is not None
    finally:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=5)
        if child.stdout:
            child.stdout.close()
