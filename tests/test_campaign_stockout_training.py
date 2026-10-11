"""Real LR/HGB fits and portable reload; exposed synthetic rows, no Project claim."""

from copy import deepcopy
from datetime import timedelta

import numpy as np
import pytest
from pydantic import ValidationError
from test_stockout_training import development as development  # noqa: F401
from test_stockout_training import parents as parents  # noqa: F401

from retailops_ai.evaluation_campaign import campaign_stockout_training as full
from retailops_ai.evaluation_campaign.campaign_stockout_training_contract import (
    CampaignStockoutPreprocessing,
    CampaignStockoutRiskPipeline,
)
from retailops_ai.stockout_training import pipeline as native
from retailops_ai.stockout_training.contract import DEFAULT_POLICY, RiskPipeline
from retailops_ai.stockout_training.inputs import keys_sha256, labels_sha256


@pytest.mark.parametrize("family", ["logistic_regression", "hist_gradient_boosting"])
@pytest.mark.parametrize("variant", ["without_upstream", "with_upstream", "raw_sales_only"])
def test_same_native_equations_and_portable_state_on_shared_population(
    development, family, variant
):
    rows, outcomes = development.rows["train"], development.outcomes["train"]
    arguments = dict(
        family=family,
        variant=variant,
        fit_known_at=development.split_policy.train_until,
        policy=DEFAULT_POLICY,
    )
    previous = native.fit_model(rows, outcomes, **arguments)
    complete = full.fit_model(rows, outcomes, batch_rows=3, **arguments)
    state = complete.model_dump(
        mode="json", exclude={"version", "quality_qualified", "stage_ready"}
    )
    state["preprocessing"].pop("version")
    assert state == previous.model_dump(mode="json")
    assert full.population_digest(rows) == keys_sha256(rows)
    assert full.population_digest(rows, outcomes) == labels_sha256(rows, outcomes)
    later = development.rows["tune"]
    assert full.predict(complete, later, batch_rows=4) == pytest.approx(
        native.predict(previous, later, calibrated=False), rel=1e-12, abs=1e-12
    )
    assert not complete.quality_qualified and not complete.stage_ready


@pytest.mark.parametrize("family", ["logistic_regression", "hist_gradient_boosting"])
def test_every_row_above_native_cap_is_fitted_hashed_and_scored_after_reload(development, family):
    rows, outcomes = [], []
    for i in range(10001):
        row = deepcopy(development.rows["train"][i % len(development.rows["train"])])
        row["product_id"] = f"product-{i:05d}"
        row["values"]["available_qty"] = float(i % 97 + 7 * (i % 2))
        rows.append(row)
        outcomes.append(i % 2)
    arguments = dict(
        family=family,
        variant="without_upstream",
        fit_known_at=development.split_policy.train_until,
        policy=DEFAULT_POLICY,
    )
    with pytest.raises(ValueError, match="matrix_row_limit"):
        native.fit_model(rows, outcomes, **arguments)
    model = full.fit_model(rows, outcomes, batch_rows=113, **arguments)
    assert model.preprocessing.train_rows == 10001
    assert model.preprocessing.train_keys_sha256 == keys_sha256(rows)
    assert model.train_labels_sha256 == labels_sha256(rows, outcomes)
    assert model.train_labels_sha256 != labels_sha256(rows[:-1], outcomes[:-1])
    with pytest.raises(ValidationError):
        RiskPipeline.model_validate_json(model.model_dump_json())
    restored = CampaignStockoutRiskPipeline.model_validate_json(model.model_dump_json())
    cutoff = (model.fit_known_at + timedelta(days=1)).isoformat()
    for row in rows:
        row["as_of"] = cutoff
    expected = np.asarray(
        [
            value
            for start in range(0, len(rows), 251)
            for value in native.predict(restored, rows[start : start + 251], calibrated=False)
        ]
    )
    observed = full.predict(restored, rows, batch_rows=113)
    assert len(observed) == 10001 and np.isfinite(observed).all()
    np.testing.assert_allclose(observed, expected, rtol=1e-12, atol=1e-12)


def test_later_missing_values_and_unseen_categories_never_refit_preprocessing(development):
    state = full.fit_preprocessing(development.rows["train"], "with_upstream", scaled=True)
    before = state.model_dump_json()
    future = deepcopy(development.rows["tune"])
    future[0]["category_id"] = "unseen-future"
    future[0]["values"]["available_qty"] = None
    future[0]["values"]["next_expected_delivery_hours"] = 1e12
    observed = full.transform(future, state, batch_rows=3)
    expected = native.transform(future, state)
    np.testing.assert_array_equal(observed, expected)
    assert state.model_dump_json() == before
    assert state.medians[state.numeric_columns.index("next_expected_delivery_hours")] == 0.0


@pytest.mark.parametrize(
    "mutation", ["duplicate", "unordered", "naive_clock", "late", "inf", "bool_label"]
)
def test_invalid_or_future_training_population_is_refused(development, mutation):
    rows = deepcopy(development.rows["train"])
    outcomes = list(development.outcomes["train"])
    if mutation == "duplicate":
        rows[1] = deepcopy(rows[0])
    elif mutation == "unordered":
        rows.reverse()
    elif mutation == "naive_clock":
        rows[0]["as_of"] = "2026-01-02T00:00:00"
    elif mutation == "late":
        rows[-1]["as_of"] = development.split_policy.train_until.isoformat()
    elif mutation == "inf":
        rows[-1]["values"]["available_qty"] = float("inf")
    else:
        outcomes[-1] = True
    with pytest.raises(ValueError):
        full.fit_model(
            rows,
            outcomes,
            family="logistic_regression",
            variant="without_upstream",
            fit_known_at=development.split_policy.train_until,
            policy=DEFAULT_POLICY,
        )


def test_new_wire_keeps_old_population_cap_and_native_batch_cap(development):
    rows = development.rows["train"]
    with pytest.raises(ValueError, match="batch_limit"):
        full.fit_preprocessing(rows, "without_upstream", scaled=False, batch_rows=10001)
    state = full.fit_preprocessing(rows, "without_upstream", scaled=False)
    raw = state.model_dump(mode="json")
    raw["train_rows"] = 1_000_001
    with pytest.raises(ValidationError):
        CampaignStockoutPreprocessing.model_validate(raw)
    with pytest.raises(ValueError, match="outcome_count"):
        full.population_digest(rows, [0])


def test_equivalent_utc_clock_representations_do_not_duplicate_a_physical_key(development):
    first = deepcopy(development.rows["train"][0])
    first["as_of"] = first["as_of"].replace("Z", "+00:00")
    second = deepcopy(first)
    second["as_of"] = second["as_of"].replace("+00:00", "Z")
    assert first["as_of"] < second["as_of"]
    with pytest.raises(ValueError, match="unique_keys"):
        full.fit_preprocessing([first, second], "without_upstream", scaled=True)
