"""Permission boundaries, immutable gates, and complete receipts on mechanics fixtures."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.stockout.split import SplitPolicy
from retailops_ai.stockout_campaign import assembly, evaluation
from retailops_ai.stockout_campaign.contract import (
    CampaignFreeze,
    CampaignPermission,
    SourceRef,
    require_permission,
)
from retailops_ai.stockout_campaign.implementation import code_digest, lock_digest
from retailops_ai.stockout_campaign.runner import run_world
from retailops_ai.stockout_runtime.contracts import ScoringPolicy, ScoringRecipe

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 5, tzinfo=UTC)


@pytest.fixture
def recipes():
    return (
        ScoringRecipe.model_validate_json(
            (ROOT / "docs/reference/stockout-final-selected-recipe.json").read_bytes()
        ),
        ScoringPolicy.model_validate_json(
            (ROOT / "docs/reference/stockout-final-selected-policy.json").read_bytes()
        ),
    )


def seal(value):
    raw = value.model_dump(mode="json")
    raw["campaign_id"] = "stockout-final-campaign-sha256-" + canonical_sha256(
        {k: v for k, v in raw.items() if k != "campaign_id"}
    )
    return CampaignFreeze.model_validate_json(canonical_bytes(raw))


@pytest.fixture
def frozen(recipes):
    recipe, policy = recipes
    sources = []
    for i, (world, seed) in enumerate(
        (w, s) for w in ("matching", "future_stress") for s in (42, 137, 2026)
    ):
        checksum = str(i + 1) * 64
        sources.append(
            SourceRef.model_validate_json(
                canonical_bytes(
                    dict(
                        world=world,
                        seed=seed,
                        workflow_run_id=i + 1,
                        artifact_id=i + 1,
                        artifact_bytes=1,
                        artifact_sha256=checksum,
                        checkpoint_sha256=checksum,
                        resource_sha256=checksum,
                        producer_commit="08639e9188badb352ed64686a088fe237badad41"
                        if world == "matching"
                        else "61eb215193106cc3f41e6b79e0470585cc9e791b",
                        consumer_commit="4faaf4b6c1997fda3a609645595165643bf302a9",
                        source_dataset_id="source-sha256-" + checksum,
                        curated_dataset_id="curated-sha256-" + checksum,
                        qualification_id="inventory-labels-sha256-" + checksum,
                        feature_bundle_id="feature-partitions-sha256-" + checksum,
                        upstream_bundle_id="upstream-partitions-sha256-" + checksum,
                        label_bundle_id="label-partitions-sha256-" + checksum,
                        temporal_bundle_id="temporal-partitions-sha256-" + checksum,
                        eligible_test_membership=4,
                        split_policy=SplitPolicy(
                            start_at="2026-04-21T00:00:00Z",
                            train_until="2026-06-05T00:00:00Z",
                            tune_until="2026-06-24T00:00:00Z",
                            calibration_until="2026-07-13T00:00:00Z",
                            test_until="2026-08-01T00:00:00Z",
                            evaluated_at="2026-08-01T00:00:00Z",
                        ).model_dump(mode="json"),
                    )
                )
            )
        )
    fixture = CampaignFreeze.model_construct(
        campaign_id="stockout-final-campaign-sha256-" + "0" * 64,
        prepared_at=NOW,
        sources=tuple(sources),
        selection_id=recipe.pin.selection_id,
        selection_content_sha256="1" * 64,
        recipe_content_sha256=canonical_sha256(recipe.model_dump(mode="json")),
        policy_content_sha256=canonical_sha256(policy.model_dump(mode="json")),
        evaluator_code_sha256=code_digest(),
        dependency_lock_sha256=lock_digest(),
        expected_categories=recipe.pipeline.calibrator.categories,
        expected_stock_locations=recipe.pipeline.calibrator.stock_locations,
    )
    freeze = seal(fixture)
    permission = CampaignPermission(
        campaign_id=freeze.campaign_id,
        approved_by="fixture-reviewer",
        approved_at=NOW,
        authorization_evidence="Synthetic mechanics fixture only; no genuine final source permission.",
        final_outcome_access=True,
        thresholds_and_capacity_approved=True,
    )
    return freeze, permission


def test_permission_identity_and_time_are_checked_before_any_parent_read(
    frozen, tmp_path, monkeypatch
):
    freeze, permission = frozen

    def refuse(*args, **kwargs):
        pytest.fail("private parent must not be opened")

    monkeypatch.setattr(assembly, "read_json", refuse)
    for approved in (
        None,
        permission.model_copy(update={"campaign_id": "stockout-final-campaign-sha256-" + "f" * 64}),
        permission.model_copy(update={"approved_at": NOW - timedelta(seconds=1)}),
    ):
        with pytest.raises(ValueError, match="permission"):
            assembly.assemble_final(
                tmp_path / "missing",
                None,
                freeze=freeze,
                permission=approved,
                source=freeze.sources[0],
            )
    require_permission(freeze, permission)


def test_changed_code_source_and_weakened_gates_are_rejected(frozen, tmp_path):
    freeze, permission = frozen
    changed = seal(freeze.model_copy(update={"evaluator_code_sha256": "f" * 64}))
    approved = permission.model_copy(update={"campaign_id": changed.campaign_id})
    with pytest.raises(ValueError, match="code_or_lock_changed"):
        assembly.assemble_final(
            tmp_path / "missing",
            None,
            freeze=changed,
            permission=approved,
            source=changed.sources[0],
        )
    foreign = freeze.sources[0].model_copy(update={"artifact_id": 777})
    with pytest.raises(ValueError, match="source_not_in_campaign"):
        assembly.guard(freeze, permission, foreign)
    raw = freeze.model_dump(mode="json")
    raw["quality_requirements"]["minimum_segment_rows"] = 19
    with pytest.raises(ValidationError):
        CampaignFreeze.model_validate_json(canonical_bytes(raw))


def test_no_permission_creates_no_audit_download_or_report(frozen, recipes, tmp_path):
    freeze, _ = frozen
    with pytest.raises(ValueError, match="permission_required"):
        run_world(
            freeze=freeze,
            permission=None,
            source=freeze.sources[0],
            recipe=recipes[0],
            policy=recipes[1],
            archive=tmp_path / "archive.zip",
            report=tmp_path / "report.json",
            audit=tmp_path / "audit.jsonl",
        )
    assert not list(tmp_path.iterdir())


def test_portable_prediction_never_fits_and_small_segments_stay_not_ready(
    frozen, recipes, monkeypatch
):
    from retailops_ai.stockout_selection import pipeline as conditional
    from retailops_ai.stockout_training import pipeline as base

    freeze, permission = frozen
    recipe, policy = recipes

    def refuse(*args, **kwargs):
        pytest.fail("final evaluation must never fit")

    monkeypatch.setattr(base, "fit_model", refuse)
    monkeypatch.setattr(base, "fit_sigmoid", refuse)
    monkeypatch.setattr(conditional, "fit_conditional", refuse)
    from retailops_ai.stockout.feature_contract import FeatureValues

    rows = []
    for i in range(4):
        values = {k: None for k in FeatureValues.model_fields}
        values["history_constrained_days"] = i % 2
        values["available_qty"] = 10 + i
        values.update(forecast_units_7d=8.0, forecast_days_of_supply=1.0, forecast_unavailable=0)
        rows.append(
            dict(
                product_id="fixture-sku-" + str(i),
                stock_location_id=recipe.pipeline.calibrator.stock_locations[0],
                category_id=recipe.pipeline.calibrator.categories[0],
                as_of="2026-07-25T23:59:59Z",
                values=values,
            )
        )
    data = assembly.FinalData(
        rows,
        [0, 1, 0, 1],
        dict(eligible=4),
        "1" * 64,
        "2" * 64,
        {k: [] for k in ("promotion_plans", "fulfillment_routes", "assortment")},
    )
    result = evaluation.evaluate_final(
        data,
        freeze=freeze,
        permission=permission,
        source=freeze.sources[0],
        recipe=recipe,
        policy=policy,
    )
    assert result["model_refits"] == 0 and not result["independent_quality_accepted"]
    assert result["status"] == "not_ready" and result["final_test_outcomes_evaluated"]
    assert "capacity" not in result["metrics"]["all"]
    assert "capacity_from_same_global_queue" in result["metrics"]["all"]
    assert result["segment_gates"]["data_role"] == "authorized_final_test"


def test_aggregate_keeps_each_failed_world_and_refuses_missing_or_forged_gates(frozen, recipes):
    from retailops_ai.stockout_campaign.report import aggregate

    freeze, permission = frozen
    recipe, policy = recipes
    rows = [
        dict(
            product_id="fixture-" + str(i),
            stock_location_id=recipe.pipeline.calibrator.stock_locations[0],
            category_id=recipe.pipeline.calibrator.categories[0],
            as_of="2026-07-25T23:59:59Z",
            values={
                **{k: None for k in recipe.pipeline.base.preprocessing.numeric_columns},
                "history_constrained_days": i % 2,
                "available_qty": 10,
                "forecast_unavailable": 0,
            },
        )
        for i in range(4)
    ]
    data = assembly.FinalData(
        rows,
        [0, 1, 0, 1],
        dict(eligible=4),
        "1" * 64,
        "2" * 64,
        {k: [] for k in ("promotion_plans", "fulfillment_routes", "assortment")},
    )
    reports = [
        evaluation.evaluate_final(
            data, freeze=freeze, permission=permission, source=s, recipe=recipe, policy=policy
        )
        for s in freeze.sources
    ]
    combined = aggregate(reports, freeze=freeze, permission=permission)
    assert (
        combined["content"]["status"] == "not_ready" and len(combined["content"]["blockers"]) == 6
    )
    assert (
        combined["content"]["no_pooled_quality_claim"]
        and not combined["content"]["independent_quality_accepted"]
    )
    with pytest.raises(ValueError, match="six_complete"):
        aggregate(reports[:-1], freeze=freeze, permission=permission)
    from copy import deepcopy

    missing = deepcopy(reports)
    del missing[0]["segment_gates"]["segments"]["historical_inventory_constraint:true"]
    with pytest.raises(ValueError, match="required_gate_missing"):
        aggregate(missing, freeze=freeze, permission=permission)
    forged = deepcopy(reports)
    forged[0]["status"] = "passed"
    forged[0]["independent_quality_accepted"] = True
    with pytest.raises(ValueError, match="status_disagrees"):
        aggregate(forged, freeze=freeze, permission=permission)
