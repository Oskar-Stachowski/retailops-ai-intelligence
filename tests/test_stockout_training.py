"""Train-only fit, signed portable models, protected outcomes and capacity arithmetic."""

from copy import deepcopy
from datetime import timedelta

import pytest
from pydantic import ValidationError
from test_stockout_split import POLICY, START, datasets

from retailops_ai.source_snapshot.files import canonical_json
from retailops_ai.stockout.split import build_split
from retailops_ai.stockout.upstream_contract import SeriesForecast, UpstreamPoint
from retailops_ai.stockout.upstream_dataset import build_comparison, digest
from retailops_ai.stockout_training.contract import (
    DEFAULT_POLICY,
    RiskPipeline,
    TrainingPolicy,
    columns,
)
from retailops_ai.stockout_training.development import build_development, select_on_tune
from retailops_ai.stockout_training.evaluation import capacity, metrics
from retailops_ai.stockout_training.inputs import assemble_development, category_at
from retailops_ai.stockout_training.pipeline import (
    fit_model,
    fit_preprocessing,
    fit_sigmoid,
    predict,
    raw_scores,
    transform,
)


@pytest.fixture
def parents():
    days = [*range(1, 23), *range(31, 53), *range(61, 83), 91, 92]
    features, labels = datasets(days)
    forecasts = []
    for i, (f, label) in enumerate(zip(features["points"], labels["points"], strict=True)):
        origin = START + timedelta(
            days=days[i], hours=23, minutes=59, seconds=59, microseconds=999999
        )
        f["as_of"] = origin.isoformat().replace("+00:00", "Z")
        f["values"]["available_qty"] = 10 + i if i % 2 else 100 + i
        label.update(
            as_of=f["as_of"],
            window_end_at=(origin + timedelta(days=7)).isoformat(),
            label_available_at=(origin + timedelta(days=7)).isoformat(),
            incident_stockout=i % 2,
            first_incident_at=(origin + timedelta(days=1)).isoformat() if i % 2 else None,
            first_incident_event_id="event" if i % 2 else None,
        )
        cutoff = origin.replace(microsecond=0)
        forecasts.append(
            UpstreamPoint(
                product_id="product",
                stock_location_id="stock",
                as_of=origin,
                forecast_origin=cutoff,
                training_cutoff=cutoff,
                selection_cutoff=cutoff,
                source_available_at=cutoff,
                upstream_model_version="baseline-sha256-" + "0" * 64,
                status="available",
                reason=None,
                forecast_units_7d=14.0,
                series=(
                    SeriesForecast(
                        selling_location_id="shop",
                        channel="store",
                        route_record_sha256="0" * 64,
                        history_context_sha256="0" * 64,
                        input_rows_sha256="0" * 64,
                        source_available_at=cutoff,
                        daily_units=(2.0,) * 7,
                        reason=None,
                    ),
                ),
            ).model_dump(mode="json")
        )
    upstream = dict(
        upstream_id="upstream",
        descriptor=dict(feature_dataset_id="features"),
        points=forecasts,
        report=dict(upstream_forecast_ready=True),
    )
    features["descriptor"]["curated_dataset_id"] = "curated"
    comparison = build_comparison(features, upstream)
    split = build_split(features, labels, POLICY)
    catalog = [
        dict(
            id="product",
            category_id="category",
            curated_available_at=START,
            source_record_sha256="0" * 64,
        )
    ]
    return features, upstream, comparison, labels, split, catalog


@pytest.fixture
def development(parents):
    return assemble_development(*parents)


def test_model_variants_are_allowlisted_and_censor_control_removes_only_two_values():
    assert set(columns("with_upstream")) - set(columns("without_upstream")) == {
        "forecast_units_7d",
        "forecast_days_of_supply",
        "forecast_unavailable",
    }
    assert set(columns("without_upstream")) - set(columns("raw_sales_only")) == {
        "in_stock_sales_mean",
        "days_of_supply_in_stock",
    }
    for variant in ("with_upstream", "without_upstream", "raw_sales_only"):
        assert not set(columns(variant)) & {
            "incident_stockout",
            "product_id",
            "as_of",
            "latent_demand",
        }


def test_empty_column_future_missing_and_unseen_category_use_frozen_training_state(development):
    state = fit_preprocessing(development.rows["train"], "without_upstream", scaled=True)
    i = state.numeric_columns.index("next_expected_delivery_hours")
    assert state.medians[i] == 0.0
    future = deepcopy(development.rows["tune"][:1])
    future[0]["values"]["available_qty"] = None
    future[0]["category_id"] = "unseen"
    transformed = transform(future, state)
    n = len(state.numeric_columns)
    assert transformed[0, n] == 1.0
    assert transformed[0, -1] == 0.0
    before = state.model_dump()
    future[0]["values"]["next_expected_delivery_hours"] = 1e10
    transform(future, state)
    assert state.model_dump() == before


