"""Time boundary purge, delayed availability and protected final-test membership."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from retailops_ai.stockout.contract import LabelPoint
from retailops_ai.stockout.feature_contract import DailyHistory, FeaturePoint
from retailops_ai.stockout.split import SplitPolicy, build_split, development_labels

START = datetime(2026, 1, 1, tzinfo=UTC)
POLICY = SplitPolicy(
    start_at=START,
    train_until=START + timedelta(days=30),
    tune_until=START + timedelta(days=60),
    calibration_until=START + timedelta(days=90),
    test_until=START + timedelta(days=120),
    evaluated_at=START + timedelta(days=127),
)


def datasets(days, *, positive=False, delay=timedelta(0)):
    features, labels = [], []
    for n in days:
        origin = START + timedelta(days=n)
        feature = FeaturePoint.model_validate(
            dict(
                product_id="product",
                stock_location_id="stock",
                as_of=origin,
                status="eligible",
                reason=None,
                feature_available_at=origin,
                history=tuple(
                    DailyHistory(
                        business_date=origin.date() - timedelta(days=d),
                        observed_units=2,
                        inventory_day_verified=True,
                        in_stock_all_day=True,
                        stockout_onsets=0,
                    )
                    for d in range(27, -1, -1)
                ),
                lineage=(
                    dict(
                        table="inventory_daily_snapshots",
                        rows=1,
                        source_records_sha256="0" * 64,
                        max_available_at=origin,
                    ),
                ),
                values=dict(
                    available_qty=100,
                    snapshot_age_hours=0.0,
                    history_known_days=28,
                    history_missing_days=0,
                    observed_sales_mean=2.0,
                    observed_sales_std=0.0,
                    in_stock_sales_mean=2.0,
                    history_in_stock_days=28,
                    history_constrained_days=0,
                    history_inventory_unknown_days=0,
                    historical_stockout_onsets=0,
                    days_of_supply_observed=50.0,
                    days_of_supply_in_stock=50.0,
                    open_order_quantity=0,
                    due_within_7d_quantity=0,
                    overdue_order_quantity=0,
                    next_expected_delivery_hours=None,
                    quoted_lead_time_days=None,
                ),
            )
        )
        label = LabelPoint(
            product_id="product",
            stock_location_id="stock",
            as_of=origin,
            window_end_at=origin + timedelta(days=7),
            evaluated_at=POLICY.evaluated_at,
            status="evaluable",
            reason=None,
            incident_stockout=int(positive),
            label_available_at=origin + timedelta(days=7) + delay,
            first_incident_at=origin + timedelta(days=1) if positive else None,
            first_incident_event_id="event" if positive else None,
        )
        features.append(feature.model_dump(mode="json"))
        labels.append(label.model_dump(mode="json"))
    parent = dict(source_dataset_id="source", qualification_id="qualification")
    return dict(
        feature_dataset_id="features",
        descriptor=dict(role="stockout_features", **parent),
        points=features,
        report=dict(upstream_forecast_ready=False),
    ), dict(
        label_dataset_id="labels", descriptor=dict(role="stockout_labels", **parent), points=labels
    )


@pytest.mark.parametrize(
    "days,role", [([22, 23], "train"), ([52, 53], "tune"), ([82, 83], "calibration")]
)
def test_seven_day_end_exactly_at_next_role_is_purged(days, role):
    features, labels = datasets(days)
    result = build_split(features, labels, POLICY)
    assert [m["eligible"] for m in result["membership"]] == [True, False]
    assert result["membership"][1]["reason"] == "purged_boundary_or_delayed_label"
    assert result["report"]["eligible_by_role"][role] == 1


def test_late_label_is_purged_even_when_outcome_window_finished_earlier():
    features, labels = datasets([20], delay=timedelta(days=3))
    result = build_split(features, labels, POLICY)
    assert result["membership"][0]["window_end_at"] < POLICY.train_until.isoformat().replace(
        "+00:00", "Z"
    )
    assert result["membership"][0]["reason"] == "purged_boundary_or_delayed_label"


def test_role_ranges_are_half_open_and_outside_rows_are_not_training_rows():
    features, labels = datasets([-1, 0, 30, 60, 90, 120])
    result = build_split(features, labels, POLICY)
    assert [m["role"] for m in result["membership"]] == [
        None,
        "train",
        "tune",
        "calibration",
        "test",
        None,
    ]
    assert result["report"]["reasons"] == dict(outside_campaign=2)
    assert len(development_labels(result, features, labels, role="train")) == 1


def test_test_outcomes_do_not_affect_membership_or_development_counts():
    features, labels = datasets([1, 31, 61, 91])
    before = build_split(features, labels, POLICY)
    changed = deepcopy(labels)
    changed["points"][-1].update(
        incident_stockout=1,
        first_incident_at=(START + timedelta(days=92)).isoformat(),
        first_incident_event_id="test-event",
    )
    assert build_split(features, changed, POLICY) == before
    assert "test" not in before["report"]["development_classes"]
    assert before["report"]["final_test_outcomes_evaluated"] is False
    with pytest.raises(ValueError, match="separate_approved_campaign"):
        development_labels(before, features, labels, role="test")


def test_resealed_test_to_train_membership_cannot_be_used_for_development():
    features, labels = datasets([1, 91])
    split = build_split(features, labels, POLICY)
    split["membership"][-1]["role"] = "train"
    with pytest.raises(ValueError, match="split_replay_mismatch"):
        development_labels(split, features, labels, role="train")


def test_missing_feature_and_unknown_label_are_not_negative_training_examples():
    features, labels = datasets([1, 2])
    features["points"].pop(0)
    labels["points"][1].update(
        status="not_evaluable",
        reason="inventory_unknown",
        incident_stockout=None,
        label_available_at=None,
    )
    split = build_split(features, labels, POLICY)
    assert [m["reason"] for m in split["membership"]] == [
        "feature_missing",
        "label_inventory_unknown",
    ]
    assert development_labels(split, features, labels, role="train") == []


@pytest.mark.parametrize("which", ["feature", "label"])
def test_duplicate_physical_origin_is_rejected(which):
    features, labels = datasets([1])
    doc = features if which == "feature" else labels
    doc["points"] += deepcopy(doc["points"])
    with pytest.raises(ValueError, match="duplicate_physical_origin"):
        build_split(features, labels, POLICY)


def test_parent_mismatch_and_non_chronological_policy_are_rejected():
    features, labels = datasets([1])
    labels["descriptor"]["qualification_id"] = "foreign"
    with pytest.raises(ValueError, match="parent_mismatch"):
        build_split(features, labels, POLICY)
    with pytest.raises(ValidationError, match="ordered_utc_intervals"):
        SplitPolicy.model_validate({**POLICY.model_dump(), "tune_until": POLICY.train_until})


def test_membership_ready_requires_both_classes_in_each_development_role():
    features, labels = datasets([1, 2, 31, 32, 61, 62, 91, 92])
    assert build_split(features, labels, POLICY)["report"]["temporal_membership_ready"] is False
    for i in [1, 3, 5]:
        label = labels["points"][i]
        label.update(
            incident_stockout=1,
            first_incident_at=(
                LabelPoint.model_validate(label).as_of + timedelta(days=1)
            ).isoformat(),
            first_incident_event_id="event",
        )
    report = build_split(features, labels, POLICY)["report"]
    assert report["temporal_membership_ready"] is True
    assert report["upstream_forecast_ready"] is False and report["model_ready"] is False
