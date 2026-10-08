"""Bounded phase controls with declared parents and fake models, no project evidence."""

import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_campaign_evaluation_configuration import configuration, parents, trial_rows
from test_campaign_evaluation_data import evaluation_dataset as evaluation_dataset
from test_campaign_evaluation_data import evaluation_plan
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.evaluation_campaign import campaign_evaluation_data as data
from retailops_ai.evaluation_campaign import campaign_evaluation_worker as worker
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_score_contract import FAMILIES
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.evaluation_campaign.physical_forecast import _index
from retailops_ai.source_snapshot.files import SnapshotError, file_hash


@pytest.fixture
def controlled(evaluation_dataset, tmp_path, monkeypatch):
    dataset, manifest, original, _, _ = evaluation_dataset
    runtime = runtime_pin()
    fits = {
        operation: receipt.model_copy(update={"runtime_code_sha256": runtime.code_sha256})
        for operation, receipt in parents()[-1].items()
    }
    frozen = configuration()
    frozen = frozen.model_copy(
        update={
            "runtime_code_sha256": runtime.code_sha256,
            "trials": tuple(
                trial.model_copy(
                    update={
                        "fit_receipt_sha256": {
                            family: fits[trial.fit_operation_ids[family]].content_sha256()
                            for family in FAMILIES
                        }
                    }
                )
                for trial in frozen.trials
            ),
        }
    )
    plan = evaluation_plan(
        original.role,
        frozen_configuration_sha256=frozen.content_sha256(),
        source_recipe_sha256=original.source_recipe_sha256,
        worker_environment_lock_sha256=frozen.worker_environment_lock_sha256,
    )
    inputs, actuals = tmp_path / "inputs.sqlite", tmp_path / "actuals.sqlite"
    with (
        closing(_index(inputs, plan.max_index_bytes)) as covariates,
        closing(_index(actuals, plan.max_index_bytes)) as outcomes,
    ):
        counts = data.index_role(covariates, outcomes, dataset, manifest, plan)
    counts |= {
        "inputs_sha256": file_hash(inputs.parent, inputs.name)[1],
        "actuals_sha256": file_hash(actuals.parent, actuals.name)[1],
    }
    loaded = []

    def models(request):
        trial = request["trial"]["tune_score_operation_id"]
        loaded.append(trial)
        return {}, {"index": int(trial.rsplit("-", 1)[1])}

    def infer(windows, encodings, fitted):
        # The actual model call sees inference records, never outcomes or role.
        assert all(
            not hasattr(record, "actual") and not hasattr(record, "role")
            for window in windows
            for record in window.records
        )
        return {
            record.key: trial_rows(index=fitted["index"], eligible=record.eligible)[
                fitted["index"]
            ].values
            for window in windows
            for record in window.records
        }

    monkeypatch.setattr(worker, "load_models", models)
    monkeypatch.setattr(worker, "infer_functionals", infer)
    return tmp_path, plan, frozen, counts, fits, loaded


def prediction_request(controlled, index):
    root, plan, frozen, counts, fits, _ = controlled
    trial = frozen.trials[index]
    return {
        "plan": plan.model_dump(mode="json"),
        "runtime": {"code_sha256": frozen.runtime_code_sha256},
        "frozen_configuration_sha256": frozen.content_sha256(),
        "population": counts,
        "inputs": str(root / "inputs.sqlite"),
        "trial": trial.model_dump(mode="json"),
        "fits": {
            family: fits[trial.fit_operation_ids[family]].model_dump(mode="json")
            for family in FAMILIES
        },
        "bundles": {family: "declared-controlled-bundle" for family in FAMILIES},
        "index_overhead_bytes": (root / "actuals.sqlite").stat().st_size
        + ((root / "projection.sqlite").stat().st_size if index else 0),
    }


def predict_and_consume(controlled, index, *, consume=True):
    root, plan, frozen, counts, _, _ = controlled
    trial_root = root / f"trial-{index}"
    trial_root.mkdir(mode=0o700)
    request = prediction_request(controlled, index)
    result = worker.predict(trial_root, request, plan)
    assert result["actual_index_passes"] == 0
    assert result["rows"] == counts["rows"]
    request |= {
        "configuration": frozen.model_dump(mode="json"),
        "actuals": str(root / "actuals.sqlite"),
        "projection": str(root / "projection.sqlite"),
        "predicted": result,
        "metrics_output": str(root / f"metrics-{index}.json"),
    }
    if consume:
        consumed = worker.consume(trial_root, request, plan)
        assert consumed["actual_index_passes"] == 1
        assert consumed["prediction_trace_sha256"] == result["prediction_trace_sha256"]
    return trial_root, request


