"""Public bounded runner/worker/file verification; declared tiny sources/models only."""

import json

import pytest
from pydantic import ValidationError
from test_campaign_evaluation import control as control
from test_campaign_robust_forecast import declared_context as declared_context
from test_campaign_robust_forecast import robust_execute
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_evaluation as runner
from retailops_ai.evaluation_campaign import campaign_evaluation_worker as worker
from retailops_ai.evaluation_campaign.campaign_robust_receipt import (
    CampaignForecastRequiredGroupEvaluationReceipt,
    parse_forecast_evaluation_receipt,
)
from retailops_ai.source_snapshot.files import SnapshotError


@pytest.mark.parametrize(
    "control",
    [
        "portfolio-ordinary-required-robust",
        "portfolio-demand-required-robust",
        "portfolio-physical-required-robust",
    ],
    indirect=True,
)
def test_public_required_groups_bind_protocol_worker_receipt_and_all_actual_reports(
    control, declared_context
):
    bundle, receipt = robust_execute(
        control, declared_context, required_group_policy=control["required_group_policy"]
    )
    assert isinstance(receipt, CampaignForecastRequiredGroupEvaluationReceipt)
    assert (
        receipt.required_group_policy.content_sha256()
        == control["protocol"].selection_policy_sha256
    )
    assert receipt.portfolio_protocol == control["protocol"]
    report = json.loads((bundle / "metrics.json").read_bytes())["selected_robustness"]
    assert report["required_group_policy_sha256"] == receipt.required_group_policy.content_sha256()
    assert len(report["groups"]) > len(report["required_groups"])
    assert receipt.critical_segment_inventory_complete and receipt.block_uncertainty_complete
    assert (
        not receipt.quality_qualified
        and not receipt.stage_ready
        and not receipt.final_test_accessed
    )
    assert not receipt.training_or_preprocessing_refitted
    assert (bundle / "required-groups.json").is_file() and (
        bundle / "portfolio-protocol.json"
    ).is_file()
    runner.verify_campaign_forecast_evaluation(bundle, journal=control["root"], receipt=receipt)
    assert (
        parse_forecast_evaluation_receipt(canonical_bytes(receipt.model_dump(mode="json")))
        == receipt
    )


@pytest.mark.parametrize("control", ["portfolio-ordinary-required-robust"], indirect=True)
@pytest.mark.parametrize("attack", ["missing-uncertainty", "changed-policy"])
def test_policy_not_frozen_before_role_workers_is_rejected(control, declared_context, attack):
    policy = control["required_group_policy"]
    changes = {"required_group_policy": policy}
    if attack == "missing-uncertainty":
        changes["uncertainty_policy"] = None
    else:
        value = policy.model_dump(mode="json")
        value["sources"][0]["required"].append({"dimension": "history", "value": "cold_start"})
        value["sources"][0]["required"].sort(key=lambda g: (g["dimension"], g["value"]))
        changes["required_group_policy"] = type(policy).model_validate_json(canonical_bytes(value))
    with pytest.raises(SnapshotError):
        robust_execute(control, declared_context, **changes)
    assert not {"prepare", "predict", "consume", "finalize"}.intersection(control["calls"])


@pytest.mark.parametrize("control", ["portfolio-ordinary-required-robust"], indirect=True)
@pytest.mark.parametrize("artifact", ["required-groups.json", "portfolio-protocol.json"])
def test_resealed_policy_or_protocol_cannot_pass_public_receipt_verifier(
    control, declared_context, artifact
):
    bundle, receipt = robust_execute(
        control, declared_context, required_group_policy=control["required_group_policy"]
    )
    (bundle / artifact).write_bytes(b"{}\n")
    with pytest.raises(SnapshotError):
        runner.verify_campaign_forecast_evaluation(bundle, journal=control["root"], receipt=receipt)
    raw = receipt.model_dump(mode="json")
    raw["version"] = "ai09-campaign-portfolio-forecast-robust-evaluation-receipt-1.0.0"
    with pytest.raises(ValidationError):
        parse_forecast_evaluation_receipt(canonical_bytes(raw))


@pytest.mark.parametrize("control", ["portfolio-ordinary-required-robust"], indirect=True)
def test_required_group_metadata_cannot_enter_model_inference(control, tmp_path):
    plan = control["recipe"].resolve(control["configuration"])
    with pytest.raises(SnapshotError, match="inference_request_contains_outcomes"):
        worker.predict(
            tmp_path,
            {"required_group_policy": control["required_group_policy"].model_dump(mode="json")},
            plan,
        )
