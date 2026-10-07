"""Real Calibration role/bundle controls; metadata Tune parent and version gate explicit."""

import copy

import pytest
from test_campaign_calibration_data import calibration_control as calibration_control
from test_campaign_calibration_data import calibration_plan
from test_campaign_tune_data import diagnostic, tune_plan
from test_campaign_tune_worker import controlled_export
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_calibration_data as data
from retailops_ai.evaluation_campaign import campaign_calibration_worker as worker
from retailops_ai.evaluation_campaign.campaign_fit import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_generation_worker import read
from retailops_ai.evaluation_campaign.campaign_tune_contract import CampaignForecastTuneReceipt
from retailops_ai.evaluation_campaign.campaign_tune_data import choose
from retailops_ai.source_snapshot.files import SnapshotError


@pytest.fixture
def request_control(calibration_control, monkeypatch):
    dataset, bundle, manifest, score, original = calibration_control
    exported = controlled_export(dataset, manifest, original.source_recipe_sha256)
    score_plan = score.plan.model_copy(update={"export_operation_id": exported.operation_id})
    for name, contents in (
        ("plan.json", score_plan.model_dump(mode="json")),
        (
            "parents.json",
            read(bundle / "parents.json") | {"export_receipt_sha256": exported.content_sha256()},
        ),
    ):
        (bundle / name).write_bytes(canonical_bytes(contents) + b"\n")
    files, size = _bundle_inventory(bundle, score.plan.max_output_bytes)
    score = score.model_copy(
        update={
            "plan": score_plan,
            "protocol_sha256": exported.protocol_sha256,
            "export_receipt_sha256": exported.content_sha256(),
            "artifact_files": files,
            "artifact_sha256": canonical_sha256(files),
            "artifact_bytes": size,
        }
    )
    trial, prototype = diagnostic()
    tune_score = prototype.model_copy(
        update={
            "plan": score_plan.model_copy(update={"role": "tune"}),
            "model_artifact_sha256": score.model_artifact_sha256,
            "fit_receipt_sha256": score.fit_receipt_sha256,
            "protocol_sha256": exported.protocol_sha256,
            "dataset_id": exported.dataset_id,
            "runtime_code_sha256": exported.runtime_code_sha256,
            "export_receipt_sha256": exported.content_sha256(),
        }
    )
    tp = tune_plan(
        source_recipe_sha256=original.source_recipe_sha256,
        export_operation_id=exported.operation_id,
    )
    selection = choose([trial], {tune_score.operation_id: tune_score}, tp)
    inventory = {
        name: "1" * 64 for name in ("plan.json", "parents.json", "metrics.json", "selection.json")
    }
    tune = CampaignForecastTuneReceipt(
        protocol_sha256=exported.protocol_sha256,
        operation_id=original.tune_operation_id,
        reservation_id="campaign-operation-" + "4" * 32,
        plan=tp,
        export_receipt_sha256=exported.content_sha256(),
        score_receipt_sha256={tune_score.operation_id: tune_score.content_sha256()},
        dataset_id=exported.dataset_id,
        runtime_code_sha256=exported.runtime_code_sha256,
        **{
            field: trial[field]
            for field in (
                "rows",
                "eligible_rows",
                "keys_sha256",
                "eligible_keys_sha256",
                "role_population_sha256",
                "baseline_predictions_sha256",
            )
        },
        selection=selection,
        artifact_sha256=canonical_sha256(inventory),
        artifact_bytes=1,
        artifact_files=inventory,
        worker_evidence={"controlled_metadata_tune_not_completed_project": True},
    )
    plan = calibration_plan(export_operation_id=exported.operation_id)
    monkeypatch.setattr(worker, "_versions", lambda plan: None)
    return {
        "dataset": str(dataset),
        "bundle": str(bundle),
        "exported": exported.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
        "tune": tune.model_dump(mode="json"),
        "tune_scores": {tune_score.operation_id: tune_score.model_dump(mode="json")},
        "scores": {score.operation_id: score.model_dump(mode="json")},
        "runtime": manifest.descriptor.runtime.model_dump(mode="json"),
    }, score


def test_real_worker_binds_selected_tune_model_and_reads_only_calibration(
    request_control, tmp_path, monkeypatch
):
    request, score = request_control
    opened, original = [], data.regular_file

    def capture(root, name):
        opened.append(name)
        return original(root, name)

    monkeypatch.setattr(data, "regular_file", capture)
    root = tmp_path / "calibration-worker"
    root.mkdir(mode=0o700)
    result = worker.calibrate(root, request)
    assert opened == ["calibration.jsonl", "predictions.jsonl"]
    assert result["rows"] == score.rows and result["eligible_rows"] == score.eligible_rows
    assert result["calibration"]["center"] == request["tune"]["selection"]["median"]
    assert result["calibration"]["status"] == "not_ready"
    assert read(root / "bundle/calibration.json") == result["calibration"]


@pytest.mark.parametrize(
    "mutation",
    [
        "runtime",
        "export-manifest",
        "trial",
        "source",
        "role",
        "fit",
        "model",
        "bundle",
        "selection-not-ready",
    ],
)
def test_worker_rejects_wrong_parent_before_calibration_labels(
    request_control, tmp_path, monkeypatch, mutation
):
    request, score = request_control
    request = copy.deepcopy(request)
    if mutation == "runtime":
        request["runtime"]["code_sha256"] = "f" * 64
    elif mutation == "export-manifest":
        request["exported"]["manifest_sha256"] = "f" * 64
    elif mutation == "trial":
        request["scores"].clear()
    elif mutation == "source":
        request["scores"][score.operation_id]["plan"]["source_recipe_sha256"] = "f" * 64
    elif mutation == "role":
        request["scores"][score.operation_id]["plan"]["role"] = "tune"
    elif mutation == "fit":
        request["scores"][score.operation_id]["plan"]["fit_operation_ids"]["hgb"] = "different-fit"
    elif mutation == "model":
        request["scores"][score.operation_id]["model_artifact_sha256"]["hgb"] = "f" * 64
    elif mutation == "bundle":
        from pathlib import Path

        (Path(request["bundle"]) / "predictions.jsonl").write_bytes(b"{}\n")
    else:
        request["tune"]["selection"]["status"] = "not_ready"
        request["tune"]["selection"]["reasons"] = ["insufficient_eligible_rows"]

    def forbidden(*args, **kwargs):
        raise AssertionError("parent/runtime failure must precede residual label reads")

    monkeypatch.setattr(worker, "fit", forbidden)
    with pytest.raises(SnapshotError):
        worker.calibrate(tmp_path / "unused-output", request)
