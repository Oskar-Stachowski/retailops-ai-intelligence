"""Cutoff, abstention, train-only preprocessing and genuine forest roundtrip semantics."""

import hashlib
import json
import shutil
from datetime import UTC, date, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError
from test_qualified_anomaly_inputs import history, project

from retailops_ai.anomaly_detectors.codec import fit_fills, forest_scores, score_matrix, transform
from retailops_ai.anomaly_detectors.contract import (
    Diagnostic,
    FitPolicy,
    Forest,
    ModelManifest,
    Node,
    Pipeline,
    Prediction,
    RunManifest,
    Runtime,
    Tree,
)
from retailops_ai.anomaly_detectors.engine import capacity_threshold, run
from retailops_ai.anomaly_detectors.fit import fit_pipeline
from retailops_ai.anomaly_detectors.protocol import Protocol, Scope, Window, requested, series_key
from retailops_ai.qualified_anomalies.contract import ModelRow, Policy
from retailops_ai.source_snapshot.files import SnapshotError, canonical_json, json_sha256

PRODUCT, LOCATION = str(UUID(int=1)), str(UUID(int=2))


def scope(kind="sale_completed", currency="PLN", product=PRODUCT):
    return Scope(
        event_type=kind,
        product_id=product,
        selling_location_id=LOCATION,
        channel="store",
        currency=currency,
    )


def protocol(scopes=None):
    return Protocol(
        scopes=tuple(sorted(scopes or [scope()], key=series_key)),
        train=Window(start=date(2026, 7, 1), end=date(2026, 7, 20)),
        validation=Window(start=date(2026, 7, 23), end=date(2026, 8, 7)),
        test=Window(start=date(2026, 8, 12), end=date(2026, 8, 27)),
        training_cutoff=datetime(2026, 7, 22, 23, 59, 59, tzinfo=UTC),
        selection_cutoff=datetime(2026, 8, 11, 23, 59, 59, tzinfo=UTC),
    )


def point(day, units=15, kind="sale_completed", currency="PLN"):
    days, replay = history(kind, currency, day)
    replay["accepted_facts"][-1]["quantity"] = units
    return project(days, replay)


def points(config):
    return [
        point(config.train.start + timedelta(days=i), 12 + i % 7, s.event_type, s.currency)
        for s in config.scopes
        for i in range((config.test.end - config.train.start).days + 1)
    ]


def runtime():
    return Runtime(
        code_files={"mechanics": "0" * 64},
        contract_files={},
        dependency_lock_sha256="0" * 64,
        python_version="3.11.15",
    )


def feature_parent():
    return SimpleNamespace(
        qualified_anomaly_input_id="qualified-anomaly-inputs-sha256-" + "0" * 64,
        descriptor=SimpleNamespace(
            policy=Policy(), model_dump=lambda **kwargs: {"mechanics_only": True}
        ),
    )


def numeric_rows(size=32):
    return [
        ModelRow(
            observed_units=10 + i,
            expected_units=10,
            residual_units=i,
            robust_scale_units=1.0,
            standardized_residual=float(i),
            planned_price=None,
            promotion_offered=False,
            on_hand=None,
        )
        for i in range(size)
    ]


@pytest.mark.parametrize(
    "change",
    ["overlap", "scope_duplicate", "scope_order", "training_clock", "selection_clock", "budget"],
)
def test_protocol_rejects_leaky_or_unbounded_splits(change):
    raw = protocol().model_dump(mode="json")
    if change == "overlap":
        raw["validation"]["start"] = "2026-07-20"
    elif change == "scope_duplicate":
        raw["scopes"] *= 2
    elif change == "scope_order":
        raw["scopes"] = [
            scope(product=str(UUID(int=3))).model_dump(mode="json"),
            scope().model_dump(mode="json"),
        ]
    elif change == "training_clock":
        raw["training_cutoff"] = "2026-07-25T00:00:00Z"
    elif change == "selection_clock":
        raw["selection_cutoff"] = "2026-08-14T00:00:00Z"
    else:
        raw["test"]["end"] = "2040-01-01"
    with pytest.raises(ValidationError):
        Protocol.model_validate_json(json.dumps(raw))


