"""Runtime uses the exact conditional calibrator and retains inventory status rules."""

from datetime import timedelta

import pytest
from test_stockout_runtime import altered
from test_stockout_runtime import context as context
from test_stockout_runtime import records as records

from retailops_ai.source_snapshot.files import canonical_json
from retailops_ai.stockout.upstream_dataset import digest
from retailops_ai.stockout_policy.contract import PolicySpec
from retailops_ai.stockout_runtime.contracts import ScoringPolicy, ScoringRecipe
from retailops_ai.stockout_runtime.scoring import score_point
from retailops_ai.stockout_selection.contract import (
    ConditionalRiskPipeline,
    ConditionalSigmoid,
    SelectionModelPin,
)
from retailops_ai.stockout_selection.pipeline import predict_conditional


@pytest.fixture
def conditional(context):
    feature, kw = context
    original = kw["recipe"].pipeline
    calibrator = ConditionalSigmoid(
        fit_known_at=original.sigmoid.fit_known_at,
        calibration_rows=20,
        calibration_keys_sha256="0" * 64,
        calibration_labels_sha256="1" * 64,
        C=1.0,
        raw_score_slope=0.9,
        intercept=0.1,
        categories=("category",),
        stock_locations=("stock",),
        offset_weights=(0.2, 0.3, 0.4),
    )
    model = ConditionalRiskPipeline(
        base=original.model_copy(update={"sigmoid": None}), calibrator=calibrator
    )
    pin = SelectionModelPin(
        selection_id="stockout-selection-sha256-" + "0" * 64,
        model_id="risk-model-sha256-" + digest(model.model_dump(mode="json")),
        calibrator_sha256=digest(calibrator.model_dump(mode="json")),
        selection_known_at=kw["recipe"].pin.selection_known_at,
    )
    recipe = ScoringRecipe(version="stockout-portable-scoring-2.0.0", pin=pin, pipeline=model)
    raw = kw["policy"].spec.model_dump(mode="json")
    raw.update(version="stockout-threshold-proposal-2.0.0", evaluation_role="calibration")
    spec = PolicySpec.model_validate_json(canonical_json(raw))
    body = dict(
        version="stockout-scoring-policy-2.0.0",
        pin=pin.model_dump(mode="json"),
        proposal_id="stockout-policy-proposal-sha256-" + "0" * 64,
        spec=spec.model_dump(mode="json"),
        operational_approval="requires_separate_lifecycle_approval",
    )
    policy = ScoringPolicy.model_validate_json(
        canonical_json(dict(policy_id="stockout-scoring-policy-sha256-" + digest(body), **body))
    )
    kw.update(
        recipe=recipe,
        policy=policy,
        release=kw["release"].model_copy(
            update=dict(
                recipe_content_sha256=digest(recipe.model_dump(mode="json")),
                policy_content_sha256=digest(policy.model_dump(mode="json")),
            )
        ),
    )
    return feature, kw


def test_runtime_preserves_conditional_offsets_and_monotone_calibrator(conditional):
    f, kw = conditional
    item = score_point(f, **kw)
    row = dict(
        product_id=f.product_id,
        stock_location_id=f.stock_location_id,
        as_of=f.as_of.isoformat(),
        category_id=kw["category_id"],
        values=f.values.model_dump(),
    )
    assert item.probability == float(predict_conditional(kw["recipe"].pipeline, [row])[0])
    assert item == score_point(f, **kw)
    assert item.quality_status == "mechanics_only" and item.status == "scored"
    assert item.calibrator_version.endswith(kw["recipe"].pin.calibrator_sha256)


@pytest.mark.parametrize("state", ["zero", "unknown", "stale"])
def test_conditional_model_does_not_score_zero_unknown_or_stale_inventory(conditional, state):
    f, kw = conditional
    if state == "zero":
        f = altered(f, available_qty=0).model_copy(update={"status": "already_stockout"})
    elif state == "unknown":
        f = altered(f, available_qty=None).model_copy(
            update={"status": "insufficient_data", "reason": "inventory_unknown"}
        )
    else:
        f = altered(f, snapshot_age_hours=25.0)
    item = score_point(f, **kw)
    assert item.probability is None and item.risk_band is None


def test_later_selection_is_required_even_for_current_stockout(conditional):
    f, kw = conditional
    pin = kw["recipe"].pin.model_copy(update={"selection_known_at": f.as_of + timedelta(days=1)})
    kw["recipe"] = kw["recipe"].model_copy(update={"pin": pin})
    f = altered(f, available_qty=0).model_copy(update={"status": "already_stockout"})
    with pytest.raises(ValueError):
        score_point(f, **kw)
