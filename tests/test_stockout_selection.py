"""Later calibration, selection bias, complete portable state and PIT refusal."""

import json
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
from test_stockout_qualification import development as development
from test_stockout_qualification import parents as parents
from test_stockout_qualification import sample as sample

from retailops_ai.stockout_selection import bundle
from retailops_ai.stockout_selection.contract import (
    ConditionalRiskPipeline,
    ConditionalSigmoid,
    QualityRequirements,
    SelectionPolicy,
)
from retailops_ai.stockout_selection.fit import fit_selection, validate_roles
from retailops_ai.stockout_selection.pipeline import offsets, predict_conditional
from retailops_ai.stockout_temporal_storage.store import PartitionInputs


def policy():
    return SelectionPolicy(requirements=QualityRequirements(expected_category_count=2))


def test_exact_later_roles_and_honest_selection_metrics(sample):
    data, membership, _ = sample
    s = fit_selection(data, membership, policy())["content"]
    assert s["roles"]["base_train"]["rows"] == 120
    assert s["roles"]["calibration_fit"]["rows"] == 44
    assert s["roles"]["development_selection"]["rows"] == 44
    assert len(s["base_pipelines"]) == 6
    assert len(s["comparison"]) + len(s["rejected_candidates"]) == 24
    assert s["selected_development_gates"]["status"] == "passed"
    assert s["metrics_interpretation"] == "development_selection_not_independent_quality"
    assert s["selection_known_at"] == "2026-07-13T00:00:00Z"
    assert all(p["preprocessing"]["train_rows"] == 120 for p in s["base_pipelines"].values())
    assert s["selected_pipeline"]["calibrator"]["calibration_rows"] == 44
    assert not any(
        s[k]
        for k in (
            "independent_quality_accepted",
            "final_test_outcomes_evaluated",
            "model_ready",
            "model_promoted",
        )
    )


def test_selection_targets_never_change_base_or_any_fitted_calibrator(sample):
    data, membership, _ = sample
    before = fit_selection(data, membership, policy())["content"]
    after = fit_selection(
        replace(
            data,
            outcomes={
                **data.outcomes,
                "calibration": [1 - y for y in data.outcomes["calibration"]],
            },
        ),
        membership,
        policy(),
    )["content"]
    assert before["base_pipelines"] == after["base_pipelines"]
    for name in before["comparison"]:
        assert before["comparison"][name]["calibrator"] == after["comparison"][name]["calibrator"]
    assert before["comparison"] != after["comparison"]
    assert after["selected_development_gates"]["status"] == "not_ready"


def test_selection_features_do_not_fit_vocabulary_or_preprocessing(sample):
    data, membership, _ = sample
    before = fit_selection(data, membership, policy())["content"]
    changed = deepcopy(data.rows)
    for row in changed["calibration"]:
        row["category_id"] = "later-category"
        row["values"]["available_qty"] = 100000
    after = fit_selection(replace(data, rows=changed), membership, policy())["content"]
    assert before["base_pipelines"] == after["base_pipelines"]
    for name in before["comparison"]:
        assert before["comparison"][name]["calibrator"] == after["comparison"][name]["calibrator"]
    assert "later-category" not in after["selected_pipeline"]["calibrator"]["categories"]
    assert not after["selected_development_gates"]["required_segment_universe_complete"]


