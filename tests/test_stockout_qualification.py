"""Independent calibration, exact availability boundaries and honest segment gates."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from test_stockout_training import development as development
from test_stockout_training import parents as parents

from retailops_ai.stockout.split import SplitPolicy
from retailops_ai.stockout_qualification import bundle
from retailops_ai.stockout_qualification.contract import QualificationPolicy
from retailops_ai.stockout_qualification.fit import (
    calibration_error,
    fit_qualification,
    quality_gates,
)
from retailops_ai.stockout_temporal_storage.store import PartitionInputs
from retailops_ai.stockout_training.inputs import DevelopmentData


def clock(value):
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


@pytest.fixture
def sample(development):
    split = SplitPolicy(
        start_at="2026-04-21T00:00:00Z",
        train_until="2026-06-05T00:00:00Z",
        tune_until="2026-06-24T00:00:00Z",
        calibration_until="2026-07-13T00:00:00Z",
        test_until="2026-08-01T00:00:00Z",
        evaluated_at="2026-08-01T00:00:00Z",
    )
    rows, outcomes, membership = {}, {}, []
    for role, start, days in (
        ("train", "2026-04-28", 30),
        ("tune", "2026-06-05", 11),
        ("calibration", "2026-06-24", 11),
    ):
        rows[role], outcomes[role] = [], []
        for day in range(days):
            origin = clock(start) + timedelta(days=day, hours=12)
            for product in range(4):
                y = (product + day) % 2
                row = deepcopy(development.rows["train"][0])
                row.update(
                    product_id=f"p{product}",
                    stock_location_id=f"s{product % 2}",
                    category_id=f"c{product // 2}",
                    as_of=origin.isoformat().replace("+00:00", "Z"),
                )
                row["values"]["available_qty"] = 10 if y else 100
                row["values"]["snapshot_age_hours"] = 0.0
                row["values"]["history_constrained_days"] = 1
                rows[role].append(row)
                outcomes[role].append(y)
                end = (origin + timedelta(days=7)).isoformat().replace("+00:00", "Z")
                membership.append(
                    dict(
                        product_id=row["product_id"],
                        stock_location_id=row["stock_location_id"],
                        as_of=row["as_of"],
                        role=role,
                        eligible=True,
                        label_available_at=end,
                        window_end_at=end,
                    )
                )
    data = DevelopmentData(
        rows,
        outcomes,
        dict(
            feature_bundle_id="feature-parent",
            label_bundle_id="label-parent",
            upstream_bundle_id="upstream-parent",
            temporal_bundle_id="temporal-parent",
            source_dataset_id="source-parent",
            curated_dataset_id="curated-parent",
        ),
        split,
        {},
        "0" * 64,
    )
    policy = QualificationPolicy(
        base_fit_known_at="2026-05-20T00:00:00Z",
        calibration_start_at="2026-05-20T00:00:00Z",
        calibration_fit_known_at="2026-06-05T00:00:00Z",
        expected_category_count=2,
    )
    return data, membership, policy


def test_four_roles_are_disjoint_and_calibration_is_evaluated_later(sample):
    data, membership, policy = sample
    q = fit_qualification(data, membership, policy)["content"]
    assert q["roles"]["base_train"]["rows"] == 60
    assert q["roles"]["calibration_fit"]["rows"] == 32
    assert q["purged_base_training_rows"] == 28
    assert q["roles_disjoint"]
    assert q["calibration_evaluation_out_of_sample"]
    assert len(q["pipelines"]) == 6
    assert all(p["preprocessing"]["train_rows"] == 60 for p in q["pipelines"].values())
    assert q["gates"]["status"] == "passed"
    assert not q["final_test_outcomes_evaluated"]
    assert not q["threshold_policy_ready"]
    assert not q["model_ready"]


def test_validation_targets_do_not_change_any_fit_or_selection(sample):
    data, membership, policy = sample
    before = fit_qualification(data, membership, policy)["content"]
    changed = replace(
        data,
        outcomes={**data.outcomes, "calibration": [1 - y for y in data.outcomes["calibration"]]},
    )
    after = fit_qualification(changed, membership, policy)["content"]
    assert before["pipelines"] == after["pipelines"]
    assert before["selected_on_raw_tune"] == after["selected_on_raw_tune"]
    assert before["independent_validation"] != after["independent_validation"]
    assert after["gates"]["status"] == "not_ready"


def test_validation_features_never_change_preprocessing_or_calibrator(sample):
    data, membership, policy = sample
    before = fit_qualification(data, membership, policy)["content"]
    altered = deepcopy(data.rows)
    for row in altered["calibration"]:
        row["values"]["available_qty"] = 100000
        row["category_id"] = "future-unknown"
    after = fit_qualification(replace(data, rows=altered), membership, policy)["content"]
    assert before["pipelines"] == after["pipelines"]
    assert before["selected_on_raw_tune"] == after["selected_on_raw_tune"]
    assert not after["gates"]["required_segment_universe_complete"]


def test_label_available_exactly_at_base_fit_boundary_is_purged(sample):
    data, membership, policy = sample
    membership[0]["label_available_at"] = policy.base_fit_known_at.isoformat()
    q = fit_qualification(data, membership, policy)["content"]
    assert q["roles"]["base_train"]["rows"] == 59
    assert q["purged_base_training_rows"] == 29


@pytest.mark.parametrize(
    "change", ["role", "origin", "label_available", "window_end", "duplicate", "missing"]
)
def test_resealed_role_and_time_changes_are_refused(sample, change):
    data, membership, policy = sample
    if change == "role":
        membership[0]["role"] = "test"
    elif change == "origin":
        data.rows["train"][0]["as_of"] = "2026-07-14T00:00:00Z"
    elif change == "label_available":
        membership[0]["label_available_at"] = data.split_policy.train_until.isoformat()
    elif change == "window_end":
        membership[0]["window_end_at"] = data.split_policy.train_until.isoformat()
    elif change == "duplicate":
        membership.append(deepcopy(membership[0]))
    else:
        membership.pop(0)
    with pytest.raises(ValueError):
        fit_qualification(data, membership, policy)


def test_insufficient_calibration_classes_are_refused(sample):
    data, membership, policy = sample
    outcomes = deepcopy(data.outcomes)
    for i, row in enumerate(data.rows["train"]):
        if row["as_of"] >= "2026-05-20":
            outcomes["train"][i] = 0
    with pytest.raises(ValueError, match="class_support"):
        fit_qualification(replace(data, outcomes=outcomes), membership, policy)


def test_calibration_cannot_move_into_tuning(sample):
    data, membership, policy = sample
    policy = policy.model_copy(update=dict(calibration_fit_known_at=data.split_policy.tune_until))
    with pytest.raises(ValueError, match="chronology"):
        fit_qualification(data, membership, policy)


@pytest.mark.parametrize(
    "change", ["missing", "one_class", "few_rows", "bad_AP", "bad_Brier", "bad_calibration"]
)
def test_metric_computability_does_not_replace_quality(sample, change):
    data, membership, policy = sample
    q = fit_qualification(data, membership, policy)["content"]
    metrics = deepcopy(q["independent_validation"][q["selected_on_raw_tune"]]["sigmoid"])
    segment = metrics["category:c0"]
    if change == "missing":
        del metrics["category:c0"]
    elif change == "one_class":
        segment.update(positives=22, negatives=0, status="not_evaluable")
    elif change == "few_rows":
        segment["rows"] = 19
    elif change == "bad_AP":
        segment["average_precision"] = segment["prevalence"]
    elif change == "bad_Brier":
        segment["brier"] = segment["train_prevalence_constant_brier"]
    else:
        segment["reliability"] = [dict(count=22, mean_probability=1.0, observed_rate=0.0)]
    result = quality_gates(
        metrics, expected_categories={"c0", "c1"}, expected_locations={"s0", "s1"}, policy=policy
    )
    assert result["status"] == "not_ready"
    assert result["segments"]["category:c0"]["status"] != "passed"


def test_calibration_error_weights_bins_and_empty_bins_are_safe():
    result = calibration_error(
        dict(
            rows=10,
            reliability=[
                dict(count=8, mean_probability=0.1, observed_rate=0.0),
                dict(count=2, mean_probability=0.8, observed_rate=1.0),
                dict(count=0, mean_probability=None, observed_rate=None),
            ],
        )
    )
    assert result == 0.12


def test_card_keeps_true_partition_parent_names_and_honest_readiness(sample):
    data, membership, policy = sample
    q = fit_qualification(data, membership, policy)
    c = bundle.card(data, q)
    assert "feature_bundle_id" in c["descriptor"]["parents"]
    assert "feature_dataset_id" not in c["descriptor"]["parents"]
    assert c["descriptor"]["parents"]["qualification_id"] == q["qualification_id"]
    assert len(c["content"]["coefficient_tables"]) == 3
    assert len(c["content"]["local_factual_context"]) == 44
    assert c["content"]["readiness"]["calibration_evaluation_out_of_sample"]
    assert not c["content"]["readiness"]["model_ready"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("available_qty", 0),
        ("available_qty", None),
        ("snapshot_age_hours", 25.0),
        ("snapshot_age_hours", None),
    ],
)
def test_card_never_explains_current_stockout_or_stale_unknown_inventory(sample, field, value):
    data, _, _ = sample
    row = deepcopy(data.rows["tune"][0])
    row["values"][field] = value
    with pytest.raises(ValueError):
        bundle.factual_context(row)


def test_opt_in_refused_before_any_private_file_is_read(monkeypatch, tmp_path, sample):
    def unexpected(*args):
        pytest.fail("private inputs were read before opt-in")

    monkeypatch.setattr(bundle, "capture", unexpected)
    inputs = PartitionInputs(*(tmp_path / str(n) for n in range(5)))
    with pytest.raises(ValueError, match="opt_in"):
        bundle.build_qualification(tmp_path / "temporal", inputs, sample[2])


def test_parent_changed_during_fitting_prevents_capsule_return(monkeypatch, tmp_path, sample):
    data, members, policy = sample
    calls = []

    def capture(path):
        calls.append(path)
        return {"seal": "before" if len(calls) <= 6 else "after"}

    monkeypatch.setattr(bundle, "capture", capture)
    monkeypatch.setattr(bundle, "assemble_partitioned_development", lambda *a, **kw: data)
    monkeypatch.setattr(bundle, "read_json", lambda *a: dict(temporal_bundle_id="temporal-parent"))
    monkeypatch.setattr(bundle, "membership", lambda *a: members)
    inputs = PartitionInputs(*(tmp_path / str(n) for n in range(5)))
    with pytest.raises(ValueError, match="parent_changed"):
        bundle.build_qualification(
            tmp_path / "temporal", inputs, policy, allow_evaluation_truth=True
        )


def test_cli_failure_is_safe_and_never_publishes_missing_inputs(monkeypatch, capsys, tmp_path):
    import sys

    from retailops_ai.stockout_qualification import cli

    args = ["qualification", "build"]
    for name in (
        "curated",
        "private",
        "features",
        "upstream",
        "labels",
        "temporal",
        "policy",
        "output",
    ):
        args.extend(["--" + name, str(tmp_path / name)])
    monkeypatch.setattr(sys, "argv", args)
    assert cli.main() == 2
    assert "invalid_stockout_qualification" in capsys.readouterr().out
    assert not (tmp_path / "output").exists()
