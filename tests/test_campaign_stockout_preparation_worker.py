"""Fresh worker phases on existing exposed fixtures; no Project generation."""

import subprocess
import sys
from pathlib import Path

import pytest
from test_campaign_anomaly_parent import public_parent as public_parent  # noqa: F401
from test_campaign_anomaly_parent import source
from test_campaign_stockout_labels import qualified as qualified  # noqa: F401
from test_campaign_stockout_maturity import policy
from test_campaign_stockout_preparation import limits

from retailops_ai.evaluation_campaign import campaign_stockout_preparation_worker as worker
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignGeneratedParentReceipt,
    CampaignParentBudget,
)
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_stockout_preparation_contract import (
    CampaignStockoutPreparationPlan,
)
from retailops_ai.evaluation_campaign.campaign_stockout_role_artifact import (
    verify_stockout_roles,
)
from retailops_ai.evaluation_campaign.partitions import runtime_pin
from retailops_ai.source_snapshot.files import SnapshotError


@pytest.fixture(scope="module")
def prepared_worker(public_parent, qualified, tmp_path_factory):
    root = tmp_path_factory.mktemp("stockout-preparation-worker")
    specification = source(*public_parent[:2])
    plan = CampaignStockoutPreparationPlan(
        source_recipe_sha256="a" * 64,
        generation_operation_id="controlled-generation",
        policy=policy(),
        parent_budget=CampaignParentBudget(
            **{k: getattr(specification, k) for k in CampaignParentBudget.model_fields}
        ),
        resources=limits(),
    )
    # This receipt is controlled metadata for testing the worker, not journal
    # authorization or acceptance of a generated Project campaign.
    generated = CampaignGeneratedParentReceipt(
        protocol_sha256="b" * 64,
        source_recipe_sha256=plan.source_recipe_sha256,
        operation_id=plan.generation_operation_id,
        reservation_id="campaign-operation-" + "c" * 32,
        source=specification,
        runtime=runtime_pin(),
    )
    write(
        root / "request.json",
        {
            "snapshot": str(public_parent[0]),
            "curated": str(public_parent[1]),
            "qualification": str(qualified[0]),
            "plan": plan.model_dump(mode="json"),
            "generated": generated.model_dump(mode="json"),
        },
    )
    entry = Path(worker.__file__).with_name("campaign_stockout_preparation_entry.py")
    for phase in ("public", "roles", "verify"):
        result = subprocess.run(
            [sys.executable, "-I", "-B", str(entry), phase, str(root)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == 0, (phase, result.stdout, result.stderr)
        assert read(root / (phase + ".json"))["worker_peak_rss_bytes"] > 0
    return root, plan, generated


def test_fresh_phases_accept_complete_original_role_artifact(prepared_worker):
    root, plan, generated = prepared_worker
    result = read(root / "verify.json")
    expected = plan.role_plan(generated, result["public_dataset_id"])
    assert result["role_plan"] == expected.model_dump(mode="json")
    manifest = verify_stockout_roles(
        root / "bundle",
        expected_dataset_id=result["dataset_id"],
        expected_plan=expected,
        allow_evaluation_truth=True,
    )
    assert manifest["descriptor"]["maturity_receipt"]["rows"] > 0
    assert not manifest["journal_authorization_proven"] and not manifest["stage_ready"]


def test_wrong_generation_cannot_start_public_preparation(prepared_worker, monkeypatch):
    root, _, _ = prepared_worker
    request = read(root / "request.json")
    request["generated"]["source_recipe_sha256"] = "d" * 64
    monkeypatch.setattr(
        worker, "prepare_public_stockout", lambda *a, **k: pytest.fail("parent I/O")
    )
    with pytest.raises(SnapshotError, match="runtime_mismatch"):
        worker.execute(root, "public", request)
