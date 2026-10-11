"""Ordinary negatives require native complete days; missing grains stay unknown."""

from uuid import UUID

import pytest
from pydantic import ValidationError
from test_anomaly_detectors import scope
from test_anomaly_portfolio_model import saved_model as saved_model
from test_campaign_anomaly_evaluation import SOURCE_ID, labels, plan, rows

from retailops_ai.anomaly_evaluation.contract import OrdinaryTruth, Truth
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_anomaly_evaluation as evaluation
from retailops_ai.evaluation_campaign.campaign_anomaly_truth_worker import complete_ordinary_windows


def tables(*, missing=False, purchase=True):
    key = scope().model_dump(exclude={"event_type"})
    observations = [
        {
            **key,
            "business_date": f"2026-01-0{n}",
            "source_data_complete": not (missing and n == 2),
            "quality_status": "valid",
            "available_at": f"2026-01-0{n + 1}T00:00:00+00:00",
        }
        for n in (1, 2, 3)
    ]
    return {
        "daily_demand_observations": observations,
        "inventory_sales": [
            {
                **key,
                "sold_at": "2026-01-01T12:00:00+00:00",
                "available_at": "2026-01-01T12:00:00+00:00",
            }
        ]
        if purchase
        else [],
        "product_catalog": [{"id": key["product_id"], "category_id": "category"}],
        "return_policies": [
            {
                "category_id": "category",
                "channel": key["channel"],
                "window_days": 2,
                "max_ingestion_delay_days": 5,
                "known_at": "2025-12-01T00:00:00+00:00",
            }
        ],
        "return_events": [],
    }


def test_native_windows_preserve_missing_day_and_finite_return_tail():
    windows = complete_ordinary_windows(
        tables(missing=True), {"start": "2026-01-01", "end": "2026-01-07"}
    )
    sales = [w for w in windows if w["event_type"] == "sale_completed"]
    returns = [w for w in windows if w["event_type"] == "return_completed"]
    assert [w["window"] for w in sales] == [
        {"start": "2026-01-01", "end": "2026-01-01"},
        {"start": "2026-01-03", "end": "2026-01-03"},
    ]
    assert len(returns) == 1 and returns[0]["window"] == {
        "start": "2026-01-01",
        "end": "2026-01-03",
    }
    assert returns[0]["available_at"] == "2026-01-09T00:00:00Z"


def test_no_purchase_does_not_invent_a_complete_return_series():
    result = complete_ordinary_windows(
        tables(purchase=False), {"start": "2026-01-01", "end": "2026-01-03"}
    )
    assert len(result) == 1 and result[0]["event_type"] == "sale_completed"


@pytest.mark.parametrize("unconverted", ["false", "true", 1, None])
def test_completeness_requires_a_converted_native_boolean(unconverted):
    value = tables(purchase=False)
    value["daily_demand_observations"][1]["source_data_complete"] = unconverted
    windows = complete_ordinary_windows(value, {"start": "2026-01-01", "end": "2026-01-03"})
    assert [w["window"] for w in windows] == [
        {"start": "2026-01-01", "end": "2026-01-01"},
        {"start": "2026-01-03", "end": "2026-01-03"},
    ]


def test_duplicate_native_day_is_not_silently_deduplicated():
    value = tables()
    value["daily_demand_observations"].append(value["daily_demand_observations"][0])
    with pytest.raises(ValueError, match="duplicate_native_day"):
        complete_ordinary_windows(value, {"start": "2026-01-01", "end": "2026-01-03"})


def test_complete_census_over_budget_is_rejected_without_trimming():
    value = tables()
    value["daily_demand_observations"] = []
    sale = value["inventory_sales"][0]
    value["inventory_sales"] = [
        {**sale, "product_id": str(UUID(int=n + 1)), "sold_at": "2027-12-31T12:00:00+00:00"}
        for n in range(1500)
    ]
    value["product_catalog"] = [
        {"id": s["product_id"], "category_id": "category"} for s in value["inventory_sales"]
    ]
    with pytest.raises(ValueError, match="census_budget"):
        complete_ordinary_windows(value, {"start": "2026-01-01", "end": "2027-12-31"})


def ordinary():
    old = labels(episodes=False)
    return OrdinaryTruth(
        source_dataset_id=SOURCE_ID,
        source_verification_sha256="7" * 64,
        source_generation_receipt_sha256="8" * 64,
        complete_windows=old.complete_windows,
    )


def ordinary_plan(model, truth, family="seasonal_residual"):
    raw = plan(model, truth, family).model_dump(mode="json")
    raw.update(
        version="ai09-native-ordinary-anomaly-census-plan-1.0.0",
        source_scenario_sha256=None,
        source_verification_sha256=truth.source_verification_sha256,
        source_generation_receipt_sha256=truth.source_generation_receipt_sha256,
    )
    return evaluation.CampaignOrdinaryAnomalyCensusPlan.model_validate_json(canonical_bytes(raw))


@pytest.mark.parametrize("family", ["seasonal_residual", "isolation_forest"])
def test_real_model_ordinary_false_alerts_without_fictitious_scenario(saved_model, family):
    truth = ordinary()
    p = ordinary_plan(saved_model, truth, family)
    result = evaluation.evaluate_anomaly_census(saved_model, rows(saved_model, p), truth, p)
    assert isinstance(result, evaluation.CampaignOrdinaryAnomalyCensusEvaluation)
    assert result.plan.source_scenario_sha256 is None
    report = result.native_evaluation["descriptor"]
    assert report["coverage"]["unknown_truth"] == 0
    assert report["observation"]["clean_observations"] == 4
    assert report["observation"]["positive_observations"] == 0
    assert report["observation"]["recall"]["value"] is None
    assert report["observation"]["average_precision"]["value"] is None
    assert report["observation"]["false_alerts_per_1000"]["value"] is not None
    evaluation.verify_anomaly_census_evaluation(
        result, model=saved_model, scored=rows(saved_model, p), truth=truth, plan=p
    )
    with pytest.raises(ValidationError):
        evaluation.CampaignAnomalyCensusEvaluation.model_validate_json(result.model_dump_json())
    with pytest.raises(ValidationError):
        Truth.model_validate_json(truth.model_dump_json())
    assert not result.quality_qualified and not result.parent_source_verified


@pytest.mark.parametrize(
    "binding", ["source_verification_sha256", "source_generation_receipt_sha256"]
)
def test_resealed_ordinary_truth_cannot_change_its_independent_parents(saved_model, binding):
    truth = ordinary()
    p = ordinary_plan(saved_model, truth)
    changed = truth.model_copy(update={binding: "0" * 64})
    p = p.model_copy(update={"truth_sha256": canonical_sha256(changed.model_dump(mode="json"))})
    with pytest.raises(ValueError, match="model_truth_or_cutoff_binding"):
        evaluation.evaluate_anomaly_census(saved_model, rows(saved_model, p), changed, p)


def test_ordinary_never_accepts_positive_episodes_or_a_scenario_digest():
    raw = ordinary().model_dump(mode="json")
    for patch in (
        {"episodes": [labels().episodes[0].model_dump(mode="json")]},
        {"source_scenario_sha256": "a" * 64},
    ):
        with pytest.raises(ValidationError):
            OrdinaryTruth.model_validate_json(canonical_bytes(raw | patch))
