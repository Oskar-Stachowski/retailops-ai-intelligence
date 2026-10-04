"""Required real fitting/reload; this suite runs in the separate locked TensorFlow environment."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
import pytest
from test_development_comparison import protocol_for
from test_forecast_features import tables as tables
from test_forecast_manifests import artifacts as artifacts
from test_forecast_manifests import timeline as timeline
from test_tensorflow_challenger import FEATURE_ID, SPLIT_ID
from test_tensorflow_challenger import development as development

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.comparison import require_same_forecast_keys
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.tensorflow_challenger.cli import main
from retailops_ai.tensorflow_challenger.contract import ChallengerPolicy
from retailops_ai.tensorflow_challenger.evaluation import compare_development
from retailops_ai.tensorflow_challenger.pipeline import (
    LoadedChallenger,
    fit_challenger,
    verify_artifact,
)


def test_real_training_supported_flavor_fresh_cpu_reload_and_corruption(development, tmp_path):
    fold, train, validation = development
    root = tmp_path / "trained"
    manifest = fit_challenger(
        train,
        validation,
        fold=fold,
        feature_set_id=FEATURE_ID,
        split_id=SPLIT_ID,
        output=root,
        policy=ChallengerPolicy(epochs=3),
    )
    assert manifest["worker"]["epochs_run"] == 3
    assert manifest["worker"]["tensorflow_version"] == "2.20.0"
    assert manifest["worker"]["visible_gpu_count"] == 0
    assert manifest["resources"]["peak_tree_rss_bytes"] < 1024**3
    assert manifest["artifact_bytes"] < 32 * 1024**2
    assert len(manifest["worker"]["mlflow_run_id"]) == 32
    loaded = LoadedChallenger(root)
    values = loaded.predict(validation)
    assert (
        require_same_forecast_keys([s.row for w in validation for s in w.samples], values)[0] == 14
    )
    assert all(p.value.interval is None and p.interval_status == "not_ready" for p in values)
    assert all(p.value.mean is not None and p.value.median is not None for p in values)
    report = compare_development(validation, values)
    assert report["global"]["status"] == "not_ready"
    assert "interval_predictions_incomplete_or_empty" in report["global"]["not_ready_reasons"]
    # Repeatability is checked on numeric outputs, not MLflow run IDs/ZIP timestamps.
    repeated = tmp_path / "repeat"
    fit_challenger(
        train,
        validation,
        fold=fold,
        feature_set_id=FEATURE_ID,
        split_id=SPLIT_ID,
        output=repeated,
        policy=ChallengerPolicy(epochs=3),
    )
    np.testing.assert_allclose(
        np.load(root / "validation_prediction.npy", allow_pickle=False),
        np.load(repeated / "validation_prediction.npy", allow_pickle=False),
        rtol=1e-6,
        atol=1e-6,
    )
    # A new CPU process must load the same checked bundle, independent of worker memory.
    code = """
