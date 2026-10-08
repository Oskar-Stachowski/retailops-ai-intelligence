"""Real pair/bundle checks on declared controlled files; package version gate mocked."""

import hashlib

import pytest
from test_campaign_tune_data import paired_control as paired_control
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_tune_worker as worker
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportPlan,
    CampaignDevelopmentExportReceipt,
    CampaignParentBudget,
)
from retailops_ai.evaluation_campaign.campaign_fit import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_generation_worker import read
from retailops_ai.source_snapshot.files import SnapshotError


def controlled_export(dataset, manifest, source_recipe_sha256):
    recipe = manifest.descriptor.recipe
    plan = CampaignDevelopmentExportPlan(
        source_recipe_sha256=source_recipe_sha256,
        generation_operation_id="controlled-generation",
        parent_budget=CampaignParentBudget(
            **{key: getattr(recipe.source, key) for key in CampaignParentBudget.model_fields}
        ),
        **{
            key: getattr(recipe, key)
            for key in (
                "origins",
                "roles",
                "purge_days",
                "label_delay_days",
                "features",
                "max_population_rows",
                "max_artifact_bytes",
                "max_index_bytes",
                "max_record_bytes",
            )
        },
    )
    return CampaignDevelopmentExportReceipt(
        protocol_sha256="b" * 64,
        operation_id="controlled-export",
        reservation_id="campaign-operation-" + "0" * 32,
        plan=plan,
        generated_parent_receipt_sha256="c" * 64,
        recipe=recipe,
        dataset_id=manifest.dataset_id,
        manifest_sha256=hashlib.sha256((dataset / "manifest.json").read_bytes()).hexdigest(),
        runtime_code_sha256=manifest.descriptor.runtime.code_sha256,
        snapshot_inventory_sha256=manifest.descriptor.snapshot_inventory_sha256,
        curated_inventory_sha256=manifest.descriptor.curated_inventory_sha256,
        logical_curated_sha256=manifest.descriptor.logical_curated_sha256,
        population_rows=manifest.descriptor.feature_descriptor.row_count,
        artifact_bytes=sum(path.stat().st_size for path in dataset.rglob("*") if path.is_file()),
    )


@pytest.fixture
def request_control(paired_control, monkeypatch):
    dataset, bundle, manifest, score, plan = paired_control
    exported = controlled_export(dataset, manifest, plan.source_recipe_sha256)
    parents = read(bundle / "parents.json")
    parents["export_receipt_sha256"] = exported.content_sha256()
    (bundle / "parents.json").write_bytes(canonical_bytes(parents) + b"\n")
    files, size = _bundle_inventory(bundle, score.plan.max_output_bytes)
    score = score.model_copy(
        update={
            "export_receipt_sha256": exported.content_sha256(),
            "artifact_files": files,
            "artifact_sha256": canonical_sha256(files),
            "artifact_bytes": size,
        }
    )
    monkeypatch.setattr(worker, "_versions", lambda plan: None)
    return {
        "dataset": str(dataset),
        "plan": plan.model_dump(mode="json"),
        "exported": exported.model_dump(mode="json"),
        "scores": {score.operation_id: score.model_dump(mode="json")},
        "bundles": {score.operation_id: str(bundle)},
        "runtime": manifest.descriptor.runtime.model_dump(mode="json"),
    }, score


def test_complete_worker_checks_real_parents_and_pairs_without_another_role(
    request_control, tmp_path, monkeypatch
):
    request, score = request_control
    opened = []
    from retailops_ai.evaluation_campaign import campaign_tune_data

    original = campaign_tune_data.regular_file

    def capture(root, name):
        opened.append(name)
        return original(root, name)

    monkeypatch.setattr(campaign_tune_data, "regular_file", capture)
    root = tmp_path / "tune-worker"
    root.mkdir(mode=0o700)
    result = worker.select(root, request)
    assert opened == ["tune.jsonl", "predictions.jsonl"]
    assert result["rows"] == score.rows and result["eligible_rows"] == score.eligible_rows
    assert result["full_tune_label_passes"] == result["trial_count"] == 1
    assert result["calibration_label_passes"] == result["independent_or_final_label_passes"] == 0
    assert result["selection"]["status"] == "not_ready"
    assert read(root / "bundle/selection.json") == result["selection"]


@pytest.mark.parametrize(
    "mutation", ["runtime", "export-manifest", "trials", "source", "role", "bundle"]
)
def test_worker_refuses_inconsistent_parents_before_any_label_pass(
    request_control, tmp_path, monkeypatch, mutation
):
    request, score = request_control
    if mutation == "runtime":
        request["runtime"]["code_sha256"] = "f" * 64
    elif mutation == "export-manifest":
        request["exported"]["manifest_sha256"] = "f" * 64
    elif mutation == "trials":
        request["scores"].clear()
    elif mutation == "source":
        request["scores"][score.operation_id]["plan"]["source_recipe_sha256"] = "f" * 64
    elif mutation == "role":
        request["scores"][score.operation_id]["plan"]["role"] = "calibration"
    else:
        from pathlib import Path

        (Path(request["bundles"][score.operation_id]) / "predictions.jsonl").write_bytes(b"{}\n")

    def forbidden(*args, **kwargs):
        raise AssertionError("binding or artifact failure must precede paired label reads")

    monkeypatch.setattr(worker, "paired_metrics", forbidden)
    with pytest.raises(SnapshotError):
        worker.select(tmp_path / "unused-output", request)
