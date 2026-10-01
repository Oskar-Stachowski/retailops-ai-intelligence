"""Formal identity, label maturity, coverage and fold-local preprocessing."""

import hashlib
import json
import shutil
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from pydantic import ValidationError
from test_forecast_features import DAY, SERIES, fact, rows
from test_forecast_features import tables as tables

from retailops_ai.curated.contract import columns_for, encoded
from retailops_ai.data_contracts.common import DateWindow, end_of_day
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.contract import (
    CalendarDescriptor,
    CalendarManifest,
    Implementation,
    OriginWindow,
    Parent,
    TaskConfig,
    make_origin,
)
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.forecasting.manifest_contract import FeaturePolicy, FoldPlan, SplitPolicy
from retailops_ai.forecasting.manifest_io import dump
from retailops_ai.forecasting.manifests import build_feature_set, verify_feature_set
from retailops_ai.forecasting.preprocessing import (
    fit_fold,
    fit_train_samples,
    load_preprocessing,
    transform,
)
from retailops_ai.forecasting.splits import (
    build_split,
    default_split,
    label_point,
    load_split,
    qualify,
    verify_split,
)
from retailops_ai.source_snapshot.files import SnapshotError


def plan():
    return FoldPlan(
        name="fold-a",
        train=DateWindow(start=DAY, end=DAY),
        validation=DateWindow(start=DAY + timedelta(days=16), end=DAY + timedelta(days=16)),
        development_holdout=DateWindow(
            start=DAY + timedelta(days=32), end=DAY + timedelta(days=32)
        ),
        training_cutoff=end_of_day(DAY + timedelta(days=15)),
        selection_cutoff=end_of_day(DAY + timedelta(days=31)),
        evaluation_cutoff=end_of_day(DAY + timedelta(days=47)),
    )


def sample(tables, day=DAY):
    view = OriginFeatures(tables, make_origin(day))
    history = view.history(SERIES)
    row = view.targets(history)[0]
    fold = plan()
    label = label_point(row, fold, tables["daily_demand_versions"])
    return row, history, label, qualify(row, history, fold, FeaturePolicy(), label)


@pytest.fixture
def timeline(tables):
    tables = deepcopy(tables)
    for name in ("channel_assignments", "assortment", "price_plans"):
        tables[name][0]["effective_to"] = DAY + timedelta(days=70)
    start = DAY - timedelta(days=39)
    for name in ("business_calendar", "category_calendar", "daily_demand_versions"):
        template = tables[name][0]
        tables[name] = []
        for i in range(110):
            row = deepcopy(template)
            row["id"] = name + str(i)
            row["business_date"] = start + timedelta(days=i)
            if name == "daily_demand_versions":
                row["observed_units"] = i + 1
                row["curated_available_at"] = datetime.combine(
                    row["business_date"] + timedelta(days=1), datetime.min.time(), UTC
                )
            tables[name].append(row)
    return tables


