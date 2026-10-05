"""Final-card mechanics retain failed quality and never fit or activate a model."""

from datetime import UTC, datetime, timedelta

import pytest
import test_stockout_final_campaign as campaign_fixtures
from test_model_lifecycle import actor
from test_stockout_final_campaign import seal
from test_stockout_final_evidence import receipts as receipts
from test_stockout_runtime import context as context
from test_stockout_runtime import records as records

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.stockout_lifecycle import qualification
from retailops_ai.stockout_lifecycle.card import final_card
from retailops_ai.stockout_lifecycle.evidence import collect, verify_execution_evidence
from retailops_ai.stockout_runtime.contracts import ScoringPolicy, ScoringRecipe
from retailops_ai.stockout_runtime.inputs import PreparedStockoutInputs

base_frozen = campaign_fixtures.frozen
original_recipes = campaign_fixtures.recipes


@pytest.fixture
def selection(original_recipes):
    recipe, _ = original_recipes
    content = dict(
        selected_pipeline=recipe.pipeline.model_dump(mode="json"),
        selected_model_id=recipe.pin.model_id,
        roles_disjoint=True,
        independent_quality_accepted=False,
        final_test_outcomes_evaluated=False,
        model_promoted=False,
        model_ready=False,
        roles={},
        selected="mechanics-only-choice",
        rejected_candidates={},
        comparison={
            "mechanics-only-choice": dict(
                base_model="logistic_regression:with_upstream",
                C=10.0,
                development_selection_gates=dict(status="not_ready"),
                conditional=dict(all=dict(rows=4, status="not_evaluable")),
            )
        },
    )
    descriptor = dict(content_sha256=canonical_sha256(content), parents={})
    return dict(
        schema_version="stockout-selection-capsule-2.0.0",
        parents_full_replay=True,
        independent_quality_accepted=False,
        final_test_outcomes_evaluated=False,
        model_promoted=False,
        ai08_ready=False,
        selection=dict(
            selection_id="stockout-selection-sha256-" + canonical_sha256(descriptor),
            descriptor=descriptor,
            content=content,
        ),
    )


@pytest.fixture
def recipes(original_recipes, selection):
    recipe, policy = original_recipes
    raw = recipe.model_dump(mode="json")
    raw["pin"]["selection_id"] = selection["selection"]["selection_id"]
    recipe = ScoringRecipe.model_validate_json(canonical_bytes(raw))
    raw = policy.model_dump(mode="json", exclude={"policy_id"})
    raw["pin"] = recipe.pin.model_dump(mode="json")
    raw["policy_id"] = "stockout-scoring-policy-sha256-" + canonical_sha256(raw)
    return recipe, ScoringPolicy.model_validate_json(canonical_bytes(raw))


@pytest.fixture
def frozen(base_frozen, selection):
    freeze, permission = base_frozen
    freeze = seal(
        freeze.model_copy(
            update={
                "selection_content_sha256": selection["selection"]["descriptor"]["content_sha256"],
            }
        )
    )
    return freeze, permission.model_copy(update={"campaign_id": freeze.campaign_id})


