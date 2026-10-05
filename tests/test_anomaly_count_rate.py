"""Sparse count calibration uses earlier known history and explicit event policies."""

import json
from datetime import date, timedelta

import pytest
from pydantic import ValidationError
from test_anomaly_detectors import numeric_rows, point
from test_anomaly_portfolio_model import saved_model  # noqa: F401 - shared genuine forest fixture

from retailops_ai.anomaly_detectors.codec import baseline_score
from retailops_ai.anomaly_detectors.protocol import series_key
from retailops_ai.anomaly_detectors.rows import (
    CountLag,
    CountRateRow,
    rate_count_residual,
    rate_stock_shortfall,
    validate_row,
)
from retailops_ai.anomaly_evaluation.verification import verify_scores
from retailops_ai.anomaly_portfolio.model import (
    CountRateDescriptor,
    EventCapacity,
    Model,
    count_rate_row,
    score,
)
from retailops_ai.source_snapshot.files import json_sha256


def test_known_prior_rate_prevents_zero_week_accumulation_in_sparse_counts():
    current = numeric_rows(1)[0].model_copy(
        update={
            "observed_units": 1,
            "expected_units": 0,
            "residual_units": 1,
            "standardized_residual": 1.0,
        }
    )
    support = tuple(CountLag(lag_days=i, observed_units=1, expected_units=0) for i in range(1, 7))
    result = CountRateRow(
        **current.model_dump(),
        event_type="return_completed",
        prior_known_units=28,
        prior_known_observations=28,
        recent_counts=support,
        short_count_residual=rate_count_residual(current, support, 3, 1.0),
        long_count_residual=rate_count_residual(current, support, 7, 1.0),
        inventory_shortfall=0,
    )
    assert result.short_count_residual == result.long_count_residual == 0
    assert baseline_score(result) == 1
    assert validate_row(result.model_dump()) == result
    assert rate_count_residual(current, (), 3, 1) is None


def test_history_rate_uses_fit_cutoff_and_never_current_or_future_outcome():
    day = date(2026, 8, 12)
    current = point(day, 18, "return_completed")
    future = point(day + timedelta(days=1), 100000, "return_completed")
    before = count_rate_row(current, {})
    after = count_rate_row(current, {(*series_key(future), future.business_date): future})
    assert before is not None and before == after
    assert before.prior_known_units == sum(
        p.observed_units for p in current.history if p.status == "qualified"
    )
    changed = point(day, 1000, "return_completed")
    extreme = count_rate_row(changed, {})
    assert extreme is not None and extreme.prior_known_units == before.prior_known_units
    assert extreme.prior_known_observations == before.prior_known_observations


def test_stock_constraint_applies_to_sales_and_requires_known_positive_rate():
    row = numeric_rows(1)[0].model_copy(update={"on_hand": 0, "observed_units": 0})
    assert rate_stock_shortfall(row, 3, "sale_completed") == 8
    assert rate_stock_shortfall(row, 3, "return_completed") == 0
    assert rate_stock_shortfall(row, 0, "sale_completed") == 0
    assert rate_stock_shortfall(row.model_copy(update={"on_hand": None}), 3, "sale_completed") == 0


def test_rate_row_rejects_resealed_false_arithmetic_and_label_fields():
    value = count_rate_row(point(date(2026, 8, 12), 18), {})
    assert value is not None
    for name, replacement in (
        ("inventory_shortfall", 8),
        ("label", True),
        ("prior_known_observations", 0),
        ("short_count_residual", 12),
    ):
        raw = value.model_dump(mode="json")
        raw[name] = replacement
        with pytest.raises(ValidationError):
            CountRateRow.model_validate_json(json.dumps(raw))
    with pytest.raises(ValidationError):
        EventCapacity(event_type="return_completed", alert_fraction=0.01, high_fraction=0.02)