@pytest.fixture
def artifacts(timeline, tmp_path, monkeypatch, request):
    from retailops_ai.forecasting import features_store, splits

    curated = tmp_path / "curated"
    curated.mkdir()
    source_descriptor = {
        "source_parameters": {"profile": "controlled-temporal-fixture", "seed": 42}
    }
    (curated / "curated_manifest.json").write_text(json.dumps({"descriptor": source_descriptor}))
    parent = Parent(
        source_dataset_id="source-sha256-" + "a" * 64,
        curated_dataset_id="curated-sha256-" + "b" * 64,
        snapshot_id="snapshot-sha256-" + "c" * 64,
        curated_descriptor_sha256=canonical_sha256(source_descriptor),
        business_timezone="UTC",
        forecast_source_status="passed",
    )
    task = TaskConfig()
    origin_days = getattr(request, "param", 33)
    origins = tuple(make_origin(DAY + timedelta(days=i)) for i in range(origin_days))
    descriptor = CalendarDescriptor(
        schema_version="1.0.0",
        task=task,
        task_id=task.task_id(),
        parent=parent,
        origin_window=OriginWindow(start=DAY, end=DAY + timedelta(days=origin_days - 1)),
        implementation=Implementation(
            version="forecast-calendar-1.0.0",
            code_files={"controlled-fixture": "d" * 64},
            code_sha256=canonical_sha256({"controlled-fixture": "d" * 64}),
        ),
        calendar_content_sha256=canonical_sha256([o.model_dump(mode="json") for o in origins]),
    )
    calendar = CalendarManifest(
        schema_version="1.0.0",
        calendar_id="forecast-calendar-sha256-"
        + canonical_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
        origins=origins,
        forecast_model_status="not_ready",
        generated_at=datetime.now(UTC),
    )

    class Index:
        def __init__(self, db):
            self.db = db

        def origin_tables(self, origin):
            return {
                name: [r for r in values if r["curated_available_at"] <= origin.availability_cutoff]
                for name, values in timeline.items()
            }

    @contextmanager
    def index(*args):
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE source (kind TEXT,business_date TEXT,identifier TEXT,body BLOB)")
        for row in timeline["daily_demand_versions"]:
            for column in columns_for("daily_demand_versions"):
                row.setdefault(column["name"], None)
            row["source_data_complete"] = True
            row["quality_status"] = "valid"
            db.execute(
                "INSERT INTO source VALUES (?,?,?,?)",
                (
                    "daily_demand_versions",
                    row["business_date"].isoformat(),
                    row["id"],
                    encoded(row),
                ),
            )
        try:
            yield Index(db)
        finally:
            db.close()

    monkeypatch.setattr(features_store, "source_index", index)
    monkeypatch.setattr(splits, "source_index", index)
    features = build_feature_set(curated, calendar, tmp_path / "features")
    split = build_split(features, curated, tmp_path / "splits", SplitPolicy(folds=(plan(),)))
    return features, split, curated, calendar


def test_default_temporal_split_freezes_actual_windows_and_cutoffs():
    class Calendar:
        class descriptor:
            origin_window = OriginWindow(start=date(2026, 5, 19), end=date(2026, 7, 17))

    policy = default_split(Calendar())
    fold = policy.folds[0]
    assert fold.train == DateWindow(start=date(2026, 5, 19), end=date(2026, 5, 28))
    assert fold.validation.start == date(2026, 6, 13)
    assert fold.development_holdout.start == date(2026, 7, 8)
    assert fold.training_cutoff == end_of_day(date(2026, 6, 12))
    assert fold.evaluation_cutoff == end_of_day(date(2026, 8, 1))
    assert policy.portfolio_final_test == "not_included_not_opened"


@pytest.mark.parametrize(
    "mutation",
    [
        "short_purge",
        "overlap",
        "future_train_cutoff",
        "future_selection_cutoff",
        "early_evaluation",
    ],
)
def test_invalid_split_policy_is_rejected(mutation):
    value = plan().model_dump(mode="json")
    if mutation == "short_purge":
        value["purge_days"] = 13
    elif mutation == "overlap":
        value["validation"]["start"] = value["train"]["end"]
    elif mutation == "future_train_cutoff":
        value["training_cutoff"] = end_of_day(DAY + timedelta(days=16)).isoformat()
    elif mutation == "future_selection_cutoff":
        value["selection_cutoff"] = end_of_day(DAY + timedelta(days=32)).isoformat()
    else:
        value["evaluation_cutoff"] = end_of_day(DAY + timedelta(days=31)).isoformat()
    with pytest.raises(ValidationError):
        FoldPlan.model_validate_json(json.dumps(value))


@pytest.mark.parametrize(
    "columns", [("inventory",), ("latent_demand",), ("target_weekday", "target_weekday"), ()]
)
def test_formal_allowlist_rejects_truth_inventory_and_duplicates(columns):
    with pytest.raises(ValidationError):
        FeaturePolicy(columns=columns)


