"""Only prospectively approved, supported small-category calibration can warn."""

from copy import deepcopy

import pytest
import test_stockout_final_campaign as campaign_fixtures
from pydantic import ValidationError
from test_stockout_final_campaign import seal

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.stockout_campaign.contract import (
    BalancedQualityRequirements,
    CampaignFreeze,
    require_permission,
)
from retailops_ai.stockout_campaign.gates import quality_gates
from retailops_ai.stockout_campaign.report import aggregate
from retailops_ai.stockout_lifecycle.evidence import verify_world_gates
from retailops_ai.stockout_selection.contract import QualityRequirements

recipes = campaign_fixtures.recipes
base_frozen = campaign_fixtures.frozen
CATEGORIES = {"cat-" + str(i) for i in range(8)}
LOCATIONS = {"stock-1", "stock-2"}


def metric(n=50, ece=0.05, positives=None):
    positives = n // 2 if positives is None else positives
    prevalence = positives / n
    return dict(
        rows=n,
        positives=positives,
        negatives=n - positives,
        status="evaluable",
        prevalence=prevalence,
        average_precision=0.8,
        brier=0.1,
        train_prevalence_constant_brier=0.26,
        reliability=[dict(count=n, mean_probability=prevalence + ece, observed_rate=prevalence)],
    )


def measurements():
    return {
        name: metric()
        for name in {
            "all",
            "historical_inventory_constraint:true",
            *("category:" + c for c in CATEGORIES),
            *("stock_location:" + s for s in LOCATIONS),
        }
    }


def gates(values, policy=None, categories=None, stocks=None):
    return quality_gates(
        values,
        expected_categories=categories or CATEGORIES,
        expected_locations=stocks or LOCATIONS,
        policy=policy or BalancedQualityRequirements(),
    )


@pytest.mark.parametrize(
    "n,ece,status",
    [
        (50, 0.15, "passed"),
        (50, 0.1500001, "warning"),
        (99, 0.2, "warning"),
        (100, 0.16, "failed"),
        (50, 0.2000001, "failed"),
        (19, 0.16, "not_evaluable"),
    ],
)
def test_category_warning_boundaries(n, ece, status):
    values = measurements()
    values["category:cat-0"] = metric(n, ece)
    result = gates(values)
    gate = result["segments"]["category:cat-0"]
    assert gate["status"] == status
    assert result["status"] == ("passed" if status in {"passed", "warning"} else "not_ready")
    if status == "warning":
        assert gate["checks"]["calibration_error_in_budget"] is False
        assert gate["warning_policy"] == "stockout-balanced-quality-2.0.0"


@pytest.mark.parametrize(
    "name", ["all", "stock_location:stock-1", "historical_inventory_constraint:true"]
)
def test_global_location_and_constraint_calibration_are_hard(name):
    values = measurements()
    values[name] = metric(50, 0.16)
    assert gates(values)["segments"][name]["status"] == "failed"


@pytest.mark.parametrize("change", ["AP", "Brier", "class_support", "missing", "strict_v1"])
def test_warning_cannot_conceal_other_failures_or_apply_to_v1(change):
    values = measurements()
    values["category:cat-0"] = metric(50, 0.16)
    if change == "AP":
        values["category:cat-0"]["average_precision"] = 0.4
    elif change == "Brier":
        values["category:cat-0"]["brier"] = 0.3
    elif change == "class_support":
        values["category:cat-0"] = metric(50, 0.16, positives=4)
    elif change == "missing":
        del values["category:cat-0"]
    result = gates(values, QualityRequirements() if change == "strict_v1" else None)
    assert result["status"] == "not_ready"
    assert result["segments"]["category:cat-0"]["status"] != "warning"


@pytest.fixture
def balanced(base_frozen):
    freeze, permission = base_frozen
    freeze = seal(
        freeze.model_copy(
            update={
                "version": "stockout-final-campaign-2.0.0",
                "quality_requirements": BalancedQualityRequirements(),
                "sources": tuple(
                    s.model_copy(update={"eligible_test_membership": 50}) for s in freeze.sources
                ),
            }
        )
    )
    permission = permission.model_copy(
        update={
            "version": "stockout-final-permission-2.0.0",
            "campaign_id": freeze.campaign_id,
            "small_category_warnings_approved": True,
        }
    )
    return freeze, permission


