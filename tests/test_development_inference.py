"""Inference refuses changed populations and invalid numeric worker output before scoring."""

import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_tensorflow_challenger import FEATURE_ID, SPLIT_ID
from test_tensorflow_challenger import development as development

from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.model_contract import ResourceReceipt
from retailops_ai.source_snapshot.files import SnapshotError
from retailops_ai.tensorflow_challenger.contract import ChallengerPolicy
from retailops_ai.tensorflow_challenger.dataset import fit_normalization


def configured(development, monkeypatch, policy=None):
    from retailops_ai.evaluation_campaign import development_inference as inference

    fold, train, validation = development
    state = fit_normalization(train, fold=fold, feature_set_id=FEATURE_ID, split_id=SPLIT_ID)
    manifest = {
        "model_id": "model-sha256-" + "a" * 64,
        "descriptor": {
            "policy": (policy or ChallengerPolicy()).model_dump(mode="json"),
            "binding": {
                "validation_population_sha256": canonical_sha256(
                    [s.row.model_dump(mode="json") for w in validation for s in w.samples]
                )
            },
        },
    }
    monkeypatch.setattr(inference, "verify_artifact", lambda _: (manifest, state))
    return inference, manifest, validation


@pytest.mark.parametrize("change", ["shape", "dtype", "nan", "negative", "checksum", "model"])
def test_invalid_worker_results_cannot_become_forecasts(development, tmp_path, monkeypatch, change):
    inference, manifest, validation = configured(development, monkeypatch)

    def worker(root, policy):
        values = np.ones((len(validation), 14, 2), dtype=np.float32)
        if change == "shape":
            values = values[:, :13]
        elif change == "dtype":
            values = values.astype(np.float64)
        elif change == "nan":
            values[0, 0, 0] = np.nan
        elif change == "negative":
            values[0, 0, 0] = -1
        np.save(root / "output.npy", values, allow_pickle=False)
        receipt = {
            "model_id": manifest["model_id"],
            "output_sha256": hashlib.sha256((root / "output.npy").read_bytes()).hexdigest(),
        }
        if change == "checksum":
            receipt["output_sha256"] = "b" * 64
        if change == "model":
            receipt["model_id"] = "model-sha256-" + "b" * 64
        (root / "receipt.json").write_text(json.dumps(receipt))
        return ResourceReceipt(wall_seconds=1.0, cpu_seconds=1.0, peak_rss_bytes=1024)

    monkeypatch.setattr(inference, "_supervise", worker)
    with pytest.raises(SnapshotError, match="invalid_output|output_binding"):
        inference.tensorflow_predictions(tmp_path, validation)


@pytest.mark.parametrize("change", ["role", "population", "matrix_budget"])
def test_invalid_inputs_do_not_start_a_worker(development, tmp_path, monkeypatch, change):
    policy = ChallengerPolicy(max_matrix_bytes=1024) if change == "matrix_budget" else None
    inference, _, validation = configured(development, monkeypatch, policy)
    if change != "matrix_budget":
        sample = validation[0].samples[0]
        if change == "role":
            sample = replace(
                sample, membership=sample.membership.model_copy(update={"role": "train"})
            )
        else:
            sample = replace(sample, row=sample.row.model_copy(update={"product_id": "different"}))
        validation = (replace(validation[0], samples=(sample, *validation[0].samples[1:])),)

    def forbidden(*args):
        raise AssertionError("a rejected input must not start inference")

    monkeypatch.setattr(inference, "_supervise", forbidden)
    with pytest.raises(SnapshotError, match="budget|binding"):
        inference.tensorflow_predictions(tmp_path, validation)


@pytest.mark.parametrize("limit", ["rss", "wall"])
def test_supervisor_stops_and_reaps_only_its_private_worker(tmp_path, monkeypatch, limit):
    from retailops_ai.evaluation_campaign import development_inference as inference

    processes = []
    original = inference.subprocess.Popen

    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(inference.subprocess, "Popen", start)
    policy = (
        ChallengerPolicy(rss_bytes=1024**2)
        if limit == "rss"
        else ChallengerPolicy(wall_seconds=0.000001)
    )
    with pytest.raises(SnapshotError, match="budget_exceeded"):
        inference._supervise(tmp_path, policy)
    assert len(processes) == 1 and processes[0].poll() is not None
    assert (tmp_path / "worker.log").is_file()


def test_worker_refuses_a_broadcastable_wrong_output_shape(tmp_path, monkeypatch):
    from retailops_ai.evaluation_campaign import development_inference as inference

    policy = ChallengerPolicy()
    np.save(tmp_path / "input.npy", np.ones((1, 4), dtype=np.float32), allow_pickle=False)
    job = {
        "rows": 1,
        "artifact": str(tmp_path),
        "model_id": "controlled-fake-model",
        "input_sha256": hashlib.sha256((tmp_path / "input.npy").read_bytes()).hexdigest(),
        "policy": policy.model_dump(mode="json"),
    }
    (tmp_path / "job.json").write_text(json.dumps(job))
    fake = SimpleNamespace(
        state=SimpleNamespace(input_width=4, target_scale=1.0),
        manifest={"model_id": job["model_id"], "descriptor": {"policy": job["policy"]}},
        model=lambda *args, **kwargs: np.ones((1, 1, 2), dtype=np.float32),
    )
    monkeypatch.setattr(inference, "LoadedChallenger", lambda _: fake)
    monkeypatch.setattr(inference.resource, "setrlimit", lambda *args: None)
    with pytest.raises(SnapshotError, match="invalid_output"):
        inference.worker(tmp_path)
    assert not (tmp_path / "output.npy").exists()