def test_label_maturity_selects_known_version_and_does_not_turn_missing_into_zero(tables):
    row = rows(tables)[1][0]
    old = next(r for r in tables["daily_demand_versions"] if r["business_date"] == row.target_date)
    correction = fact(
        **{
            **old,
            "id": "late-correction",
            "version": 2,
            "observed_units": 999,
            "curated_available_at": plan().training_cutoff + timedelta(microseconds=1),
        }
    )
    original = label_point(row, plan(), [old, correction])
    assert original.observed_sales_units == old["observed_units"]
    correction["curated_available_at"] = plan().training_cutoff
    assert label_point(row, plan(), [old, correction]).observed_sales_units == 999
    missing = label_point(row, plan(), [])
    assert missing.status == "censored" and missing.observed_sales_units is None
    correction["source_data_complete"] = False
    assert label_point(row, plan(), [old, correction]).status == "censored"


def test_cold_start_stale_and_censored_are_retained(tables):
    row, history, label, membership = sample(tables)
    assert membership.eligible
    cold = history.model_copy(update={"points": history.points[-3:]})
    assert (
        "insufficient_history"
        in qualify(
            row.model_copy(update={"history_known_days": 2}), cold, plan(), FeaturePolicy(), label
        ).reasons
    )
    stale = history.model_copy(
        update={
            "points": tuple(p for p in history.points if p.business_date < DAY - timedelta(days=3))
        }
    )
    assert "stale_history" in qualify(row, stale, plan(), FeaturePolicy(), label).reasons
    assert (
        "censored_label"
        in qualify(row, history, plan(), FeaturePolicy(), label_point(row, plan(), [])).reasons
    )


def test_fold_local_preprocessing_ignores_validation_categories_and_values(timeline):
    row, history, label, membership = sample(timeline)
    policy = FeaturePolicy()
    state = fit_train_samples(
        [(row, membership)],
        fold=plan(),
        policy=policy,
        feature_set_id="features-sha256-" + "a" * 64,
        split_id="split-sha256-" + "b" * 64,
    )
    assert (
        next(n for n in state.descriptor.numeric if n.name == "origin_lag_1_units").reason
        == "entirely_missing_constant_zero"
    )
    mutated = row.model_dump(mode="json")
    next(v for v in mutated["values"] if v["name"] == "brand")["value"] = "Unseen validation brand"
    next(v for v in mutated["values"] if v["name"] == "rolling_mean_7")["value"] = 999999.0
    changed = InputRow.model_validate_json(json.dumps(mutated))
    output = transform(changed, state, feature_set_id=state.descriptor.feature_set_id)
    positions = {name: i for i, name in enumerate(state.descriptor.output_columns)}
    assert output[positions["brand__unknown"]] == 1
    assert output[positions["origin_lag_1_units__missing"]] == 1
    assert "Unseen validation brand" not in next(
        v.categories for v in state.descriptor.categorical if v.name == "brand"
    )
    assert next(n.value for n in state.descriptor.numeric if n.name == "rolling_mean_7") != 999999
    repeat = fit_train_samples(
        [(row, membership)],
        fold=plan(),
        policy=policy,
        feature_set_id=state.descriptor.feature_set_id,
        split_id=state.descriptor.split_id,
    )
    assert repeat.preprocessing_id == state.preprocessing_id


@pytest.mark.parametrize("mutation", ["validation", "ineligible", "wrong_fold", "wrong_key"])
def test_preprocessing_rejects_rows_outside_eligible_train(tables, mutation):
    row, history, label, membership = sample(tables)
    change = (
        {"role": "validation"}
        if mutation == "validation"
        else {"eligible": False}
        if mutation == "ineligible"
        else {"fold": "other"}
        if mutation == "wrong_fold"
        else {"product_id": "other"}
    )
    with pytest.raises(SnapshotError):
        fit_train_samples(
            [(row, membership.model_copy(update=change))],
            fold=plan(),
            policy=FeaturePolicy(),
            feature_set_id="features-sha256-" + "a" * 64,
            split_id="split-sha256-" + "b" * 64,
        )


