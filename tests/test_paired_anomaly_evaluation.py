"""Paired truth keeps the full native census and cannot use the legacy proof-free wire."""

import pytest
from pydantic import ValidationError
from test_anomaly_portfolio_model import saved_model as saved_model
from test_campaign_anomaly_evaluation import labels, plan, rows

from retailops_ai.anomaly_evaluation.contract import PairedTruth, Truth
from retailops_ai.anomaly_evaluation.paired_source_comparison import POLICY
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_anomaly_evaluation as evaluator

PAIR_FIELDS = (
    "ordinary_source_dataset_id",
    "source_verification_sha256",
    "ordinary_generation_receipt_sha256",
    "source_generation_receipt_sha256",
    "comparison_policy_sha256",
)


def truth(*, unknown=False):
    old = labels(episodes=not unknown)
    return PairedTruth(
        **old.model_dump(exclude={"version", "complete_windows"}),
        ordinary_source_dataset_id="source-sha256-" + "a" * 64,
        source_verification_sha256="7" * 64,
        ordinary_generation_receipt_sha256="8" * 64,
        source_generation_receipt_sha256="9" * 64,
        comparison_policy_sha256=canonical_sha256(POLICY),
        complete_windows=() if unknown else old.complete_windows,
    )


def paired_plan(model, actual, family="seasonal_residual"):
    value = plan(model, actual, family).model_dump(mode="json")
    value.update(version="ai09-native-paired-anomaly-census-plan-1.0.0")
    value.update({key: getattr(actual, key) for key in PAIR_FIELDS})
    return evaluator.CampaignPairedAnomalyCensusPlan.model_validate_json(canonical_bytes(value))


@pytest.mark.parametrize("family", ["seasonal_residual", "isolation_forest"])
def test_real_saved_model_score_replay_keeps_explicit_paired_proof(saved_model, family):
    actual = truth()
    frozen = paired_plan(saved_model, actual, family)
    scored = rows(saved_model, frozen)
    result = evaluator.evaluate_anomaly_census(saved_model, scored, actual, frozen)
    assert isinstance(result, evaluator.CampaignPairedAnomalyCensusEvaluation)
    assert result.rows == frozen.rows == 4
    assert not result.quality_qualified and not result.parent_source_verified
    evaluator.verify_anomaly_census_evaluation(
        result, model=saved_model, scored=scored, truth=actual, plan=frozen
    )
    with pytest.raises(ValidationError):
        evaluator.CampaignAnomalyCensusEvaluation.model_validate_json(result.model_dump_json())
    with pytest.raises(ValidationError):
        Truth.model_validate_json(actual.model_dump_json())


def test_all_unknown_pair_counts_every_observation_without_invented_negatives(saved_model):
    actual = truth(unknown=True)
    frozen = paired_plan(saved_model, actual)
    result = evaluator.evaluate_anomaly_census(
        saved_model, rows(saved_model, frozen), actual, frozen
    )
    report = result.native_evaluation["descriptor"]
    assert result.rows == 4 and report["coverage"]["unknown_truth"] == 4
    assert report["observation"]["clean_observations"] == 0
    assert report["observation"]["false_alerts_per_1000"]["value"] is None
    assert report["observation"]["recall"]["value"] is None


@pytest.mark.parametrize("field", PAIR_FIELDS)
def test_resealed_truth_cannot_change_any_independent_pair_binding(saved_model, field):
    actual = truth()
    frozen = paired_plan(saved_model, actual)
    changed = actual.model_copy(
        update={
            field: "source-sha256-" + "0" * 64
            if field == "ordinary_source_dataset_id"
            else "0" * 64
        }
    )
    frozen = frozen.model_copy(
        update={"truth_sha256": canonical_sha256(changed.model_dump(mode="json"))}
    )
    with pytest.raises(ValueError, match="binding"):
        evaluator.evaluate_anomaly_census(saved_model, rows(saved_model, frozen), changed, frozen)


def test_paired_truth_cannot_silently_use_the_legacy_census_plan(saved_model):
    actual = truth()
    legacy = plan(saved_model, actual)
    with pytest.raises(ValueError, match="model_truth_or_cutoff_binding"):
        evaluator.evaluate_anomaly_census(saved_model, rows(saved_model, legacy), actual, legacy)