@pytest.mark.parametrize(
    "change",
    [
        "test_role",
        "test_outcome_vector",
        "future_origin",
        "naive_origin",
        "future_label",
        "boundary_label",
        "boundary_end",
        "duplicate",
        "missing",
        "duplicate_row",
        "ineligible",
        "boolean_target",
        "omitted_eligible_row",
        "count",
        "one_class",
    ],
)
def test_test_access_and_resealed_time_role_changes_are_rejected(sample, change):
    data, members, _ = sample
    if change == "test_role":
        members[0]["role"] = "test"
    elif change == "test_outcome_vector":
        data = replace(data, outcomes={**data.outcomes, "test": [0]})
    elif change == "future_origin":
        data.rows["train"][0]["as_of"] = "2026-07-14T00:00:00Z"
    elif change == "naive_origin":
        data.rows["train"][0]["as_of"] = "2026-04-28T12:00:00"
    elif change in ("future_label", "boundary_label"):
        members[0]["label_available_at"] = (
            data.split_policy.train_until + timedelta(days=change == "future_label")
        ).isoformat()
    elif change == "boundary_end":
        members[0]["window_end_at"] = data.split_policy.train_until.isoformat()
    elif change == "duplicate":
        members.append(deepcopy(members[0]))
    elif change == "missing":
        members.pop(0)
    elif change == "duplicate_row":
        data.rows["train"].append(deepcopy(data.rows["train"][0]))
        data.outcomes["train"].append(data.outcomes["train"][0])
    elif change == "ineligible":
        members[0]["eligible"] = False
    elif change == "boolean_target":
        data.outcomes["train"][0] = False
    elif change == "omitted_eligible_row":
        data.rows["train"].pop()
        data.outcomes["train"].pop()
    elif change == "count":
        data.outcomes["train"].pop()
    else:
        data.outcomes["train"][:] = [0] * len(data.rows["train"])
    with pytest.raises(ValueError):
        validate_roles(data, members)


def test_json_roundtrip_complete_pipeline_and_conditional_monotonicity(sample):
    data, members, _ = sample
    s = fit_selection(data, members, policy())["content"]
    model = ConditionalRiskPipeline.model_validate_json(json.dumps(s["selected_pipeline"]))
    rows = deepcopy(data.rows["calibration"][:4])
    for row in rows:
        row["as_of"] = s["selection_known_at"]
    before = predict_conditional(model, rows)
    roundtrip = ConditionalRiskPipeline.model_validate_json(model.model_dump_json())
    assert np.array_equal(before, predict_conditional(roundtrip, rows))
    assert np.isfinite(before).all() and np.all((before >= 0) & (before <= 1))
    # A fixed group's offsets are constant and its raw-score slope is positive.
    c = model.calibrator
    assert c.raw_score_slope > 0
    x = np.arange(-10, 11) * c.raw_score_slope + c.intercept
    assert np.all(np.diff(np.exp(-np.logaddexp(0, -x))) > 0)
    unknown = deepcopy(rows)
    for row in unknown:
        row.update(category_id="unseen-category", stock_location_id="unseen-stock")
    matrix = offsets(unknown, c.categories, c.stock_locations)
    assert np.all(matrix[:, :-1] == 0)


def test_calibrator_cannot_score_origins_before_its_knowledge_cutoff(sample):
    data, members, _ = sample
    s = fit_selection(data, members, policy())["content"]
    model = ConditionalRiskPipeline.model_validate_json(json.dumps(s["selected_pipeline"]))
    with pytest.raises(ValueError, match="not_known_at_origin"):
        predict_conditional(model, data.rows["tune"])
    altered = model.model_copy(
        update={"calibrator": model.calibrator.model_copy(update={"raw_score_slope": -1.0})}
    )
    with pytest.raises(ValueError):
        predict_conditional(altered, data.rows["calibration"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("raw_score_slope", 0.0),
        ("offset_weights", (1.0,)),
        ("categories", ("c1", "c0")),
        ("C", 100.0),
        ("intercept", float("nan")),
    ],
)
def test_conditional_state_refuses_incomplete_or_nonfinite_recipes(field, value):
    raw = dict(
        fit_known_at="2026-06-24T00:00:00Z",
        calibration_rows=40,
        calibration_keys_sha256="0" * 64,
        calibration_labels_sha256="1" * 64,
        C=1.0,
        raw_score_slope=1.0,
        intercept=0.0,
        categories=("c0", "c1"),
        stock_locations=("s0", "s1"),
        offset_weights=(0.0,) * 5,
    )
    raw[field] = value
    with pytest.raises(ValueError):
        ConditionalSigmoid.model_validate(raw)