def test_vocabulary_limit_and_nonfinite_values_fail_closed(development):
    rows = [deepcopy(development.rows["train"][0]) for _ in range(33)]
    for i, r in enumerate(rows):
        r["stock_location_id"] = str(i)
    with pytest.raises(ValueError, match="vocabulary_limit"):
        fit_preprocessing(rows, "with_upstream", scaled=False)
    rows[0]["values"]["available_qty"] = float("inf")
    with pytest.raises(ValueError, match="numeric_value_invalid"):
        fit_preprocessing(rows, "with_upstream", scaled=False)


@pytest.mark.parametrize("family", ["logistic_regression", "hist_gradient_boosting"])
def test_portable_json_reproduces_probabilities_and_respects_training_cutoff(development, family):
    model = fit_model(
        development.rows["train"],
        development.outcomes["train"],
        family=family,
        variant="with_upstream",
        fit_known_at=POLICY.train_until,
        policy=DEFAULT_POLICY,
    )
    before = predict(model, development.rows["tune"], calibrated=False)
    restored = RiskPipeline.model_validate_json(canonical_json(model.model_dump(mode="json")))
    assert predict(restored, development.rows["tune"], calibrated=False) == before
    with pytest.raises(ValueError, match="not_known_at_origin"):
        predict(restored, development.rows["train"], calibrated=False)
    with pytest.raises(ValueError, match="calibrator_not_fitted"):
        predict(restored, development.rows["tune"])


def test_hgb_negative_log_odds_are_not_clipped_to_zero(development):
    rows, outcomes = [], []
    for i in range(80):
        r = deepcopy(development.rows["train"][0])
        r["values"]["available_qty"] = 1 if i % 2 else 100
        rows.append(r)
        outcomes.append(i % 2)
    model = fit_model(
        rows,
        outcomes,
        family="hist_gradient_boosting",
        variant="without_upstream",
        fit_known_at=POLICY.train_until,
        policy=DEFAULT_POLICY,
    )
    future = deepcopy(rows[:2])
    for r in future:
        r["as_of"] = POLICY.train_until.isoformat()
    scores = raw_scores(model, future)
    assert scores[0] < 0 < scores[1]
    p = predict(model, future, calibrated=False)
    assert p[0] < 0.1 and p[1] > 0.9


def test_sigmoid_has_separate_cutoff_and_small_samples_are_not_fitted(development):
    model = fit_model(
        development.rows["train"],
        development.outcomes["train"],
        family="logistic_regression",
        variant="without_upstream",
        fit_known_at=POLICY.train_until,
        policy=DEFAULT_POLICY,
    )
    small = fit_sigmoid(
        model,
        development.rows["calibration"][:4],
        development.outcomes["calibration"][:4],
        fit_known_at=POLICY.calibration_until,
        policy=DEFAULT_POLICY,
    )
    assert small is None
    sigmoid = fit_sigmoid(
        model,
        development.rows["calibration"],
        development.outcomes["calibration"],
        fit_known_at=POLICY.calibration_until,
        policy=DEFAULT_POLICY,
    )
    model = RiskPipeline.model_validate({**model.model_dump(), "sigmoid": sigmoid})
    with pytest.raises(ValueError, match="calibrator_not_known_at_origin"):
        predict(model, development.rows["calibration"])
    future = deepcopy(development.rows["calibration"][:1])
    future[0]["as_of"] = POLICY.calibration_until.isoformat()
    assert 0 <= predict(model, future)[0] <= 1


def test_final_outcomes_do_not_change_development_matrices_or_comparison(parents):
    first = assemble_development(*parents)
    changed = deepcopy(parents)
    for p in changed[3]["points"][-2:]:
        p.update(
            incident_stockout=1,
            first_incident_at=(START + timedelta(days=93)).isoformat(),
            first_incident_event_id="changed",
        )
    second = assemble_development(*changed)
    assert first == second
    assert set(first.outcomes) == {"train", "tune", "calibration"}
    assert first.coverage["final_test"] == dict(
        eligible_membership_only=2, outcomes_evaluated=False
    )


def test_resealed_test_to_train_and_modified_comparison_are_rejected(parents):
    changed = deepcopy(parents)
    changed[4]["membership"][-1]["role"] = "train"
    changed[4]["descriptor"]["membership_sha256"] = digest(changed[4]["membership"])
    with pytest.raises(ValueError, match="split_replay_mismatch"):
        assemble_development(*changed)
    changed = deepcopy(parents)
    changed[2]["rows"][0]["base"]["available_qty"] = 999
    changed[2]["descriptor"]["rows_sha256"] = digest(changed[2]["rows"])
    with pytest.raises(ValueError, match="comparison_replay_mismatch"):
        assemble_development(*changed)