import sys, numpy as np
from pathlib import Path
from retailops_ai.tensorflow_challenger.pipeline import LoadedChallenger
p = Path(sys.argv[1]); loaded = LoadedChallenger(p)
x = np.load(p / "x_validation.npy", allow_pickle=False)
actual = np.asarray(loaded.model(x, training=False), dtype=np.float32)
expected = np.load(p / "validation_prediction.npy", allow_pickle=False)
np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-6)
print("fresh_cpu_reload_passed")
"""
    probe = subprocess.run(
        [sys.executable, "-c", code, str(root)],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "CUDA_VISIBLE_DEVICES": "-1"},
        timeout=120,
    )
    assert "fresh_cpu_reload_passed" in probe.stdout
    # Resealed outer hashes cannot make an incorrect MLflow input signature compatible.
    signature = tmp_path / "wrong-signature"
    shutil.copytree(root, signature)
    mlmodel = signature / "model/MLmodel"
    raw = mlmodel.read_bytes().replace(str(loaded.state.input_width).encode(), b"99999")
    mlmodel.write_bytes(raw)
    body = json.loads((signature / "manifest.json").read_text())
    body["descriptor"]["files"]["MLmodel"] = {
        "size_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    body["model_id"] = "model-sha256-" + canonical_sha256(body["descriptor"])
    body["artifact_bytes"] = sum(f["size_bytes"] for f in body["descriptor"]["files"].values())
    (signature / "manifest.json").write_text(json.dumps(body))
    attempt = json.loads((signature / "attempt.json").read_text())
    attempt["model_id"] = body["model_id"]
    (signature / "attempt.json").write_text(json.dumps(attempt))
    with pytest.raises(SnapshotError, match="signature_mismatch"):
        verify_artifact(signature)
    # A changed preprocessor/model binary is rejected before the supported loader.
    for name in ("preprocessing.json", "data/model.keras"):
        broken = tmp_path / name.replace("/", "-")
        shutil.copytree(root, broken)
        path = broken / "model" / name
        path.write_bytes(path.read_bytes() + b"corruption")
        with pytest.raises(SnapshotError, match="checksum_or_identity"):
            LoadedChallenger(broken)


def test_development_cli_uses_the_verified_disk_parents_and_retains_diagnostic(
    artifacts, tmp_path, monkeypatch
):
    features, split, *_ = artifacts
    root = tmp_path / "cli-attempt"
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(ChallengerPolicy(epochs=3).model_dump_json())
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "challenger",
            "train-development",
            "--features",
            str(features),
            "--split",
            str(split),
            "--fold",
            "fold-a",
            "--output",
            str(root),
            "--policy",
            str(policy_path),
        ],
    )
    assert main() == 0
    report = json.loads((root / "evaluation.json").read_text())
    manifest, _ = verify_artifact(root)
    assert report["model_id"] == manifest["model_id"]
    assert report["prediction_rows"] == 14
    assert report["global"]["status"] == "not_ready"
    assert report["promotion_allowed"] is False and report["final_test_accessed"] is False


def test_comparison_real_tree_heads_tensorflow_reload_and_no_refits(
    development, tmp_path, monkeypatch
):
    from retailops_ai.evaluation_campaign import development as benchmark
    from retailops_ai.evaluation_campaign.development_contract import DevelopmentComparisonPolicy
    from retailops_ai.evaluation_campaign.development_metrics import comparison_metrics
    from retailops_ai.evaluation_campaign.trial_contract import TrialPlan
    from retailops_ai.evaluation_campaign.trial_registry import (
        audit_code,
        initialize,
        inspect,
        summary,
    )
    from retailops_ai.evaluation_campaign.trial_runner import (
        run_registered_comparison,
        snapshot_attempt,
    )

    fold, train, validation = development
    policy = DevelopmentComparisonPolicy(tensorflow=ChallengerPolicy(epochs=3))
    protocol = protocol_for(fold, policy)
    root = tmp_path / "comparison"

    # Controlled parents exercise the complete real fit/registry path, not source qualification.
    @contextmanager
    def parents(*args):
        yield (
            SimpleNamespace(feature_set_id=FEATURE_ID),
            SimpleNamespace(split_id=SPLIT_ID),
            fold,
            train,
            validation,
        )

    monkeypatch.setattr(benchmark, "development_parents", parents)
    monkeypatch.setattr(benchmark, "_protocol", lambda *args: protocol)
    registry = tmp_path / "registry"
    initialize(
        registry,
        TrialPlan(
            registry_path=str(registry),
            protocols=(protocol,),
            maximum_new_attempts=1,
            audit_code_sha256=audit_code(),
        ),
    )
    manifest = run_registered_comparison(
        registry=registry,
        protocol_sha256=canonical_sha256(protocol.model_dump(mode="json")),
        features=tmp_path / "features",
        split=tmp_path / "split",
        curated=tmp_path / "curated",
        output=root,
    )
    assert manifest["registry_attempt_id"] == inspect(registry).events[-1].attempt_id
    assert inspect(registry).events[-1].snapshot == snapshot_attempt(root)
    assert summary(inspect(registry))["completed_new_attempts"] == 1
    trees = benchmark._trees(root, train, protocol, replay=True)
    inference = {}
    predictions = benchmark._predictions(root, validation, trees, inference_resources=inference)
    legacy = LoadedChallenger(root / "tensorflow").predict(validation)
    require_same_forecast_keys(predictions["tensorflow"], legacy)
    checked, _ = verify_artifact(root / "tensorflow")
    # A parent already using oneDNN may differ from the isolated CPU worker on Linux.
    # Use the existing artifact's fixed numeric reload tolerance for this comparison.
    np.testing.assert_allclose(
        [[p.value.mean, p.value.median] for p in predictions["tensorflow"]],
        [[p.value.mean, p.value.median] for p in legacy],
        rtol=checked["worker"]["reload_rtol"],
        atol=checked["worker"]["reload_atol"],
    )
    assert [p.value.interval for p in predictions["tensorflow"]] == [
        p.value.interval for p in legacy
    ]
    assert inference["worker"]["validation_windows"] == len(validation)
    assert inference["worker"]["cold_load_seconds_including_framework_import"] > 0
    assert inference["resources"]["peak_rss_bytes"] < policy.tensorflow.rss_bytes
    report = comparison_metrics(validation, predictions, train)
    assert report["prediction_rows_per_model"] == 14
    assert report["models"]["rf_mean"]["global"]["candidate"]["median"]["mae"] is None

    def forbidden(*args, **kwargs):
        raise AssertionError("replay must not refit")

    monkeypatch.setattr(benchmark, "fit_worker", forbidden)
    reloaded = benchmark._trees(root, train, protocol, replay=True)
    assert benchmark._prediction_bytes(
        benchmark._predictions(root, validation, reloaded)
    ) == benchmark._prediction_bytes(predictions)
    receipt_path = root / "trees/rf_mean.resources.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["cpu_seconds"] = policy.trees.model.fit_cpu_seconds + 1
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(SnapshotError, match="tree_resource_budget"):
        benchmark._trees(root, train, protocol, replay=True)