def test_v2_requires_exact_warning_policy_and_separate_owner_acceptance(balanced):
    freeze, permission = balanced
    require_permission(freeze, permission)
    for updates in (
        {"small_category_warnings_approved": False},
        {"version": "stockout-final-permission-1.0.0"},
    ):
        with pytest.raises(ValueError, match="permission"):
            require_permission(freeze, permission.model_copy(update=updates))
    raw = freeze.model_dump(mode="json")
    raw["quality_requirements"]["small_category_warning_maximum_ece"] = 0.21
    raw["campaign_id"] = "stockout-final-campaign-sha256-" + canonical_sha256(
        {k: v for k, v in raw.items() if k != "campaign_id"}
    )
    with pytest.raises(ValidationError):
        CampaignFreeze.model_validate_json(canonical_bytes(raw))


def test_aggregate_retains_warnings_and_cold_replay_rejects_a_changed_warning(balanced):
    freeze, permission = balanced
    reports = []
    for source in freeze.sources:
        values = {
            name: metric()
            for name in {
                "all",
                "historical_inventory_constraint:true",
                *("category:" + c for c in freeze.expected_categories),
                *("stock_location:" + s for s in freeze.expected_stock_locations),
            }
        }
        category = "category:" + freeze.expected_categories[0]
        values[category] = metric(50, 0.16)
        checks = gates(
            values,
            freeze.quality_requirements,
            set(freeze.expected_categories),
            set(freeze.expected_stock_locations),
        )
        segment = {
            k: checks[k] for k in ("status", "segments", "required_segment_universe_complete")
        }
        segment["data_role"] = "authorized_final_test"
        scenarios = {}
        if source.world == "future_stress":
            for name in ("normal", "promotion", "demand_shock", "inventory_constraint"):
                values["scenario:" + name] = metric()
                scenarios[name] = dict(
                    status="passed",
                    checks=dict(
                        support=True,
                        AP_above_no_skill=True,
                        brier_better_than_train_constant=True,
                        calibration_error_in_budget=True,
                    ),
                )
            for name in ("promotion", "demand_shock", "inventory_constraint"):
                values["scenario:control:" + name] = metric()
                scenarios["control:" + name] = dict(
                    status="passed",
                    rows=50,
                    interpretation="coverage_control_no_causal_effect_claim",
                )
        report = dict(
            schema_version="stockout-final-world-report-1.0.0",
            campaign_id=freeze.campaign_id,
            world=source.world,
            seed=source.seed,
            source=source.model_dump(mode="json"),
            selection_id=freeze.selection_id,
            recipe_content_sha256=freeze.recipe_content_sha256,
            policy_content_sha256=freeze.policy_content_sha256,
            execution_code_sha256=freeze.evaluator_code_sha256,
            dependency_lock_sha256=freeze.dependency_lock_sha256,
            permission_sha256=canonical_sha256(permission.model_dump(mode="json")),
            coverage=dict(eligible=50),
            metrics=values,
            segment_gates=segment,
            scenario_gates=scenarios,
            status="passed",
            independent_quality_accepted=True,
            model_refits=0,
            recalibration=False,
            thresholds_changed=False,
            source_generation=False,
            final_test_outcomes_evaluated=True,
            model_promoted=False,
            ai08_ready=False,
        )
        verify_world_gates(report, freeze)
        reports.append(report)
    result = aggregate(reports, freeze=freeze, permission=permission)
    assert result["content"]["status"] == "passed"
    assert len(result["content"]["warnings"]) == 6
    changed = deepcopy(reports)
    changed[0]["segment_gates"]["segments"][category]["calibration_warning_budget"] = 0.21
    with pytest.raises(ValueError, match="warning_not_prospectively"):
        aggregate(changed, freeze=freeze, permission=permission)
    with pytest.raises(ValueError, match="measurement_changed"):
        verify_world_gates(changed[0], freeze)
    changed = deepcopy(reports[-1])
    changed["metrics"]["scenario:normal"] = metric(50, 0.16)
    with pytest.raises(ValueError, match="scenario_gate_measurement_changed"):
        verify_world_gates(changed, freeze)
