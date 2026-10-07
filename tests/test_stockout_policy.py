"""Physical capacity, honest undefined metrics and development-only policy identity."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_stockout_qualification import development as development
from test_stockout_qualification import parents as parents
from test_stockout_qualification import sample as sample

from retailops_ai.stockout_policy import bundle
from retailops_ai.stockout_policy.assessment import assess
from retailops_ai.stockout_policy.contract import (
    AttentionCosts,
    OperatorCapacity,
    PolicyRow,
    PolicySpec,
    RiskThresholds,
    risk_band,
)
from retailops_ai.stockout_temporal_storage.store import PartitionInputs


@pytest.fixture
def spec():
    return PolicySpec(
        thresholds=RiskThresholds(medium_from=0.25, high_from=0.5, critical_from=0.9),
        capacity=OperatorCapacity(mode="top_n", top_n=1),
        costs=AttentionCosts(false_attention=1.0, missed_incident=5.0),
    )


def row(product, p, y, *, stock="stock", category="category", origin="2026-06-06T00:00:00Z"):
    return PolicyRow(
        product_id=product,
        stock_location_id=stock,
        as_of=origin,
        category_id=category,
        probability=p,
        incident_stockout=y,
        historical_inventory_constraint=True,
    )


@pytest.mark.parametrize(
    ("p", "expected"),
    [
        (0.0, "low"),
        (0.249999, "low"),
        (0.25, "medium"),
        (0.5, "high"),
        (0.9, "critical"),
        (1.0, "critical"),
    ],
)
def test_thresholds_have_explicit_inclusive_lower_bound(spec, p, expected):
    assert risk_band(p, spec.thresholds) == expected


@pytest.mark.parametrize("p", [float("nan"), float("inf"), -0.1, 1.01, True])
def test_probability_rejects_invalid_values(spec, p):
    with pytest.raises(ValueError):
        risk_band(p, spec.thresholds)


def test_literal_labels_cannot_accept_boolean():
    with pytest.raises(ValueError):
        row("p", 0.2, True)


@pytest.mark.parametrize(
    "values",
    [
        dict(mode="top_n"),
        dict(mode="top_n", top_n=1, fraction=0.2),
        dict(mode="top_fraction", top_n=2),
        dict(mode="top_fraction", fraction=0.0),
    ],
)
def test_capacity_is_explicit_and_has_one_mode(values):
    with pytest.raises(ValueError):
        OperatorCapacity(**values)


def test_confusion_and_cost_match_operator_workload(spec):
    result = assess([row("a", 0.9, 0), row("b", 0.8, 1), row("c", 0.1, 0)], spec)
    c = result["capacity"]
    assert (c["true_positive"], c["false_positive"], c["true_negative"], c["false_negative"]) == (
        0,
        1,
        1,
        1,
    )
    assert (c["precision"], c["recall"], c["cost"]) == (0.0, 0.0, 6.0)
    assert not result["thresholds_approved"] and not result["serving_eligible"]


def test_same_global_queue_is_used_for_all_categories(spec):
    points = [row("a", 0.9, 1, category="a"), row("b", 0.8, 1, category="b")]
    result = assess(points, spec)
    assert result["capacity"]["selected"] == 1
    segments = result["segments_at_same_global_capacity"]
    assert segments["category:a"]["selected"] == 1
    assert segments["category:b"]["selected"] == 0
    assert segments["category:b"]["precision"] is None
    assert segments["category:b"]["status"] == "not_evaluable"


def test_fraction_rounding_and_capacity_reset_per_origin(spec):
    capacity = OperatorCapacity(mode="top_fraction", fraction=0.2)
    result = assess(
        [row(str(i), 0.8, i % 2) for i in range(6)]
        + [row("a", 0.9, 1, origin="2026-06-07T00:00:00Z")],
        spec.model_copy(update={"capacity": capacity}),
    )
    assert [o["selected"] for o in result["capacity"]["origins"]] == [2, 1]


def test_physical_stock_keys_are_distinct_but_duplicates_are_rejected(spec):
    assert (
        assess([row("a", 0.8, 1, stock="one"), row("a", 0.7, 0, stock="two")], spec)["capacity"][
            "rows"
        ]
        == 2
    )
    with pytest.raises(ValueError, match="duplicate_physical"):
        assess([row("a", 0.8, 1), row("a", 0.7, 0)], spec)


def test_ties_are_lexical_and_independent_of_input_order(spec):
    a, b = row("a", 0.8, 1), row("b", 0.8, 0)
    assert assess([b, a], spec)["capacity"] == assess([a, b], spec)["capacity"]
    assert assess([b, a], spec)["capacity"]["true_positive"] == 1


def test_no_positive_class_does_not_produce_perfect_recall(spec):
    result = assess([row("a", 0.8, 0)], spec)["capacity"]
    assert result["recall"] is None and result["status"] == "not_evaluable"


def test_proposal_refuses_private_access_without_opt_in(spec):
    with pytest.raises(ValueError, match="opt_in"):
        bundle.build_proposal(Path("missing"), None, None, spec)


def setup(monkeypatch, sample):
    data, members, qp = sample
    monkeypatch.setattr(bundle, "assemble_partitioned_development", lambda *a, **kw: data)
    monkeypatch.setattr(bundle, "membership", lambda *a: members)
    monkeypatch.setattr(
        bundle, "read_json", lambda *a: {"temporal_bundle_id": data.parents["temporal_bundle_id"]}
    )
    monkeypatch.setattr(bundle, "capture", lambda p: {"sha256": "0" * 64})
    inputs = PartitionInputs(
        *(Path(name) for name in ("curated", "private", "features", "upstream", "labels"))
    )
    return data, qp, inputs


def test_proposal_is_repeatable_and_binds_true_model_calibrator_and_tune(monkeypatch, sample, spec):
    data, qp, inputs = setup(monkeypatch, sample)
    proposal = bundle.build_proposal(
        Path("temporal"), inputs, qp, spec, allow_evaluation_truth=True
    )
    assert proposal == bundle.build_proposal(
        Path("temporal"), inputs, qp, spec, allow_evaluation_truth=True
    )
    assert proposal["descriptor"]["parents"] == data.parents
    assert proposal["content"]["tune_rows"] == 44
    assert proposal["descriptor"]["model_pin"]["prediction_mode"] == "sigmoid"
    assert not proposal["content"]["serving_eligible"]
    changed = spec.model_copy(update={"capacity": OperatorCapacity(mode="top_n", top_n=2)})
    assert (
        proposal["proposal_id"]
        != bundle.build_proposal(
            Path("temporal"), inputs, qp, changed, allow_evaluation_truth=True
        )["proposal_id"]
    )


def test_parent_changed_during_policy_fit_cannot_return_proposal(monkeypatch, sample, spec):
    _, qp, inputs = setup(monkeypatch, sample)
    seals = iter([{"sha256": "0" * 64}] * 6 + [{"sha256": "1" * 64}] * 6)
    monkeypatch.setattr(bundle, "capture", lambda p: next(seals))
    with pytest.raises(ValueError, match="source_changed"):
        bundle.build_proposal(Path("temporal"), inputs, qp, spec, allow_evaluation_truth=True)


def test_independent_validation_is_not_policy_cost_source(monkeypatch, sample, spec):
    data, qp, inputs = setup(monkeypatch, sample)
    before = bundle.build_proposal(Path("temporal"), inputs, qp, spec, allow_evaluation_truth=True)
    changed = replace(
        data,
        outcomes={**data.outcomes, "calibration": [1 - y for y in data.outcomes["calibration"]]},
    )
    monkeypatch.setattr(bundle, "assemble_partitioned_development", lambda *a, **kw: changed)
    after = bundle.build_proposal(Path("temporal"), inputs, qp, spec, allow_evaluation_truth=True)
    assert before["content"]["assessment"] == after["content"]["assessment"]
    assert before["content"]["tune_labels_sha256"] == after["content"]["tune_labels_sha256"]
    assert before["proposal_id"] != after["proposal_id"]