def test_public_builder_refuses_before_any_private_parent_access(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        pytest.fail("parent accessed without private verification opt-in")

    monkeypatch.setattr(bundle, "capture", forbidden)
    inputs = PartitionInputs(
        *[tmp_path / n for n in ("curated", "private", "features", "upstream", "labels")]
    )
    with pytest.raises(ValueError, match="opt_in_required"):
        bundle.build_selection(Path("/does-not-exist"), inputs, policy())


def test_grid_and_seed_cannot_silently_change():
    with pytest.raises(ValueError):
        SelectionPolicy(calibrator_C_grid=(1.0,))
    with pytest.raises(ValueError):
        SelectionPolicy(development_seed=137)


@pytest.mark.parametrize("seed", [137, 2026, "42", None])
def test_public_development_source_cannot_relabel_another_seed(monkeypatch, tmp_path, seed):
    monkeypatch.setattr(
        bundle, "read_json", lambda *a: dict(descriptor=dict(source_parameters=dict(seed=seed)))
    )
    with pytest.raises(ValueError, match="actual_development_seed"):
        bundle.development_source_parameters(tmp_path, policy())


def test_parent_race_prevents_a_complete_selection_capsule(monkeypatch, tmp_path, sample):
    data, members, _ = sample
    calls = []

    def capture(path):
        calls.append(path)
        return {"seal": "before" if len(calls) <= 6 else "changed"}

    monkeypatch.setattr(bundle, "capture", capture)
    monkeypatch.setattr(bundle, "assemble_partitioned_development", lambda *a, **kw: data)
    monkeypatch.setattr(bundle, "read_json", lambda *a: dict(temporal_bundle_id="temporal-parent"))
    monkeypatch.setattr(bundle, "membership", lambda *a: members)
    monkeypatch.setattr(bundle, "development_source_parameters", lambda *a: dict(seed=42))
    inputs = PartitionInputs(*(tmp_path / str(n) for n in range(5)))
    with pytest.raises(ValueError, match="parent_changed"):
        bundle.build_selection(tmp_path / "temporal", inputs, policy(), allow_evaluation_truth=True)


def test_public_card_binds_actual_source_and_never_claims_independent_quality(
    monkeypatch, tmp_path, sample
):
    data, members, _ = sample
    monkeypatch.setattr(bundle, "capture", lambda *a: {"seal": "constant"})
    monkeypatch.setattr(bundle, "assemble_partitioned_development", lambda *a, **kw: data)
    monkeypatch.setattr(bundle, "read_json", lambda *a: dict(temporal_bundle_id="temporal-parent"))
    monkeypatch.setattr(bundle, "membership", lambda *a: members)
    monkeypatch.setattr(
        bundle, "development_source_parameters", lambda *a: dict(seed=42, products=4)
    )
    inputs = PartitionInputs(*(tmp_path / str(n) for n in range(5)))
    capsule = bundle.build_selection(
        tmp_path / "temporal", inputs, policy(), allow_evaluation_truth=True
    )
    selection, card = capsule["selection"], capsule["model_card"]
    assert selection["descriptor"]["actual_development_source_parameters"]["seed"] == 42
    assert card["content"]["selection_id"] == selection["selection_id"]
    assert (
        card["content"]["complete_calibrator"]
        == selection["content"]["selected_pipeline"]["calibrator"]
    )
    assert all(
        r["role"] == "development_selection" for r in card["content"]["local_factual_context"]
    )
    assert not any(card["content"]["readiness"].values())


def test_cli_opt_in_precedes_even_policy_access(monkeypatch, tmp_path, capsys):
    import sys

    from retailops_ai.stockout_selection import cli

    args = ["selection", "build"]
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
    monkeypatch.setattr(cli, "read_bounded", lambda *a: pytest.fail("read before opt-in"))
    assert cli.main() == 2
    assert "invalid_stockout_selection" in capsys.readouterr().out
    assert not (tmp_path / "output").exists()