def test_membership_counts_unknown_gap_and_delayed_return_outcome():
    config = protocol([scope("return_completed")])
    known = points(config)
    known.pop(0)
    pairs = requested(config, known)
    assert len(pairs) == 58 and pairs[0][0].input_status == "no_declaration"
    assert pairs[0][1] is None and not pairs[0][0].eligible
    train = [m for m, _ in pairs if m.role == "train"]
    assert sum(m.eligible for m in train) == 17
    assert "outcome_after_training_cutoff" in train[-1].reason_codes
    assert all(
        not m.eligible and "split_gap" in m.reason_codes for m, _ in pairs if m.role == "gap"
    )


def test_input_clock_policy_mismatch_and_duplicate_keys_fail():
    config = protocol()
    p = point(config.train.start)
    with pytest.raises(SnapshotError, match="duplicate_input_key"):
        requested(config, [p, p])
    config = config.model_copy(update={"feature_policy": Policy(sales_delay_hours=0)})
    with pytest.raises(SnapshotError, match="clock_mismatch"):
        requested(config, [p])


@pytest.mark.parametrize(
    "features",
    [
        ("seed",),
        ("label",),
        ("observed_units", "observed_units"),
        ("on_hand", "observed_units"),
        (),
    ],
)
def test_fit_policy_allowlist_and_order(features):
    with pytest.raises(ValidationError):
        FitPolicy(features=features)


def test_preprocessing_uses_training_only_and_keeps_missing_flags():
    rows = numeric_rows()
    fills = fit_fills(rows, FitPolicy())
    optional = [f for f in fills if f.name in {"planned_price", "on_hand"}]
    assert all(
        f.value == 0 and f.known_count == 0 and f.reason == "entirely_missing_constant_zero"
        for f in optional
    )
    later = rows[0].model_copy(update={"planned_price": 10000.0, "on_hand": 99999})
    assert transform(later, fills)[5] == 10000.0
    assert transform(rows[0], fills)[-1] == 1.0
    assert fit_fills(rows, FitPolicy()) == fills


def test_validation_capacity_ties_and_zero_alert_budget():
    threshold = capacity_threshold([3.0] * 16, FitPolicy())
    assert threshold.allowed_alerts == 0 and threshold.threshold == threshold.high_threshold == 3
    assert sum(v > threshold.threshold for v in [3.0] * 16) == 0
    assert capacity_threshold([0.0] * 15, FitPolicy()) is None
    chosen = capacity_threshold([float(i) for i in range(100)], FitPolicy())
    assert chosen.allowed_alerts == 5 and sum(i > chosen.threshold for i in range(100)) == 5


@pytest.mark.parametrize("mutation", ["cycle", "shared", "unreachable", "partition", "nonfinite"])
def test_tree_graph_validation(mutation):
    raw = {
        "nodes": [
            {"sample_count": 16, "feature": 0, "threshold": 1.0, "left": 1, "right": 2},
            {"sample_count": 8, "feature": None, "threshold": None, "left": None, "right": None},
            {"sample_count": 8, "feature": None, "threshold": None, "left": None, "right": None},
        ]
    }
    if mutation == "cycle":
        raw["nodes"][0]["left"] = 0
    elif mutation == "shared":
        raw["nodes"][0]["right"] = 1
    elif mutation == "unreachable":
        raw["nodes"].append(raw["nodes"][1])
    elif mutation == "partition":
        raw["nodes"][1]["sample_count"] = 7
    else:
        raw["nodes"][0]["threshold"] = float("inf")
    with pytest.raises(ValidationError):
        Tree.model_validate_json(json.dumps(raw))


def test_real_forest_fit_roundtrip_and_float32_threshold_boundary():
    policy = FitPolicy(max_features=0.5)
    fitted, resource = fit_pipeline(numeric_rows(), numeric_rows(16), policy)
    restored = Pipeline.model_validate_json(fitted.model_dump_json())
    assert forest_scores(fitted, numeric_rows(16)) == forest_scores(restored, numeric_rows(16))
    assert (
        resource.native_max_score_error <= 1e-12 and resource.peak_rss_bytes <= policy.fit_rss_bytes
    )
    assert fitted == fit_pipeline(numeric_rows(), [], policy)[0]
    # 1.0 is above the double threshold, even though it rounds to 1 in float32.
    tree = Tree(
        nodes=(
            Node(sample_count=16, feature=0, threshold=0.99999999, left=1, right=2),
            Node(sample_count=1, feature=None, threshold=None, left=None, right=None),
            Node(sample_count=15, feature=None, threshold=None, left=None, right=None),
        )
    )
    forest = Forest(feature_count=2, max_samples=16, native_offset=-0.5, trees=(tree,) * 8)
    scores = score_matrix(forest, [(1.0, 0.0), (0.9999999, 0.0)])
    assert scores[0] < scores[1]