def final_request(controlled):
    root, _, frozen, counts, _, _ = controlled
    bundle = root / "bundle"
    bundle.mkdir(mode=0o700)
    return {
        "runtime": {"code_sha256": frozen.runtime_code_sha256},
        "configuration": frozen.model_dump(mode="json"),
        "population": counts,
        "actuals": str(root / "actuals.sqlite"),
        "projection": str(root / "projection.sqlite"),
        "bundle": str(bundle),
    }


def test_complete_sequential_comparison_preserves_keys_separate_heads_and_honest_scope(controlled):
    root, plan, frozen, counts, _, loaded = controlled
    for index in range(len(frozen.trials)):
        trial_root, _ = predict_and_consume(controlled, index)
        # Only the current raw prediction index is needed after durable aggregation.
        (trial_root / "predictions.sqlite").unlink()
    result = worker.finalize(root, final_request(controlled), plan)
    assert loaded == [trial.tune_score_operation_id for trial in frozen.trials]
    assert all(result[key] == counts[key] for key in worker.POPULATION)
    rows = [
        json.loads(line) for line in (root / "bundle/predictions.jsonl").read_bytes().splitlines()
    ]
    assert len(rows) == counts["rows"]
    assert all(row["role"] == plan.role for row in rows)
    for row in rows:
        if row["eligible"]:
            assert row["candidate"]["mean"] == 30.0
            assert row["candidate"]["median"] == 8.0
            assert row["candidate"]["interval"] == {"lower": 6.0, "upper": 10.0}
            assert row["reference"]["median"] == 40.0
            assert row["reference"]["interval_center"] == 2.0
    metrics = read(root / "bundle/metrics.json")
    assert len(metrics["segments"]) == 15
    assert not metrics["quality_qualified"] and not metrics["stage_ready"]
    assert not metrics["critical_segment_inventory_complete"]
    assert not metrics["block_uncertainty_complete"]
    assert metrics["final_test_accessed"] == (plan.phase == "final")


def test_missing_unselected_trial_cannot_be_reported_as_full_comparison(controlled):
    root, plan, frozen, _, _, _ = controlled
    predict_and_consume(controlled, 0)
    # Even if both selected heads appear present, all frozen trials are required.
    with sqlite3.connect(root / "projection.sqlite") as db:
        db.execute("UPDATE selected SET median_seen=1,median=8")
    with pytest.raises(SnapshotError, match="all_frozen_trials_required"):
        worker.finalize(root, final_request(controlled), plan)
    assert len(frozen.trials) == 2


def test_changed_prediction_index_rejected_before_actual_iteration(controlled, monkeypatch):
    _, plan, _, _, _, _ = controlled
    root, request = predict_and_consume(controlled, 0, consume=False)
    with sqlite3.connect(root / "predictions.sqlite") as db:
        db.execute("DELETE FROM predictions WHERE key=(SELECT key FROM predictions LIMIT 1)")

    def forbidden(*args):
        raise AssertionError("tampered prediction must fail before consuming actuals")

    monkeypatch.setattr(data, "actuals", forbidden)
    with pytest.raises(SnapshotError, match="index_checksum_mismatch"):
        worker.consume(root, request, plan)


def test_prediction_does_not_require_actual_index_file(controlled):
    root, plan, _, _, _, _ = controlled
    request = prediction_request(controlled, 0)
    (root / "actuals.sqlite").rename(root / "withheld-actuals.sqlite")
    target = root / "prediction-without-outcomes"
    target.mkdir(mode=0o700)
    result = worker.predict(target, request, plan)
    assert result["actual_index_passes"] == 0
    assert result["all_models_share_all_role_keys"]


