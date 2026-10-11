"""Real owned-child RSS and failure accounting, without changing other processes."""

import subprocess
import sys
from time import perf_counter
from types import SimpleNamespace

import psutil
import pytest

from retailops_ai.evaluation_campaign import inprocess_memory as module
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost
from retailops_ai.source_snapshot.files import SnapshotError


def test_real_owned_child_is_included_and_artifact_cost_is_retained():
    child = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            "import sys; payload=bytearray(16*1024**2); print('ready',flush=True); sys.stdin.readline()",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        child_rss = psutil.Process(child.pid).memory_info().rss
        handle = SimpleNamespace(cost=None)
        with module.measured_inprocess_operation(handle, perf_counter()) as measured:
            handle.cost = CampaignCost(wall_seconds=0.0, artifact_bytes=123)
        assert measured.samples >= 2 and not measured.failed
        assert handle.cost.peak_process_tree_rss_bytes >= child_rss > 16 * 1024**2
        assert handle.cost.artifact_bytes == 123 and handle.cost.wall_seconds > 0
        assert child.poll() is None  # The sampler observes; it never terminates workers.
    finally:
        child.communicate("done\n", timeout=5)
        assert child.returncode == 0


def test_unavailable_initial_measurement_prevents_operation_and_preserves_unknown(monkeypatch):
    def unavailable():
        raise psutil.AccessDenied()

    monkeypatch.setattr(module, "current_tree_rss_bytes", unavailable)
    handle = SimpleNamespace(cost=None)
    with pytest.raises(SnapshotError, match="measurement_unavailable"):
        with module.measured_inprocess_operation(handle, perf_counter()):
            raise AssertionError("worker must not begin without the initial measurement")
    assert handle.cost.wall_seconds > 0 and handle.cost.peak_process_tree_rss_bytes is None


@pytest.mark.parametrize("work_failed", [False, True])
def test_mid_operation_monitor_failure_blocks_success_and_preserves_primary_error(
    monkeypatch, work_failed
):
    monkeypatch.setattr(module, "current_tree_rss_bytes", lambda: 12345)
    handle = SimpleNamespace(cost=None)
    expected = RuntimeError if work_failed else SnapshotError
    with pytest.raises(expected, match="original_work_failure|measurement_unavailable"):
        with module.measured_inprocess_operation(handle, perf_counter()) as measured:
            handle.cost = CampaignCost(wall_seconds=0.0, artifact_bytes=321)
            measured.failed = True
            if work_failed:
                raise RuntimeError("original_work_failure")
    assert handle.cost.peak_process_tree_rss_bytes is None
    assert handle.cost.artifact_bytes == 321


def test_failed_work_still_has_the_actual_sampled_peak(monkeypatch):
    monkeypatch.setattr(module, "current_tree_rss_bytes", lambda: 34567)
    handle = SimpleNamespace(cost=None)
    with pytest.raises(RuntimeError, match="work_failed"):
        with module.measured_inprocess_operation(handle, perf_counter()):
            raise RuntimeError("work_failed")
    assert handle.cost.peak_process_tree_rss_bytes == 34567
    assert handle.cost.artifact_bytes is None and handle.cost.wall_seconds > 0