@pytest.mark.parametrize("change", ["NaN", "overflow", "rows"])
def test_invalid_numeric_training_inputs_fail_closed(change):
    rows = numeric_rows()
    if change == "rows":
        rows = rows[:15]
    else:
        rows[0] = rows[0].model_copy(
            update={"planned_price": float("nan") if change == "NaN" else 1e100}
        )
    with pytest.raises((SnapshotError, ValidationError)):
        fit_pipeline(rows, [], FitPolicy())


def test_frozen_selection_does_not_use_development_test_outcomes():
    config = protocol()
    data = points(config)
    first = run(feature_parent(), data, config, FitPolicy(), runtime())
    changed = [
        point(p.business_date, 1000) if p.business_date >= config.test.start else p for p in data
    ]
    second = run(feature_parent(), changed, config, FitPolicy(), runtime())
    assert first[0] == second[0] and first[2] == second[2]
    assert first[3] != second[3]
    predictions = [Prediction.model_validate_json(line) for line in first[3].splitlines()]
    assert len(predictions) == 32 and all(
        p.scoring_origin > config.selection_cutoff for p in predictions
    )
    assert {p.family for p in predictions} == {"seasonal_residual", "isolation_forest"}


def test_event_and_currency_groups_are_fitted_separately():
    config = protocol([scope(), scope("return_completed", "EUR")])
    result = run(feature_parent(), points(config), config, FitPolicy(), runtime())
    groups = result[0].descriptor.groups
    assert [(g.event_type, g.currency, g.training_rows) for g in groups] == [
        ("return_completed", "EUR", 18),
        ("sale_completed", "PLN", 20),
    ]


def test_unscoreable_or_missing_training_never_supplies_fake_zero_alert():
    config = protocol()
    data = [point(config.test.start)]
    result = run(feature_parent(), data, config, FitPolicy(), runtime())
    predictions = [Prediction.model_validate_json(line) for line in result[3].splitlines()]
    assert len(predictions) == 32
    assert all(
        p.status == "insufficient_data"
        and p.score is None
        and p.alert is None
        and p.severity is None
        for p in predictions
    )
    assert result[5]["train/no_declaration/excluded"] == 20


def test_packaged_schemas_are_current():
    for name, model in (
        ("protocol", Protocol),
        ("policy", FitPolicy),
        ("model", ModelManifest),
        ("run", RunManifest),
        ("prediction", Prediction),
    ):
        schema = json.loads(
            files("retailops_ai.anomaly_detectors")
            .joinpath(f"contracts/{name}.schema.json")
            .read_bytes()
        )
        assert schema == {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            **model.model_json_schema(),
        }


def test_validation_diagnostics_distinguish_unknown_and_unfitted():
    config = protocol()
    data = [point(config.validation.start)]
    result = run(feature_parent(), data, config, FitPolicy(), runtime())
    diagnostics = [Diagnostic.model_validate_json(line) for line in result[2].splitlines()]
    assert diagnostics[0].score_status == "scored" and diagnostics[0].score is not None
    assert diagnostics[1].score_status == "insufficient_training" and diagnostics[1].score is None
    assert all(d.score_status == "input_ineligible" for d in diagnostics[2:])
    with pytest.raises(ValidationError):
        Diagnostic.model_validate(diagnostics[1].model_dump() | {"score_status": "scored"})


def test_validation_outcomes_change_calibration_but_never_training():
    config = protocol()
    data = points(config)
    first = run(feature_parent(), data, config, FitPolicy(), runtime())
    changed = [
        point(p.business_date, 1000) if config.role(p.business_date) == "validation" else p
        for p in data
    ]
    second = run(feature_parent(), changed, config, FitPolicy(), runtime())
    before, after = first[0].descriptor.groups[0], second[0].descriptor.groups[0]
    assert before.pipeline == after.pipeline
    assert before.baseline_threshold != after.baseline_threshold
    assert first[0].detector_id != second[0].detector_id