def test_saved_json_count_arrays_replay_and_strict_numbers_remain_required(saved_model):  # noqa: F811 - injected shared fixture
    from datetime import UTC, datetime

    from test_anomaly_detectors import scope

    from retailops_ai.anomaly_detectors.protocol import Window

    fields = saved_model.descriptor.model_dump()
    fields.pop("version")
    descriptor = CountRateDescriptor(
        **fields,
        event_capacities=tuple(
            EventCapacity(event_type=e, alert_fraction=0.05, high_fraction=0.01)
            for e in ("sale_completed", "return_completed")
        ),
    )
    model = Model(
        detector_id="anomaly-detector-sha256-" + json_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
    )
    day = date(2026, 8, 12)
    p = point(day, 90)
    row = count_rate_row(p, {})
    assert row is not None
    saved = json.loads(row.model_dump_json())
    assert saved["recent_counts"] == []
    for family in ("seasonal_residual", "isolation_forest"):
        decisions = score(
            model,
            [p],
            (scope(),),
            Window(start=day, end=day),
            family,
            "batch",
            datetime(2026, 8, 16, tzinfo=UTC),
        )
        verify_scores(model, decisions, [saved])
        for name, invalid in (
            ("prior_known_units", "100"),
            ("prior_known_observations", True),
            ("oracle_label", True),
        ):
            with pytest.raises(ValidationError):
                verify_scores(model, decisions, [{**saved, name: invalid}])


def test_quality_protocol_accepts_equivalent_utc_json_and_rejects_changed_instant(saved_model):  # noqa: F811 - injected genuine forest fixture
    import hashlib
    from copy import deepcopy
    from datetime import datetime

    from test_anomaly_detectors import scope

    from retailops_ai.anomaly_evaluation.quality import QualityPolicy
    from retailops_ai.anomaly_evaluation.verification import verify_quality
    from retailops_ai.source_snapshot.files import canonical_json

    descriptor = saved_model.descriptor.model_dump(mode="json")
    frozen = {
        "detector_id": saved_model.detector_id,
        "family": "isolation_forest",
        "final_test_at_freeze": "not_scored",
        "model_sha256": hashlib.sha256(
            canonical_json(saved_model.model_dump(mode="json")) + b"\n"
        ).hexdigest(),
        "protocol": {
            **{
                k: descriptor[k]
                for k in ("train", "validation", "training_cutoff", "selection_cutoff")
            },
            "scopes": [scope().model_dump(mode="json")],
        },
        "quality_policy": QualityPolicy(
            minimum_precision=0.8,
            minimum_recall=0.65,
            maximum_false_alerts_per_1000=10,
            minimum_high_severity_precision=0.9,
            minimum_episode_recall=0.75,
            minimum_evaluable_coverage=0.55,
            minimum_clean_per_case=300,
        ).model_dump(mode="json"),
        "evaluation_policy": {},
        "data_inventory": [
            {
                "seed": seed,
                "scenario": scenario,
                "source_dataset_id": saved_model.descriptor.source_dataset_id,
            }
            for seed in (42, 137, 2026)
            for scenario in ("demand", "physical")
        ],
    }

    def check(value):
        selection = {
            "selection_id": "anomaly-selection-sha256-" + json_sha256(value),
            "descriptor": value,
        }
        verify_quality(
            {"evaluation_inputs": {}},
            {"selection": selection},
            lambda _: b"",
            saved_model.detector_id,
            "isolation_forest",
            saved_model,
        )

    assert frozen["protocol"]["training_cutoff"].endswith("Z")
    # The UTC representation binds successfully, then the missing independent
    # evidence is still rejected. No artifact or gate is accepted by this test.
    with pytest.raises(ValueError, match="anomaly_quality_artifact_inventory"):
        check(frozen)
    offset = deepcopy(frozen)
    offset["protocol"]["training_cutoff"] = saved_model.descriptor.training_cutoff.isoformat()
    with pytest.raises(ValueError, match="anomaly_quality_artifact_inventory"):
        check(offset)
    changed = deepcopy(frozen)
    cutoff = datetime.fromisoformat(changed["protocol"]["training_cutoff"]) + timedelta(seconds=1)
    changed["protocol"]["training_cutoff"] = cutoff.isoformat()
    with pytest.raises(ValueError, match="anomaly_quality_model_protocol_binding"):
        check(changed)
