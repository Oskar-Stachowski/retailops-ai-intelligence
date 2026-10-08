"""Real bounded role/SQL/worker controls, declared sources/models, no Project proof."""

import copy

import pytest
from pydantic import ValidationError
from test_campaign_evaluation import control as control
from test_campaign_evaluation import execute
from test_campaign_robust_forecast import declared_context as declared_context
from test_campaign_robust_forecast import robust_execute
from test_forecast_features import tables as tables
from test_forecast_manifests import timeline as timeline
from test_independent_forecast_partitions import population as population
from test_physical_forecast import stored_control as stored_control

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_evaluation as runner
from retailops_ai.evaluation_campaign import campaign_evaluation_worker as worker
from retailops_ai.evaluation_campaign import campaign_journal as journal
from retailops_ai.evaluation_campaign.campaign_evaluation_receipt import (
    CampaignForecastEvaluationReceipt,
)
from retailops_ai.evaluation_campaign.campaign_portfolio_evaluation_contract import (
    CampaignPortfolioForecastEvaluationBinding,
)
from retailops_ai.evaluation_campaign.campaign_robust_receipt import (
    CampaignForecastPortfolioEvaluationReceipt,
    CampaignForecastPortfolioRobustEvaluationReceipt,
    is_robust_evaluation,
    parse_forecast_evaluation_receipt,
)
from retailops_ai.source_snapshot.files import SnapshotError


@pytest.mark.parametrize(
    "control", ["portfolio-ordinary", "portfolio-demand", "portfolio-physical"], indirect=True
)
def test_public_portfolio_keeps_training_parents_and_evaluates_every_variant_key(control):
    before = {key: fit.content_sha256() for key, fit in control["fits"].items()}
    bundle, receipt = execute(control)
    assert isinstance(receipt, CampaignForecastPortfolioEvaluationReceipt)
    assert not is_robust_evaluation(receipt)
    binding = receipt.portfolio_binding
    assert binding.matches(receipt.configuration, receipt.plan)
    assert binding.training_dataset_id == control["training_exported"].dataset_id
    assert binding.evaluation_dataset_id == control["exported"].dataset_id
    assert binding.protocol == journal.inspect(control["root"]).protocol
    assert (binding.training_dataset_id == binding.evaluation_dataset_id) == (
        receipt.plan.source_recipe_sha256 == receipt.configuration.development_source_recipe_sha256
    )
    assert {key: fit.content_sha256() for key, fit in control["fits"].items()} == before
    assert receipt.actual_index_passes == len(receipt.configuration.trials) + 1
    assert receipt.all_models_share_all_role_keys and receipt.all_frozen_trials_compared
    assert not receipt.training_or_preprocessing_refitted and not receipt.final_test_accessed
    assert not receipt.quality_qualified and not receipt.stage_ready
    assert (bundle / "portfolio.json").is_file()
    runner.verify_campaign_forecast_evaluation(bundle, journal=control["root"], receipt=receipt)
    assert (
        parse_forecast_evaluation_receipt(canonical_bytes(receipt.model_dump(mode="json")))
        == receipt
    )
    assert control["calls"][-6:] == [
        "prepare",
        "predict",
        "consume",
        "predict",
        "consume",
        "finalize",
    ]
    # Legacy receipt remains strict: the new scope cannot be relabelled as v20.
    raw = receipt.model_dump(mode="json")
    raw["version"] = "ai09-campaign-forecast-evaluation-receipt-1.0.0"
    with pytest.raises(ValidationError):
        CampaignForecastEvaluationReceipt.model_validate_json(canonical_bytes(raw))


@pytest.mark.parametrize(
    "control",
    ["portfolio-ordinary-robust", "portfolio-demand-robust", "portfolio-physical-robust"],
    indirect=True,
)
def test_robust_portfolio_retains_all_groups_uncertainty_and_underpowered_rejection(
    control, declared_context
):
    bundle, receipt = robust_execute(control, declared_context)
    assert isinstance(receipt, CampaignForecastPortfolioRobustEvaluationReceipt)
    assert is_robust_evaluation(receipt)
    assert receipt.critical_segment_inventory_complete and receipt.block_uncertainty_complete
    assert not receipt.quality_qualified and not receipt.stage_ready
    runner.verify_campaign_forecast_evaluation(bundle, journal=control["root"], receipt=receipt)
    assert (
        parse_forecast_evaluation_receipt(canonical_bytes(receipt.model_dump(mode="json")))
        == receipt
    )


