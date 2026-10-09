"""Real complete public parents, portable artifacts and full cold replay controls."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_anomaly_detectors import protocol
from test_campaign_anomaly_parent import public_parent, reader, source  # noqa: F401

from retailops_ai.anomaly_detectors.census_contract import CensusFitPolicy
from retailops_ai.anomaly_detectors.rows import ALL_FEATURES
from retailops_ai.anomaly_portfolio.model import EventCapacity, load
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_anomaly_fit import _verify_bundle_content
from retailops_ai.evaluation_campaign.campaign_anomaly_fit_contract import (
    CampaignAnomalyFitPlan,
    CampaignAnomalyFitReceipt,
)
from retailops_ai.evaluation_campaign.campaign_anomaly_fit_data import (
    CampaignAnomalyPublicParent,
    fit_physical_anomaly_parent,
    reload_physical_anomaly,
)
from retailops_ai.evaluation_campaign.campaign_fit import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_generation_worker import write
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.source_snapshot.files import SnapshotError


def fit_plan(**updates):
    p = protocol()
    values = {
        k: getattr(p, k)
        for k in ("train", "validation", "test", "training_cutoff", "selection_cutoff")
    }
    return CampaignAnomalyFitPlan(
        **(
            values
            | {
                "source_recipe_sha256": "1" * 64,
                "export_operation_id": "development-42-read",
                "policy": CensusFitPolicy.model_validate(
                    {"n_estimators": 8, "max_samples": 16, "features": ALL_FEATURES}
                ),
                "event_capacities": tuple(
                    EventCapacity(event_type=e, alert_fraction=0.05, high_fraction=0.01)
                    for e in ("sale_completed", "return_completed")
                ),
                "resources": CampaignGenerationResources(
                    wall_seconds=600,
                    tree_rss_bytes=1024**3,
                    scratch_bytes=1024**3,
                    minimum_available_memory_bytes=1024**3,
                    minimum_free_disk_bytes=6 * 1024**3,
                ),
                "max_index_bytes": 128 * 1024**2,
            }
            | updates
        )
    )


@pytest.mark.parametrize("late_parent_failure", [False, True])
def test_real_public_parent_fit_bundle_and_fresh_process_complete_reload(
    public_parent,  # noqa: F811
    tmp_path,
    monkeypatch,
    late_parent_failure,
):
    snapshot, curated, _, _ = public_parent
    scratch = tmp_path / "probe"
    scratch.mkdir()
    with reader(public_parent, scratch) as parent:
        code_state = parent._replay.producer_code_state
        expected = (
            parent._replay.producer_commit,
            parent._replay.producer_lock_sha256,
            parent._replay.exporter_lock_sha256,
        )
    root = tmp_path / "fit"
    root.mkdir(mode=0o700)
    (root / "tmp").mkdir(mode=0o700)
    plan = fit_plan()
    runtime = runtime_pin()

    def execute():
        return fit_physical_anomaly_parent(
            snapshot,
            curated,
            root,
            plan=plan,
            source=source(snapshot, curated),
            runtime=runtime,
            producer_commit=expected[0],
            producer_lock=expected[1],
            exporter_lock=expected[2],
        )

    if code_state != "clean":
        # This preserved ordinary fixture was produced from a modified checkout.
        # It must remain rejected; do not relabel/reseal it as clean evidence.
        with pytest.raises(SnapshotError, match="verified_producer_or_lock_mismatch"):
            execute()
        assert not (root / "bundle").exists()
        assert not list((root / "tmp").iterdir())
        return
    if late_parent_failure:
        original_exit = CampaignAnomalyPublicParent.__exit__

        def rejected_exit(self, *args):
            original_exit(self, *args)
            assert args[0] is None
            # The full fit and both validation populations have been written,
            # but a last parent verification failure must still withhold a bundle.
            assert (root / "validation-isolation_forest.jsonl").is_file()
            assert (root / "validation-seasonal_residual.jsonl").is_file()
            raise SnapshotError("control_late_parent_failure")

        monkeypatch.setattr(CampaignAnomalyPublicParent, "__exit__", rejected_exit)
        with pytest.raises(SnapshotError, match="control_late_parent_failure"):
            execute()
        assert not (root / "bundle").exists()
        assert not list((root / "tmp").iterdir())
        return
    result = execute()
    assert result["all_parent_contexts_completed"]
    assert not list((root / "tmp").iterdir())
    model = load(root / "bundle/model.json", result["model_sha256"])
    manifest = json.loads((root / "bundle/feature-manifest.json").read_bytes())
    completed = json.loads((root / "bundle/parent-completion.json").read_bytes())
    assert manifest["resolved_days"] == completed["days"]["stats"]["projected_days"]
    assert manifest["resolved_days"] == completed["features"]["stats"]["projected_points"]
    assert model.descriptor.feature_manifest_sha256 == canonical_sha256(manifest)
    assert model.descriptor.qualified_anomaly_input_id is None
    assert result["requested_rows"] == sum(c for _, _, c in result["membership_counts"])
    files, size = _bundle_inventory(root / "bundle", plan.max_artifact_bytes)
    receipt = CampaignAnomalyFitReceipt(
        protocol_sha256="2" * 64,
        operation_id="control-fit",
        reservation_id="campaign-operation-" + "3" * 32,
        plan=plan,
        export_receipt_sha256="4" * 64,
        runtime_code_sha256=runtime.code_sha256,
        model_id=result["model_id"],
        feature_manifest_sha256=result["feature_manifest_sha256"],
        model_artifact_sha256=canonical_sha256(files),
        model_artifact_bytes=size,
        artifact_files=files,
        worker_evidence={},
    )
    _verify_bundle_content(root / "bundle", receipt)
    write(root / "fit.json", result)
    write(
        root / "request.json",
        {"plan": plan.model_dump(mode="json"), "runtime": runtime.model_dump(mode="json")},
    )
    from retailops_ai.evaluation_campaign import campaign_anomaly_fit_worker

    subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(Path(campaign_anomaly_fit_worker.__file__)),
            "reload",
            str(root),
        ],
        check=True,
        timeout=60,
        env={**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
    )
    reload = json.loads((root / "reload.json").read_bytes())
    assert reload["all_validation_rows_replayed"]
    assert reload["rows"] == {
        name: value["rows"] for name, value in result["validation_files"].items()
    }
    assert reload["cpu_seconds"] > 0
    assert reload["cold_model_load_wall_seconds"] > 0
    assert reload["validation_replay_wall_seconds"] > 0
    # Hash/extent failures reject the whole reload, even after earlier rows passed.
    name = next(iter(result["validation_files"]))
    path = root / name
    raw = path.read_bytes()
    path.write_bytes(raw[: raw.rfind(b"\n", 0, -1) + 1])
    with pytest.raises(SnapshotError, match="reload_identity"):
        reload_physical_anomaly(root, result)
    path.write_bytes(raw)
    altered = json.loads((root / "bundle/feature-manifest.json").read_bytes())
    altered["native_points_sha256"] = "0" * 64
    (root / "bundle/feature-manifest.json").write_bytes(canonical_bytes(altered) + b"\n")
    with pytest.raises(SnapshotError, match="bundle_inventory"):
        _verify_bundle_content(root / "bundle", receipt)