@pytest.fixture
def inputs(context, frozen):
    feature, kw = context
    origin = datetime(2026, 7, 25, 23, 59, 59, 999999, tzinfo=UTC)
    raw_feature = feature.model_dump(mode="json")
    raw_feature["as_of"] = origin.isoformat().replace("+00:00", "Z")
    for i, row in enumerate(raw_feature["history"]):
        row["business_date"] = (origin.date() - timedelta(days=27 - i)).isoformat()
    source = frozen[0].sources[0]
    raw = dict(
        version="stockout-prepared-inputs-1.0.0",
        input_role="inference_public_facts_only",
        scope=dict(
            product_ids=[feature.product_id], stock_location_ids=[feature.stock_location_id]
        ),
        as_of=raw_feature["as_of"],
        points=[
            dict(
                feature=raw_feature,
                category_id=kw["category_id"],
                category_available_at=kw["category_available_at"]
                .isoformat()
                .replace("+00:00", "Z"),
                upstream=dict(
                    product_id=feature.product_id,
                    stock_location_id=feature.stock_location_id,
                    as_of=raw_feature["as_of"],
                    forecast_origin=origin.replace(microsecond=0)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    training_cutoff=origin.replace(microsecond=0)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    selection_cutoff=origin.replace(microsecond=0)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    source_available_at=None,
                    upstream_model_version="baseline-sha256-" + "0" * 64,
                    status="insufficient_data",
                    reason="synthetic_card_mechanics_only",
                    forecast_units_7d=None,
                    series=[],
                ),
            )
        ],
        lineage=dict(
            source_dataset_id=source.source_dataset_id,
            curated_dataset_id=source.curated_dataset_id,
            feature_set_id=source.feature_bundle_id,
            upstream_bundle_id=source.upstream_bundle_id,
            source_watermark=None,
            source_completeness_status="unavailable",
        ),
        source_parent_files_sha256="0" * 64,
        preparation_code_sha256="1" * 64,
        source_freshness_evidence="curated_does_not_supply_a_global_source_watermark",
        parent_replay="complete_public_features_and_upstream",
    )
    raw["inputs_id"] = "stockout-inputs-sha256-" + canonical_sha256(raw)
    return PreparedStockoutInputs.model_validate_json(canonical_bytes(raw))


def test_final_card_retains_failed_segments_frozen_coefficients_and_separate_worlds(
    frozen,
    recipes,
    selection,
    receipts,
    inputs,
    monkeypatch,
):
    from retailops_ai.stockout_training import pipeline

    monkeypatch.setattr(pipeline, "fit_model", lambda *a, **k: pytest.fail("no final refit"))
    freeze, permission = frozen
    result = collect(receipts, freeze=freeze, permission=permission)
    card = final_card(
        selection=selection,
        freeze=freeze,
        permission=permission,
        recipe=recipes[0],
        policy=recipes[1],
        quality=result["final_quality"],
        execution=result["execution_evidence"],
        inputs=inputs,
    )
    assert card["quality_status"] == "not_ready_independent_final_campaign"
    assert len(card["worlds"]) == len(card["blockers"]) == 6
    assert (
        card["base_coefficients"]["columns"][0]["coefficient"]
        == recipes[0].pipeline.base.estimator.weights[0]
    )
    assert card["model_refits"] == 0 and card["model_promoted"] is False


def test_rehashed_execution_cannot_conceal_a_changed_access_audit(frozen, receipts):
    freeze, permission = frozen
    result = collect(receipts, freeze=freeze, permission=permission)
    execution = result["execution_evidence"]
    execution["content"]["worlds"][0]["access_audits"]["native"][0]["approved_by"] = (
        "another-reviewer"
    )
    execution["evidence_id"] = "stockout-final-execution-sha256-" + canonical_sha256(
        execution["content"]
    )
    with pytest.raises(ValueError):
        verify_execution_evidence(
            execution, result["final_quality"], freeze=freeze, permission=permission
        )


def test_failed_final_quality_prevents_source_replay_and_any_qualification_output(
    frozen,
    recipes,
    receipts,
    inputs,
    tmp_path,
    monkeypatch,
):
    freeze, permission = frozen
    monkeypatch.setattr(
        qualification,
        "prepare_inputs",
        lambda *a, **k: pytest.fail("no failed-quality parent read"),
    )
    with pytest.raises(ValueError, match="quality_not_ready"):
        qualification.qualify_final(
            freeze=freeze,
            permission=permission,
            recipe=recipes[0],
            policy=recipes[1],
            receipt_roots=receipts,
            selection_path=tmp_path / "missing-selection",
            curated=tmp_path / "missing-curated",
            features=tmp_path / "missing-features",
            upstream=tmp_path / "missing-upstream",
            scope=inputs.scope,
            as_of=inputs.as_of,
            output=tmp_path / "not-created",
        )
    assert not (tmp_path / "not-created").exists()


def test_review_requires_authenticated_promoter_before_any_qualification_read(tmp_path):
    with pytest.raises(ValueError, match="promoter_required"):
        qualification.approve_stockout(
            tmp_path / "missing",
            actor=actor("reader"),
            request=None,
            reports=tmp_path / "missing-reports",
            output=tmp_path / "not-created",
        )
    assert not (tmp_path / "not-created").exists()
