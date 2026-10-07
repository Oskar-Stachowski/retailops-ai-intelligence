"""Required real fit/reload of controlled arrays; no canonical data or project journal."""

import sys
from pathlib import Path
from time import perf_counter

import numpy as np
import pytest
from mlflow.tracking import MlflowClient
from test_campaign_fit_data import fit_plan
from test_campaign_fit_data import indexed as indexed
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.evaluation_campaign import campaign_fit_data as data
from retailops_ai.evaluation_campaign import campaign_fit_worker as worker
from retailops_ai.evaluation_campaign.campaign_generation import _environment
from retailops_ai.evaluation_campaign.campaign_generation_monitor import monitor
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write


@pytest.mark.parametrize("family", ["rf", "hgb", "tensorflow"])
def test_real_complete_population_fit_supported_artifact_and_fresh_cpu_reload(
    indexed, tmp_path, family
):
    db, _, _, _ = indexed
    initial = fit_plan()
    plan = initial.model_copy(
        update={
            "family": family,
            "epochs": 2,
            "rf": initial.rf.model_copy(update={"n_estimators": 4, "max_depth": 3}),
            "hgb": initial.hgb.model_copy(update={"max_iter": 3, "min_samples_leaf": 1}),
        }
    )
    root = tmp_path / family
    root.mkdir(mode=0o700)
    (root / "tmp").mkdir(mode=0o700)
    (root / "arrays").mkdir(mode=0o700)
    (root / "bundle").mkdir(mode=0o700)
    state = data.fit_encoding(db, plan)
    factory = data.tensorflow_matrices if family == "tensorflow" else data.tree_matrices
    matrices = {role: factory(db, role, state, plan, root / "arrays") for role in data.ROLES}
    write(root / "request.json", {"plan": plan.model_dump(mode="json")})
    write(root / "prepare.json", {"encoding_sha256": state.content_sha256(), "matrices": matrices})
    write(root / "bundle/encoding.json", state.model_dump(mode="json"))
    write(root / "bundle/plan.json", plan.model_dump(mode="json"))
    write(root / "bundle/binding.json", {"scope": "controlled_arrays_not_canonical_source"})
    for phase in ("fit", "reload"):
        measured = monitor(
            [sys.executable, "-I", "-B", str(Path(worker.__file__)), phase, str(root)],
            root=root,
            log=root / (phase + ".log"),
            env=_environment(root),
            scratch=(root,),
            resources=plan.resources,
            deadline=perf_counter() + plan.resources.wall_seconds,
        )
        assert measured["status"] == "passed", (
            measured,
            (root / (phase + ".log")).read_text()[-5000:]
            if (root / (phase + ".log")).exists()
            else "preflight refused",
        )
    fitted, loaded = read(root / "fit.json"), read(root / "reload.json")
    assert len(fitted["mlflow_run_id"]) == 32
    client = MlflowClient(tracking_uri=(root / "tracking").as_uri())
    run = client.get_run(fitted["mlflow_run_id"])
    assert run.info.status == "FINISHED"
    assert run.data.params["plan_sha256"] == plan.content_sha256()
    assert run.data.metrics["train_eligible_rows"] == state.train_rows
    assert (
        run.data.metrics["reload_verified_eligible_rows"] == loaded["reload_verified_eligible_rows"]
    )
    assert run.data.metrics["artifact_bytes"] > 0
    assert run.data.metrics["fit_worker_peak_rss_bytes"] > 0
    assert run.data.metrics["reload_worker_peak_rss_bytes"] > 0
    assert loaded["reload_all_early_stopping_keys_verified"] is True
    assert loaded["reload_verified_eligible_rows"] == matrices["early_stopping"].get(
        "eligible_rows", matrices["early_stopping"].get("rows")
    )
    assert fitted["dependency_versions"]["numpy"] == "2.2.6"
    if family == "rf":
        assert fitted["heads"] == ["mean"] and not (root / "median-prediction.npy").exists()
    elif family == "hgb":
        assert fitted["heads"] == ["mean", "median"]
        assert set(fitted["head_costs"]) == {"mean", "median"}
    else:
        assert fitted["epochs_run"] == 2 and fitted["visible_gpu_count"] == 0
        assert (root / "bundle/keras/MLmodel").is_file()
        metadata_file = root / "bundle/keras/MLmodel"
        original = metadata_file.read_bytes()
        wrong = original.replace(str(state.tensorflow_width).encode(), b"99999")
        assert wrong != original
        metadata_file.write_bytes(wrong)
        measured = monitor(
            [sys.executable, "-I", "-B", str(Path(worker.__file__)), "reload", str(root)],
            root=root,
            log=root / "signature-reload.log",
            env=_environment(root),
            scratch=(root,),
            resources=plan.resources,
            deadline=perf_counter() + plan.resources.wall_seconds,
        )
        assert measured["status"] == "failed" and measured["reason"] == "worker_exit"
        assert (
            "campaign_forecast_keras_signature_mismatch"
            in (root / "signature-reload.log").read_text()
        )
        metadata_file.write_bytes(original)
    name = "tensorflow" if family == "tensorflow" else "mean"
    prediction = np.load(root / (name + "-prediction.npy"), allow_pickle=False)
    assert np.isfinite(prediction).all() and (prediction >= 0).all()
    prediction.flat[0] += 1
    np.save(root / (name + "-prediction.npy"), prediction, allow_pickle=False)
    measured = monitor(
        [sys.executable, "-I", "-B", str(Path(worker.__file__)), "reload", str(root)],
        root=root,
        log=root / "corruption-reload.log",
        env=_environment(root),
        scratch=(root,),
        resources=plan.resources,
        deadline=perf_counter() + plan.resources.wall_seconds,
    )
    assert measured["status"] == "failed" and measured["reason"] == "worker_exit"
    assert client.get_run(fitted["mlflow_run_id"]).info.status == "FAILED"
