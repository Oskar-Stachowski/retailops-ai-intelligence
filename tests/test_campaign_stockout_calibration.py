"""Complete later calibration, native equations, portable reload and chronology."""

from copy import deepcopy
from datetime import timedelta

import numpy as np
import pytest
from pydantic import ValidationError
from test_stockout_training import development as development  # noqa: F401
from test_stockout_training import parents as parents  # noqa: F401

from retailops_ai.evaluation_campaign import campaign_stockout_calibration as full
from retailops_ai.evaluation_campaign import campaign_stockout_training as training
from retailops_ai.evaluation_campaign.campaign_stockout_training_contract import (
    CampaignStockoutConditionalRiskPipeline,
)
from retailops_ai.stockout_selection import pipeline as native
from retailops_ai.stockout_selection.contract import ConditionalRiskPipeline
from retailops_ai.stockout_training import pipeline as base_native
from retailops_ai.stockout_training.contract import DEFAULT_POLICY
from retailops_ai.stockout_training.inputs import keys_sha256, labels_sha256


def fit_base(development, *, original=False):
    return (base_native if original else training).fit_model(
        development.rows["train"],
        development.outcomes["train"],
        family="logistic_regression",
        variant="without_upstream",
        fit_known_at=development.split_policy.train_until,
        policy=DEFAULT_POLICY,
    )


@pytest.mark.parametrize("C", [0.01, 0.1, 1.0, 10.0])
def test_same_native_conditional_equations_and_later_role_parity(development, C):
    base = fit_base(development)
    before = base.model_dump_json()
    rows, outcomes = development.rows["tune"], development.outcomes["tune"]
    arguments = dict(C=C, fit_known_at=development.split_policy.tune_until)
    expected = native.fit_conditional(
        fit_base(development, original=True), rows, outcomes, **arguments
    )
    model = full.fit_conditional(base, rows, outcomes, batch_rows=3, **arguments)
    assert base.model_dump_json() == before
    actual_state = model.calibrator.model_dump(mode="json", exclude={"version"})
    expected_state = expected.calibrator.model_dump(mode="json")
    for name in ("raw_score_slope", "intercept", "offset_weights"):
        assert actual_state.pop(name) == pytest.approx(
            expected_state.pop(name), rel=1e-10, abs=1e-12
        )
    assert actual_state == expected_state
    np.testing.assert_allclose(
        full.predict_conditional(model, development.rows["calibration"], batch_rows=4),
        native.predict_conditional(expected, development.rows["calibration"]),
        rtol=1e-10,
        atol=1e-12,
    )
    assert not model.quality_qualified and not model.stage_ready


def test_all_10001_calibration_rows_and_full_reload_inference_are_retained(development):
    base = fit_base(development)
    rows, outcomes = [], []
    for i in range(10001):
        row = deepcopy(development.rows["tune"][i % len(development.rows["tune"])])
        row["product_id"] = f"product-{i:05d}"
        rows.append(row)
        outcomes.append(development.outcomes["tune"][i % len(development.outcomes["tune"])])
    arguments = dict(C=0.1, fit_known_at=development.split_policy.tune_until)
    with pytest.raises(ValueError, match="matrix_row_limit"):
        native.fit_conditional(fit_base(development, original=True), rows, outcomes, **arguments)
    model = full.fit_conditional(base, rows, outcomes, batch_rows=113, **arguments)
    assert model.calibrator.calibration_rows == 10001
    assert model.calibrator.calibration_keys_sha256 == keys_sha256(rows)
    assert model.calibrator.calibration_labels_sha256 == labels_sha256(rows, outcomes)
    with pytest.raises(ValidationError):
        ConditionalRiskPipeline.model_validate_json(model.model_dump_json())
    restored = CampaignStockoutConditionalRiskPipeline.model_validate_json(model.model_dump_json())
    state = restored.calibrator
    future = (state.fit_known_at + timedelta(days=1)).isoformat()
    for row in rows:
        row["as_of"] = future
    rows[0]["category_id"] = "never-seen-in-calibration"
    before = restored.model_dump_json()
    values = full.predict_conditional(restored, rows, batch_rows=127)
    expected = []
    for start in range(0, len(rows), 251):
        chunk = rows[start : start + 251]
        score = (
            base_native.raw_scores(restored.base, chunk) * state.raw_score_slope
            + native.offsets(chunk, state.categories, state.stock_locations)
            @ np.asarray(state.offset_weights)
            + state.intercept
        )
        expected.extend(base_native.probabilities(score))
    assert len(values) == 10001 and np.isfinite(values).all()
    np.testing.assert_allclose(values, expected, rtol=1e-10, atol=1e-12)
    assert restored.model_dump_json() == before
    assert state.calibration_labels_sha256 != labels_sha256(rows[:-1], outcomes[:-1])


@pytest.mark.parametrize(
    "mutation", ["before_base", "after_cutoff", "small_class", "duplicate", "bool_label", "new_C"]
)
def test_invalid_calibration_never_fits_or_changes_base(development, mutation):
    base = fit_base(development)
    before = base.model_dump_json()
    rows = deepcopy(development.rows["tune"])
    outcomes = list(development.outcomes["tune"])
    C = 0.1
    if mutation == "before_base":
        rows[0]["as_of"] = (base.fit_known_at - timedelta(days=1)).isoformat()
    elif mutation == "after_cutoff":
        rows[-1]["as_of"] = development.split_policy.tune_until.isoformat()
    elif mutation == "small_class":
        outcomes = [1, *([0] * (len(rows) - 1))]
    elif mutation == "duplicate":
        rows[1] = deepcopy(rows[0])
    elif mutation == "bool_label":
        outcomes[0] = True
    else:
        C = 0.5
    with pytest.raises(ValueError):
        full.fit_conditional(
            base, rows, outcomes, C=C, fit_known_at=development.split_policy.tune_until
        )
    assert base.model_dump_json() == before


def test_calibrator_cannot_score_before_fit_known_at(development):
    model = full.fit_conditional(
        fit_base(development),
        development.rows["tune"],
        development.outcomes["tune"],
        C=0.1,
        fit_known_at=development.split_policy.tune_until,
    )
    with pytest.raises(ValueError, match="calibrator_not_known_at_origin"):
        full.predict_conditional(model, development.rows["tune"])