def test_category_is_PIT_and_future_revision_cannot_relabel_origin(parents):
    catalog = deepcopy(parents[-1])
    catalog.append(
        {**catalog[0], "category_id": "future", "curated_available_at": START + timedelta(days=200)}
    )
    assert category_at(catalog, "product", START)[0] == "category"
    catalog[-1]["curated_available_at"] = START
    with pytest.raises(ValueError, match="ambiguous_PIT"):
        category_at(catalog, "product", START)


def test_metric_arithmetic_and_one_class_not_evaluable(development):
    rows = development.rows["tune"][:2]
    result = metrics(rows, [0, 1], [0.1, 0.9], DEFAULT_POLICY, train_prevalence=0.5)
    assert result["average_precision"] == result["roc_auc"] == 1.0
    assert result["brier"] == 0.01 and result["train_prevalence_constant_brier"] == 0.25
    assert sum(b["count"] for b in result["reliability"]) == 2
    invalid = metrics(rows, [0, 0], [0.0, 1.0], DEFAULT_POLICY, train_prevalence=0.5)
    assert invalid["status"] == "not_evaluable" and invalid["average_precision"] is None
    assert invalid["roc_auc"] is None and invalid["capacity"]["recall"] is None
    assert invalid["brier"] == 0.5


def test_capacity_is_per_origin_with_deterministic_ties(development):
    rows = [deepcopy(development.rows["tune"][0]) for _ in range(10)]
    for i, r in enumerate(rows):
        r["product_id"] = f"product-{i}"
        r["as_of"] = str(i // 5)
    result = capacity(rows, [1] + [0] * 9, [0.5] * 10, DEFAULT_POLICY)
    assert result["selected"] == 2 and result["true_positive"] == 1
    assert result["recall"] == 1 and result["precision"] == 0.5
    assert result["cost"] == 1.0
    assert [r["capacity"] for r in result["per_origin"]] == [1, 1]


def test_selection_uses_tune_only_and_excludes_censor_control():
    result = {
        "logistic_regression:without_upstream": dict(
            tune=dict(all=dict(average_precision=0.8, brier=0.2))
        ),
        "hist_gradient_boosting:with_upstream": dict(
            tune=dict(all=dict(average_precision=0.7, brier=0.1)),
            calibration_sigmoid_in_sample=dict(all=dict(average_precision=1.0, brier=0.0)),
        ),
        "logistic_regression:raw_sales_only": dict(
            tune=dict(all=dict(average_precision=1.0, brier=0.0))
        ),
    }
    assert select_on_tune(result) == "logistic_regression:without_upstream"


def test_comparison_has_same_grain_and_remains_development_only(development):
    result = build_development(development)
    assert len(result["pipelines"]) == 6
    assert len({r["tune_keys_sha256"] for r in result["results"].values()}) == 1
    assert result["report"]["sigmoid_fitted_models"] == 6
    assert result["report"]["final_test_outcomes_evaluated"] is False
    assert result["report"]["model_ready"] is False
    assert result["report"]["thresholds_fitted"] is False
    assert result["report"]["calibration_generalization"] == "not_evaluated_fit_diagnostics_only"
    altered = deepcopy(development)
    altered.rows["train"][0]["as_of"] = POLICY.test_until.isoformat()
    with pytest.raises(ValueError, match="role_window_or_labels_invalid"):
        build_development(altered)


def test_calibration_targets_cannot_change_selection_or_train_preprocessing(development):
    first = build_development(development)
    changed = deepcopy(development)
    changed.outcomes["calibration"][:] = [1 - y for y in changed.outcomes["calibration"]]
    second = build_development(changed)
    assert first["report"]["selected_on_tune"] == second["report"]["selected_on_tune"]
    for name, pipeline in first["pipelines"].items():
        assert pipeline["preprocessing"] == second["pipelines"][name]["preprocessing"]
        assert pipeline["estimator"] == second["pipelines"][name]["estimator"]
    assert first["pipelines"] != second["pipelines"]


@pytest.mark.parametrize(
    "mutation",
    [dict(capacity_fraction=0.9), dict(final_test="open"), dict(hgb_early_stopping=True)],
)
def test_recipe_cannot_silently_change(mutation):
    with pytest.raises(ValidationError):
        TrainingPolicy.model_validate(mutation)


def test_pipeline_rejects_changed_feature_allowlist(development):
    model = fit_model(
        development.rows["train"],
        development.outcomes["train"],
        family="logistic_regression",
        variant="without_upstream",
        fit_known_at=POLICY.train_until,
        policy=DEFAULT_POLICY,
    )
    document = model.model_dump()
    document["variant"] = "with_upstream"
    with pytest.raises(ValidationError, match="feature_allowlist_mismatch"):
        RiskPipeline.model_validate(document)
