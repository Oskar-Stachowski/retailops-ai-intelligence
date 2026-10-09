"""Reject missing worker evidence and distinguish an executable from its launcher."""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from retailops_ai import worker_resources as resources


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"VmHWM: 0 kB\n",
        b"VmHWM: 2 MB\n",
        b"VmHWM: -2 kB\n",
        b"VmHWM: 2 kB\nVmHWM: 2 kB\n",
        b"VmHWM: 2 kB\n" + b"x" * 65536,
    ],
)
def test_missing_or_ambiguous_peak_refuses_receipt(raw, monkeypatch):
    monkeypatch.setattr(resources.sys, "platform", "linux")
    monkeypatch.setattr(resources.Path, "open", lambda *a, **k: io.BytesIO(raw))
    with pytest.raises(ValueError, match="worker_peak_memory_unavailable"):
        resources.worker_peak_rss_bytes()


def test_current_image_peak_uses_kib_not_decimal_kilobytes(monkeypatch):
    monkeypatch.setattr(resources.sys, "platform", "linux")
    monkeypatch.setattr(resources.Path, "open", lambda *a, **k: io.BytesIO(b"VmHWM: 123 kB\n"))
    assert resources.worker_peak_rss_bytes() == 123 * 1024


@pytest.mark.skipif(sys.platform != "linux", reason="Linux exec memory accounting regression")
def test_real_exec_does_not_add_launcher_memory_to_current_worker(tmp_path, record_property):
    # A fresh launcher keeps this control independent of pytest's retained data.
    leaf = (
        "import json,resource; "
        "from retailops_ai.anomaly_detectors.worker_resources import worker_peak_rss_bytes; "
        "print(json.dumps({'current_image_peak':worker_peak_rss_bytes(),"
        "'lifetime_peak':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*1024}))"
    )
    launcher = (
        "import subprocess,sys; "
        "retained=bytearray(256*1024**2); "
        "subprocess.run([sys.executable,'-c',sys.argv[1]],check=True,start_new_session=True)"
    )
    result = subprocess.run(
        [sys.executable, "-c", launcher, leaf],
        env={**os.environ, "PYTHONPATH": str(Path(resources.__file__).parents[1])},
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    measured = json.loads(result.stdout)
    for name, value in measured.items():
        record_property(name, value)
    (tmp_path / "linux-exec-memory.json").write_text(json.dumps(measured))
    assert 0 < measured["current_image_peak"] < 128 * 1024**2
    assert measured["lifetime_peak"] >= 256 * 1024**2


@pytest.mark.skipif(sys.platform != "linux", reason="Linux census fork/exec regression")
@pytest.mark.parametrize("kind", ["census", "legacy"])
def test_native_fit_with_retained_parent_keeps_unchanged_budget(tmp_path, record_property, kind):
    script = tmp_path / "retained_parent.py"
    script.write_text(
        """import json,sys
from pathlib import Path
from retailops_ai.anomaly_detectors.census_contract import CensusFitPolicy
from retailops_ai.anomaly_detectors.census_fit import fit_census_pipeline
from retailops_ai.anomaly_detectors.contract import FitPolicy
from retailops_ai.anomaly_detectors.fit import fit_pipeline
from retailops_ai.qualified_anomalies.contract import ModelRow
retained = bytearray(600 * 1024**2)
rows = [ModelRow(observed_units=10+i, expected_units=10, residual_units=i,
    robust_scale_units=1.0, standardized_residual=float(i), planned_price=None,
    promotion_offered=False, on_hand=None) for i in range(32)]
policy = CensusFitPolicy(n_estimators=8, max_samples=16)
if sys.argv[2]=="census":
    model, cost = fit_census_pipeline(iter(rows), len(rows), rows[:4], policy,
        scratch=Path(sys.argv[1]))
else:
    model, cost = fit_pipeline(rows, rows[:4], FitPolicy(n_estimators=8, max_samples=16))
assert len(retained)==600*1024**2 and model.training_rows==32
print(json.dumps(cost.model_dump(mode='json')))
"""
    )
    result = subprocess.run(
        [sys.executable, str(script), str(tmp_path), kind],
        env={**os.environ, "PYTHONPATH": str(Path(resources.__file__).parents[1])},
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    measured = json.loads(result.stdout)
    for name, value in measured.items():
        record_property(name, value)
    if kind == "census":
        assert 600 * 1024**2 <= measured["peak_rss_bytes"] <= 1024**3
    else:
        assert 0 < measured["peak_rss_bytes"] <= 512 * 1024**2
    assert measured["native_max_score_error"] <= 1e-12