def test_artifact_path_repeats_and_retains_all_purged_rows(artifacts, tmp_path):
    features, split, curated, calendar = artifacts
    feature_manifest = verify_feature_set(features)
    manifest = verify_split(split, features)
    assert manifest.descriptor.qualification_status == "passed"
    assert manifest.descriptor.counts["fold-a:train:eligible"] == 14
    assert manifest.descriptor.counts["fold-a:validation:eligible"] == 14
    assert manifest.descriptor.counts["fold-a:development_holdout:eligible"] == 14
    assert manifest.descriptor.counts["purged_origin"] == 420
    before = {
        p.relative_to(split).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in split.rglob("*")
        if p.is_file()
    }
    assert (
        build_split(features, curated, split.parent, manifest.descriptor.resolved_policy) == split
    )
    assert before == {
        p.relative_to(split).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in split.rglob("*")
        if p.is_file()
    }
    assert build_feature_set(curated, calendar, features.parent) == features
    path = fit_fold(features, split, "fold-a", tmp_path / "preprocessing")
    fitted = load_preprocessing(path)
    assert fitted.descriptor.train_rows == 14
    assert fitted.descriptor.feature_set_id == feature_manifest.feature_set_id
    assert fit_fold(features, split, "fold-a", path.parent) == path
    for spec in manifest.tables.values():
        table = pq.read_table(split / spec.files[0].path)
        assert table.num_rows and len(set(table.column_names)) == len(table.column_names)


def test_changed_allowlist_policy_and_content_change_feature_identity(artifacts):
    features, split, curated, calendar = artifacts
    manifest = verify_feature_set(features)
    for change in (
        {"row_count": manifest.descriptor.row_count + 1},
        {"content_sha256": "f" * 64},
        {"input_code_sha256": "f" * 64},
    ):
        descriptor = manifest.descriptor.model_copy(update=change)
        assert (
            "features-sha256-" + canonical_sha256(descriptor.model_dump(mode="json"))
            != manifest.feature_set_id
        )
    changed = build_feature_set(
        curated, calendar, features.parent, FeaturePolicy(columns=("origin_lag_7_units", "brand"))
    )
    assert verify_feature_set(changed).feature_set_id != manifest.feature_set_id
    with pytest.raises(SnapshotError):
        verify_split(split, changed)


def test_not_ready_split_is_persisted_and_blocks_fit(artifacts, tmp_path):
    features, split, curated, calendar = artifacts
    policy = SplitPolicy(folds=(plan(),), minimum_eligible_rows_per_role=15)
    failed = build_split(features, curated, tmp_path / "not-ready", policy)
    assert verify_split(failed, features).descriptor.qualification_status == "not_ready"
    with pytest.raises(SnapshotError, match="not_ready"):
        fit_fold(features, failed, "fold-a", tmp_path / "preprocessing")


def test_corruption_extra_files_and_rehashed_qualification_are_rejected(artifacts, tmp_path):
    features, original, curated, calendar = artifacts
    broken = tmp_path / "broken"
    shutil.copytree(original, broken)
    manifest = load_split(broken)
    (broken / "unexpected").write_text("extra")
    with pytest.raises(SnapshotError):
        verify_split(broken, features)
    (broken / "unexpected").unlink()
    descriptor = manifest.descriptor.model_copy(
        update={"counts": {**manifest.descriptor.counts, "fold-a:train:eligible": 13}}
    )
    forged = manifest.model_copy(
        update={
            "descriptor": descriptor,
            "split_id": "split-sha256-" + canonical_sha256(descriptor.model_dump(mode="json")),
        }
    )
    dump(broken / "split_manifest.json", forged)
    with pytest.raises(SnapshotError, match="coverage"):
        verify_split(broken, features)
    path = broken / manifest.tables["labels"].files[0].path
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(SnapshotError):
        verify_split(broken, features)


