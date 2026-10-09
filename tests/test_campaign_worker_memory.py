"""Memory evidence must survive exec without borrowing its launcher's old RSS."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from retailops_ai import worker_resources
from retailops_ai.anomaly_detectors import store
from retailops_ai.anomaly_detectors import worker_resources as anomaly_resources
from retailops_ai.evaluation_campaign.campaign_anomaly_fit_worker import memory_evidence
from retailops_ai.source_snapshot.files import SnapshotError

PROBES = (
    "retailops_ai.worker_resources",
    "retailops_ai.evaluation_campaign.campaign_generation_worker",
    "measure_ai09_development_capacity",
)


@pytest.mark.parametrize("module", PROBES)
def test_source_probes_need_only_stdlib_in_isolated_interpreter(module):
    root = Path(__file__).resolve().parents[1]
    code = """import importlib,json,sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path[:0] = [str(root/'src'), str(root/'scripts')]
probe = importlib.import_module(sys.argv[2])
peak = probe.worker_peak_rss_bytes()
assert not {'pydantic','numpy','pyarrow','psutil'} & sys.modules.keys()
print(json.dumps({'peak':peak}))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code, str(root), module],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(result.stdout)["peak"] > 0


@pytest.mark.skipif(sys.platform != "linux", reason="Linux pre-exec RSS retention")
@pytest.mark.parametrize("module", PROBES)
def test_actual_producer_and_capacity_probes_exclude_launcher_history(module, record_property):
    root = Path(__file__).resolve().parents[1]
    leaf = """import importlib,json,resource,sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path[:0] = [str(root/'src'), str(root/'scripts')]
probe = importlib.import_module(sys.argv[2])
print(json.dumps({'executable_peak':probe.worker_peak_rss_bytes(),
    'lifetime_peak':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024}))
"""
    launcher = """import subprocess,sys
retained = bytearray(256*1024**2)
subprocess.run([sys.executable,'-I','-S','-c',*sys.argv[1:]],
    check=True,start_new_session=True)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", launcher, leaf, str(root), module],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    measured = json.loads(result.stdout)
    for key, value in measured.items():
        record_property(key, value)
    assert 0 < measured["executable_peak"] < 128 * 1024**2
    assert measured["lifetime_peak"] >= 256 * 1024**2


def test_native_anomaly_error_contract_is_preserved(monkeypatch):
    def unavailable():
        raise ValueError("worker_peak_memory_unavailable")

    monkeypatch.setattr(anomaly_resources, "executable_peak_rss_bytes", unavailable)
    with pytest.raises(SnapshotError, match="anomaly_worker_peak_memory_unavailable"):
        anomaly_resources.worker_peak_rss_bytes()


def test_changed_shared_probe_changes_native_model_runtime_identity(tmp_path, monkeypatch):
    native_files = store.files
    original = store.runtime()
    raw = native_files("retailops_ai").joinpath("worker_resources.py").read_bytes()
    assert (
        original.code_files["retailops_ai/worker_resources.py"] == hashlib.sha256(raw).hexdigest()
    )
    changed = tmp_path / "worker_resources.py"
    changed.write_bytes(raw + b"\n# different executable measurement\n")
    monkeypatch.setattr(
        store, "files", lambda name: tmp_path if name == "retailops_ai" else native_files(name)
    )
    actual = store.runtime()
    assert actual.code_files != original.code_files
    assert (
        actual.code_files["retailops_ai/worker_resources.py"]
        == hashlib.sha256(changed.read_bytes()).hexdigest()
    )


def test_completed_native_child_peak_is_retained_without_adding_the_parent_twice(monkeypatch):
    monkeypatch.setattr(worker_resources, "worker_peak_rss_bytes", lambda: 200 * 1024**2)
    result = {
        "fit_resources": [
            {
                "wall_seconds": 1.0,
                "cpu_seconds": 0.8,
                "peak_rss_bytes": peak * 1024**2,
                "native_max_score_error": 0.0,
            }
            for peak in (300, 400)
        ]
    }
    measured = memory_evidence("fit", result)
    assert measured["worker_peak_rss_bytes"] == 200 * 1024**2
    # Each native receipt already measures the full parent/child tree. Their
    # sequential fits cannot be added together or to this parent's peak again.
    assert measured["conservative_worker_tree_peak_rss_bytes"] == 400 * 1024**2
    assert memory_evidence("reload", {})["conservative_worker_tree_peak_rss_bytes"] == 200 * 1024**2
    assert memory_evidence("fit", {"fit_resources": []}) == memory_evidence("reload", {})


@pytest.mark.parametrize(
    "result",
    [
        {},
        {"fit_resources": None},
        {"fit_resources": [{}]},
        {"fit_resources": [{"peak_rss_bytes": 0}]},
    ],
)
def test_missing_native_child_evidence_cannot_be_replaced_by_parent_memory(result):
    with pytest.raises(ValueError):
        memory_evidence("fit", result)