@pytest.mark.parametrize("limit", ["rss", "cpu"])
def test_fit_kills_worker_when_resource_limit_is_exceeded(monkeypatch, limit):
    import retailops_ai.anomaly_detectors.fit as fitter

    class Monitor:
        def memory_info(self):
            return SimpleNamespace(rss=FitPolicy().fit_rss_bytes + 1 if limit == "rss" else 0)

        def cpu_times(self):
            return SimpleNamespace(user=61.0 if limit == "cpu" else 0.0, system=0.0)

    monkeypatch.setattr(fitter.psutil, "Process", lambda pid: Monitor())
    with pytest.raises(SnapshotError, match="resource_budget"):
        fit_pipeline(numeric_rows(), [], FitPolicy())


@pytest.fixture
def mechanics_artifact(tmp_path, monkeypatch):
    """Verifier algorithm test with explicit fake parent; native parents have a separate gate."""
    import retailops_ai.anomaly_detectors.store as store

    config = protocol()
    data = [p for p in points(config) if config.role(p.business_date) != "train"]
    parents = tuple(
        tmp_path / name for name in ("feature", "replay", "coverage", "curated", "import")
    )
    for parent in parents:
        parent.mkdir()
    raw = b"".join(canonical_json(p.model_dump(mode="json")) + b"\n" for p in data)
    (parents[0] / "features.jsonl").write_bytes(raw)
    manifest = feature_parent()
    manifest.descriptor.rows_sha256 = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr(store, "verify_features", lambda *args: manifest)
    result = store.build(*parents, tmp_path / "output/data/generated", config)
    return store, parents, result, config


def test_artifact_reuse_and_verification_preserve_bytes(mechanics_artifact, tmp_path):
    store, parents, result, config = mechanics_artifact
    directory = tmp_path / "output/data/generated/anomaly-detectors" / result["run_artifact_id"]
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    assert store.verify(directory, *parents).run_artifact_id == result["run_artifact_id"]
    assert store.build(*parents, tmp_path / "output/data/generated", config)["status"] == "reused"
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


@pytest.mark.parametrize("reseal", [False, True])
def test_artifact_rejects_modified_prediction_even_with_refreshed_seals(
    mechanics_artifact, tmp_path, reseal
):
    store, parents, result, _ = mechanics_artifact
    directory = tmp_path / "mutated"
    shutil.copytree(result["directory"], directory)
    rows = [
        json.loads(line) for line in (directory / "test_scores.jsonl").read_bytes().splitlines()
    ]
    rows[0]["observed_units"] += 1
    altered = b"".join(canonical_json(row) + b"\n" for row in rows)
    (directory / "test_scores.jsonl").write_bytes(altered)
    if reseal:
        manifest = json.loads((directory / "run_manifest.json").read_bytes())
        manifest["descriptor"]["test_scores_sha256"] = hashlib.sha256(altered).hexdigest()
        manifest["run_artifact_id"] = "anomaly-detector-run-sha256-" + json_sha256(
            manifest["descriptor"]
        )
        raw = canonical_json(manifest) + b"\n"
        (directory / "run_manifest.json").write_bytes(raw)
        (directory / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    with pytest.raises(
        SnapshotError, match="reconstruction_mismatch" if reseal else "seal_mismatch"
    ):
        store.verify(directory, *parents)


def test_artifact_rejects_parent_alias_and_symlink(mechanics_artifact, tmp_path):
    store, parents, result, config = mechanics_artifact
    with pytest.raises(SnapshotError, match="separate_generated_root"):
        store.build(*parents, parents[0] / "data/generated", config)
    directory = tmp_path / "aliased"
    shutil.copytree(result["directory"], directory)
    path = directory / "test_scores.jsonl"
    path.unlink()
    path.symlink_to(
        tmp_path / "output/data/generated/anomaly-detectors" / result["run_artifact_id"] / path.name
    )
    with pytest.raises(SnapshotError):
        store.verify(directory, *parents)