def test_fresh_core_aggregation_process_without_tensorflow_or_mlflow(controlled):
    root, plan, frozen, _, _, _ = controlled
    for index in range(len(frozen.trials)):
        predict_and_consume(controlled, index)
    request = final_request(controlled) | {
        "plan": plan.model_dump(mode="json"),
        "runtime": runtime_pin().model_dump(mode="json"),
    }
    phase_root = root / "fresh-aggregation"
    phase_root.mkdir(mode=0o700)
    write(phase_root / "request.json", request)
    entry = Path(worker.__file__).with_name("campaign_evaluation_entry.py")
    process = subprocess.run(
        [sys.executable, "-I", "-B", str(entry), "finalize", str(phase_root)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert process.returncode == 0, process.stderr[-4096:]
    result = read(phase_root / "finalize.json")
    assert result["phase"] == "finalize" and result["all_frozen_trials_compared"]
    assert result["worker_seconds"] > 0 and result["worker_peak_rss_bytes"] > 0


@pytest.mark.parametrize("field", ["actuals", "dataset", "exported", "outcomes", "unknown"])
def test_inference_rejects_unexpected_payload_before_model_or_label_io(
    monkeypatch, tmp_path, field
):
    def forbidden(*args):
        raise AssertionError("forbidden inference payload must fail before model loading")

    monkeypatch.setattr(worker, "load_models", forbidden)
    with pytest.raises(SnapshotError, match="request_contains_outcomes"):
        worker.predict(tmp_path, {field: "controlled-forbidden-payload"}, evaluation_plan())


@pytest.mark.parametrize("family", FAMILIES)
def test_changed_frozen_fit_rejected_before_loading_models(monkeypatch, tmp_path, family):
    frozen = configuration()
    fits = parents()[-1]
    trial = frozen.trials[0]
    request = {
        "frozen_configuration_sha256": frozen.content_sha256(),
        "runtime": {"code_sha256": frozen.runtime_code_sha256},
        "trial": trial.model_dump(mode="json"),
        "fits": {f: fits[trial.fit_operation_ids[f]].model_dump(mode="json") for f in FAMILIES},
    }
    request["fits"][family]["encoding_sha256"] = "f" * 64

    def forbidden(*args):
        raise AssertionError("invalid fit must fail before loading models")

    monkeypatch.setattr(worker, "load_models", forbidden)
    with pytest.raises(SnapshotError, match="frozen_trial_fit_binding_mismatch"):
        worker.predict(
            tmp_path,
            request,
            evaluation_plan(frozen_configuration_sha256=frozen.content_sha256()),
        )


@pytest.mark.parametrize("phase", ["consume", "finalize"])
def test_different_configuration_rejected_before_any_index_io(monkeypatch, tmp_path, phase):
    frozen = configuration()
    request = {
        "configuration": frozen.model_dump(mode="json"),
        "runtime": {"code_sha256": frozen.runtime_code_sha256},
    }

    def forbidden(*args):
        raise AssertionError("configuration must be verified before index I/O")

    monkeypatch.setattr(worker, "file_hash", forbidden)
    with pytest.raises(SnapshotError, match="configuration_plan_mismatch"):
        getattr(worker, phase)(tmp_path, request, evaluation_plan())


def test_combined_index_budget_includes_controller_declared_overhead(tmp_path):
    path = tmp_path / "controlled-index"
    path.write_bytes(b"controlled")
    plan = evaluation_plan(max_index_bytes=4096)
    worker._indexes((path,), plan, 4096 - path.stat().st_size)
    with pytest.raises(SnapshotError, match="combined_index_budget"):
        worker._indexes((path,), plan, 4097 - path.stat().st_size)
    with pytest.raises(SnapshotError, match="invalid_index_overhead"):
        worker._indexes((path,), plan, True)


def test_predict_phase_tracks_full_fit_identity_costs_and_separate_private_artifacts(
    tmp_path, monkeypatch
):
    frozen, plan = configuration(), evaluation_plan()
    request = {
        "runtime": runtime_pin().model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
        "trial": frozen.trials[0].model_dump(mode="json"),
    }
    write(tmp_path / "request.json", request)
    params, metrics, locations = [], [], []
    fake = SimpleNamespace(
        set_tracking_uri=lambda uri: locations.append(uri),
        create_experiment=lambda name, **kwargs: (
            locations.append(kwargs["artifact_location"]) or "controlled-experiment"
        ),
        start_run=lambda **kwargs: nullcontext(
            SimpleNamespace(info=SimpleNamespace(run_id="controlled-run"))
        ),
        log_params=params.append,
        log_metrics=metrics.append,
    )
    monkeypatch.setitem(sys.modules, "mlflow", fake)
    monkeypatch.setattr(worker.resource, "setrlimit", lambda *args: None)
    monkeypatch.setattr(
        worker,
        "predict",
        lambda *args: {
            "rows": 2,
            "eligible_rows": 1,
            "fresh_process_cold_load_seconds": 1.0,
            "batch_inference_seconds": 2.0,
            "actual_index_passes": 0,
            "prediction_trace_sha256": "1" * 64,
            "baseline_trace_sha256": "2" * 64,
        },
    )
    worker.main("predict", tmp_path)
    result = read(tmp_path / "predict.json")
    assert result["mlflow_run_id"] == "controlled-run"
    assert locations == [
        (tmp_path / "tracking").as_uri(),
        (tmp_path / "tracking-artifacts").as_uri(),
    ]
    assert all(
        params[0]["fit_receipt_sha256_" + f] == request["trial"]["fit_receipt_sha256"][f]
        for f in FAMILIES
    )
    assert metrics[0]["actual_index_passes"] == 0
    assert result["worker_peak_rss_bytes"] > 0
    assert os.stat(tmp_path / "predict.json").st_mode & 0o077 == 0