def test_fitted_state_changes_per_fold_and_rejects_wrong_feature_pin(tables):
    row, history, label, membership = sample(tables)
    first = fit_train_samples(
        [(row, membership)],
        fold=plan(),
        policy=FeaturePolicy(),
        feature_set_id="features-sha256-" + "a" * 64,
        split_id="split-sha256-" + "b" * 64,
    )
    other_fold = plan().model_copy(update={"name": "fold-b"})
    other_membership = membership.model_copy(update={"fold": "fold-b"})
    second = fit_train_samples(
        [(row, other_membership)],
        fold=other_fold,
        policy=FeaturePolicy(),
        feature_set_id=first.descriptor.feature_set_id,
        split_id=first.descriptor.split_id,
    )
    assert first.preprocessing_id != second.preprocessing_id
    with pytest.raises(SnapshotError, match="feature_set"):
        transform(row, first, feature_set_id="features-sha256-" + "f" * 64)


def test_rehashed_fitted_median_is_rejected_against_train_data(artifacts, tmp_path):
    from retailops_ai.forecasting.preprocessing import verify_preprocessing

    features, split, curated, calendar = artifacts
    fitted_dir = fit_fold(features, split, "fold-a", tmp_path / "preprocessing")
    state = load_preprocessing(fitted_dir)
    changed = state.descriptor.numeric[1].model_copy(update={"value": 99999.0})
    descriptor = state.descriptor.model_copy(
        update={"numeric": (state.descriptor.numeric[0], changed, *state.descriptor.numeric[2:])}
    )
    forged = state.model_copy(
        update={
            "descriptor": descriptor,
            "preprocessing_id": "forecast-preprocessing-sha256-"
            + canonical_sha256(descriptor.model_dump(mode="json")),
        }
    )
    dump(fitted_dir / "preprocessing.json", forged)
    with pytest.raises(SnapshotError, match="train_fit"):
        verify_preprocessing(fitted_dir, features, split)


def test_cli_not_ready_and_safe_error(artifacts, tmp_path, capsys):
    from retailops_ai.forecasting.cli import main

    features, split, curated, calendar = artifacts
    blocked = build_split(
        features,
        curated,
        tmp_path / "blocked",
        SplitPolicy(folds=(plan(),), minimum_eligible_rows_per_role=100),
    )
    assert main(["split-verify", "--split-dir", str(blocked), "--feature-dir", str(features)]) == 3
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "not_ready" and output["forecast_model_status"] == "not_ready"
    assert (
        main(
            [
                "preprocessing-fit",
                "--split-dir",
                str(blocked),
                "--feature-dir",
                str(features),
                "--fold",
                "fold-a",
                "--output-root",
                str(tmp_path / "fit"),
            ]
        )
        == 2
    )
    error = capsys.readouterr().err
    assert "forecast_rejected" in error and str(tmp_path) not in error
    assert main(["features-verify", "--feature-dir", str(features)]) == 0


def test_pinned_default_policies_and_json_schemas():
    from jsonschema import Draft202012Validator

    contracts = Path(__file__).resolve().parents[1] / "contracts/forecast/v1"
    policy = FeaturePolicy.model_validate_json((contracts / "features.default.json").read_bytes())
    assert policy == FeaturePolicy()
    value = json.loads((contracts / "split.temporal.default.json").read_bytes())
    split = SplitPolicy.model_validate_json(json.dumps(value))
    assert split.folds[0].training_cutoff == end_of_day(date(2026, 6, 12))
    for name in (
        "feature_policy",
        "feature_manifest",
        "split_policy",
        "split_manifest",
        "label_point",
        "membership",
        "preprocessing",
    ):
        schema = json.loads((contracts / (name + ".schema.json")).read_bytes())
        Draft202012Validator.check_schema(schema)