@pytest.mark.parametrize("control", ["portfolio-demand"], indirect=True)
@pytest.mark.parametrize(
    "attack",
    ["training-source", "same-dataset", "foreign-evaluation-source", "recipe", "quality-policy"],
)
def test_invalid_transfer_is_charged_before_role_io(control, monkeypatch, attack):
    changes = {}
    if attack == "training-source":
        changes["configuration"] = control["configuration"].model_copy(
            update={"development_source_recipe_sha256": control["recipe"].source_recipe_sha256}
        )
    elif attack == "same-dataset":
        changes["exported"] = control["exported"].model_copy(
            update={"dataset_id": control["training_exported"].dataset_id}
        )
    elif attack == "foreign-evaluation-source":
        changes["recipe"] = control["recipe"].model_copy(update={"source_recipe_sha256": "f" * 64})
    elif attack == "recipe":
        changes["recipe"] = control["recipe"].model_copy(update={"batch_windows": 1})
    else:
        changes["configuration"] = control["configuration"].model_copy(
            update={"quality_policy_sha256": "f" * 64}
        )

    def forbidden(*args, **kwargs):
        raise AssertionError("invalid transfer must fail before role or model I/O")

    monkeypatch.setattr(worker, "prepare", forbidden)
    with pytest.raises((SnapshotError, ValueError)):
        execute(control, **changes)
    event = journal.inspect(control["root"]).events[-1]
    assert event.result == "failed" and event.cost.wall_seconds > 0
    assert not {"prepare", "predict", "consume", "finalize"}.intersection(control["calls"])


@pytest.mark.parametrize("control", ["portfolio-physical"], indirect=True)
def test_worker_requires_the_full_matching_preregistered_binding(control):
    configuration = control["configuration"]
    plan = control["recipe"].resolve(configuration)
    binding = CampaignPortfolioForecastEvaluationBinding(
        protocol=control["protocol"],
        operation_id="independent-evaluate",
        recipe=control["recipe"],
        training_dataset_id=configuration.development_dataset_id,
        evaluation_dataset_id=control["exported"].dataset_id,
    )
    request = {
        "configuration": configuration.model_dump(mode="json"),
        "runtime": control["protocol"].runtime.model_dump(mode="json"),
        "portfolio_binding": binding.model_dump(mode="json"),
    }
    assert worker._configuration(request, plan) == configuration
    raw = copy.deepcopy(request)
    del raw["portfolio_binding"]
    with pytest.raises(SnapshotError, match="configuration_plan_mismatch"):
        worker._configuration(raw, plan)
    for path in ["training_dataset_id", "evaluation_dataset_id"]:
        raw = copy.deepcopy(request)
        raw["portfolio_binding"][path] = "ai09-physical-forecast-sha256-" + "f" * 64
        if path == "evaluation_dataset_id":
            raw["portfolio_binding"][path] = binding.training_dataset_id
        with pytest.raises(SnapshotError, match="configuration_plan_mismatch"):
            worker._configuration(raw, plan)
    raw = copy.deepcopy(request)
    raw["portfolio_binding"]["protocol"]["portfolio_version"] = "unsupported"
    with pytest.raises(ValidationError):
        worker._configuration(raw, plan)


@pytest.mark.parametrize("control", ["portfolio-demand"], indirect=True)
def test_portfolio_metadata_cannot_enter_inference_request(control, monkeypatch, tmp_path):
    def forbidden(request):
        raise AssertionError("unexpected metadata must be rejected before loading models")

    monkeypatch.setattr(worker, "load_models", forbidden)
    plan = control["recipe"].resolve(control["configuration"])
    with pytest.raises(SnapshotError, match="inference_request_contains_outcomes"):
        worker.predict(
            tmp_path, {"portfolio_binding": control["protocol"].model_dump(mode="json")}, plan
        )


@pytest.mark.parametrize("control", ["portfolio-demand"], indirect=True)
def test_portfolio_artifact_is_required_and_tampering_cannot_pass_public_verification(control):
    bundle, receipt = execute(control)
    (bundle / "portfolio.json").write_bytes(b"{}\n")
    with pytest.raises(SnapshotError, match="checksum"):
        runner.verify_campaign_forecast_evaluation(bundle, journal=control["root"], receipt=receipt)
