"""A model worker must not inherit the launcher's historical RSS after exec."""

import hashlib
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from retailops_ai.forecasting.functional_contract import FunctionalPolicy
from retailops_ai.forecasting.model_contract import ModelPolicy, RFConfig
from retailops_ai.forecasting.models import model_code
from retailops_ai.tensorflow_challenger.pipeline import implementation


@pytest.mark.parametrize("name", ["functional_worker", "model_worker"])
def test_real_numeric_worker_receipt_uses_the_shared_executable_measurement(
    name, tmp_path, monkeypatch
):
    worker = importlib.import_module("retailops_ai.forecasting." + name)
    model = ModelPolicy(rf=RFConfig(n_estimators=2, max_depth=2))
    policy = FunctionalPolicy(model=model) if name == "functional_worker" else model
    (tmp_path / "job.json").write_text(
        json.dumps(
            {
                "policy": policy.model_dump(mode="json"),
                "head": "rf_mean",
                "family": "random_forest",
            }
        )
    )
    np.save(tmp_path / "x.npy", np.ones((16, 2), dtype=np.float64))
    np.save(tmp_path / "y.npy", np.arange(16, dtype=np.float64))
    monkeypatch.setattr(sys, "argv", [name, str(tmp_path)])
    monkeypatch.setattr(worker, "worker_peak_rss_bytes", lambda: 32 * 1024**2)
    assert worker.main() == 0
    receipt = json.loads((tmp_path / "worker_receipt.json").read_bytes())
    assert receipt["peak_rss_bytes"] == 32 * 1024**2
    assert receipt["training_rows"] == 16
    assert receipt["portable_max_absolute_error"] < 1e-12


def test_model_identity_binds_the_shared_memory_implementation():
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256(
        (root / "src/retailops_ai/worker_resources.py").read_bytes()
    ).hexdigest()
    assert model_code().code_files["worker_resources.py"] == digest
    assert implementation()["worker_resources.py"] == digest


@pytest.mark.skipif(sys.platform != "linux", reason="Linux pre-exec RSS retention")
def test_actual_fit_survives_a_larger_parent_without_raising_any_budget():
    code = """
import json,numpy as np
from retailops_ai.forecasting.functional_contract import FunctionalPolicy
from retailops_ai.forecasting.model_contract import ModelPolicy,RFConfig
from retailops_ai.forecasting.models import fit_worker as basic
from retailops_ai.forecasting.functional_models import fit_worker as functional
retained = bytearray(640*1024**2)
policy = ModelPolicy(rf=RFConfig(n_estimators=2,max_depth=2),fit_rss_bytes=512*1024**2)
x=np.ones((100,2),dtype=np.float64); y=np.arange(100,dtype=np.float64)
peaks=[]
for call,kind,recipe in [(basic,'random_forest',policy),(functional,'rf_mean',FunctionalPolicy(model=policy))]:
    model,receipt=call(x,y,kind,recipe)
    assert 0 < receipt.peak_rss_bytes < policy.fit_rss_bytes
    peaks.append(receipt.peak_rss_bytes)
assert len(retained)==640*1024**2
print(json.dumps({'child_peaks':peaks,'retained_parent_bytes':len(retained)}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1"},
    )
    assert len(json.loads(result.stdout)["child_peaks"]) == 2
