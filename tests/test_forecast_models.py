"""Native/portable equivalence, train-only fitting, temporal refusal and shared coverage."""

import hashlib
import json
import shutil

import numpy as np
import pytest
from pydantic import ValidationError
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from test_forecast_baselines import metric
from test_forecast_features import tables as tables
from test_forecast_manifests import artifacts as artifacts
from test_forecast_manifests import timeline as timeline
from threadpoolctl import threadpool_limits

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecasting.evaluation_contract import BaselinePolicy
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.forecasting.manifests import input_models
from retailops_ai.forecasting.model_contract import (
    HGBConfig,
    LearnedEstimator,
    LearnedTree,
    ModelPipeline,
    ModelPolicy,
    RFConfig,
    TreeNode,
)
from retailops_ai.forecasting.model_trees import ForecastAdapter, TreePredictor, export_estimator
from retailops_ai.forecasting.models import (
    LEARNED,
    build_comparison,
    compare_validation,
    decode_model_json,
    fit_worker,
    load_comparison,
    model_card,
    verify_comparison,
)
from retailops_ai.source_snapshot.files import SnapshotError


def small_policy(**kwargs):
    return ModelPolicy(
        rf=RFConfig(n_estimators=4, max_depth=4),
        hgb=HGBConfig(max_iter=5, min_samples_leaf=2),
        **kwargs,
    )


def test_model_json_budget_accepts_large_models_without_weakening_metadata(monkeypatch):
    from retailops_ai.forecasting import models
    from retailops_ai.source_snapshot.files import MAX_METADATA_BYTES, decode_json

    raw = json.dumps({"model_data": "x" * MAX_METADATA_BYTES}).encode()
    assert len(raw) > MAX_METADATA_BYTES
    assert len(decode_model_json(raw)["model_data"]) == MAX_METADATA_BYTES
    with pytest.raises(SnapshotError, match="metadata_size_limit"):
        decode_json(raw)
    for invalid in (b'{"value":1,"value":2}', b'{"value":NaN}', b'{"value":Infinity}', b"[]"):
        with pytest.raises(SnapshotError):
            decode_model_json(invalid)
    monkeypatch.setattr(models, "MAX_MODEL_BYTES", 1024)
    with pytest.raises(SnapshotError, match="forecast_model_json_size_limit"):
        decode_model_json(raw)


@pytest.mark.parametrize("family", LEARNED)
def test_exported_json_matches_native_regressor_on_unseen_values_and_zero_targets(family):
    x = np.arange(240, dtype=np.float64).reshape(60, 4) / 11
    y = np.asarray([float(i % 7) * 3 for i in range(60)])
    extra = np.asarray(
        [[0.01, 7.0, 0.0, -4.0], [19.9, 2.4, 40.0, 100.0], [16777217.0, 16777216.0, 1.0, 0.0]]
    )
    for target in (y, np.zeros(len(y))):
        with threadpool_limits(limits=1):
            model = (
                RandomForestRegressor(n_estimators=3, max_depth=5, random_state=42, n_jobs=1)
                if family == "random_forest"
                else HistGradientBoostingRegressor(
                    max_iter=7,
                    min_samples_leaf=2,
                    early_stopping=False,
                    categorical_features=None,
                    random_state=42,
                )
            )
            model.fit(x, target)
            stored = LearnedEstimator.model_validate_json(
                export_estimator(model, family, x.shape[1]).model_dump_json()
            )
            actual = TreePredictor(stored).matrix(extra)
            expected = np.maximum(model.predict(extra), 0.0)
        np.testing.assert_allclose(actual, expected, rtol=1e-12, atol=1e-12)
        if not target.any():
            assert (actual == 0).all()


@pytest.mark.parametrize(
    "nodes",
    [
        (
            TreeNode(value=0.0, feature=0, threshold=1.0, left=0, right=1),
            TreeNode(value=1.0, feature=None, threshold=None, left=None, right=None),
        ),
        (
            TreeNode(value=0.0, feature=None, threshold=None, left=None, right=None),
            TreeNode(value=1.0, feature=None, threshold=None, left=None, right=None),
        ),
    ],
)
def test_cyclic_or_unreachable_tree_state_is_rejected(nodes):
    with pytest.raises(ValidationError):
        LearnedTree(nodes=nodes)


@pytest.mark.parametrize(
    "budget", [{"fit_wall_seconds": 0.001}, {"fit_cpu_seconds": 0.001}, {"fit_rss_bytes": 1024**2}]
)
def test_worker_enforces_wall_cpu_and_memory_limits_without_output(budget):
    x = np.arange(64, dtype=np.float64).reshape(32, 2)
    with pytest.raises(SnapshotError, match="budget_exceeded"):
        fit_worker(x, np.ones(32), "random_forest", small_policy(**budget))


def test_matrix_budget_fails_before_fit_and_hgb_random_early_stopping_is_not_allowed():
    with pytest.raises(SnapshotError, match="matrix_budget"):
        fit_worker(
            np.zeros((100, 2)), np.zeros(100), "random_forest", small_policy(max_matrix_bytes=1024)
        )
    with pytest.raises(ValidationError):
        HGBConfig(early_stopping=True)


def test_baseline_is_retained_for_ties_and_less_than_five_percent_improvement():
    policy = ModelPolicy()
    metrics = {name: metric([100], [110.0]) for name in (*policy.baseline.candidates, *LEARNED)}
    args = dict(
        fold="a", feature_set_id="features-sha256-" + "a" * 64, split_id="split-sha256-" + "b" * 64
    )
    assert compare_validation(policy, metrics, "c" * 64, **args).selected == "last_observed"
    metrics["random_forest"] = metric([100], [109.6])
    assert compare_validation(policy, metrics, "c" * 64, **args).selected == "last_observed"
    metrics["random_forest"] = metric([100], [109.0])
    metrics["hist_gradient_boosting"] = metric([100], [109.0])
    assert compare_validation(policy, metrics, "c" * 64, **args).selected == "random_forest"
    metrics["seasonal_naive7"] = metric([100], [None])
    assert compare_validation(policy, metrics, "c" * 64, **args).selected is None
    metrics["seasonal_naive7"] = metric([100, 100], [100.0, 100.0])
    with pytest.raises(SnapshotError, match="common_keys"):
        compare_validation(policy, metrics, "c" * 64, **args)


def test_train_only_pipelines_shared_keys_replay_and_rehashed_model_forgery(
    artifacts, tmp_path, monkeypatch
):
    features, split, *_ = artifacts
    from collections import Counter

    from retailops_ai.forecasting import models
    from retailops_ai.forecasting.manifest_contract import LabelPoint
    from retailops_ai.forecasting.manifest_io import iter_table
    from retailops_ai.forecasting.manifests import feature_key
    from retailops_ai.forecasting.splits import load_split

    split_manifest = load_split(split)
    train_labels = sorted(
        (
            label
            for label in iter_table(split, "labels", split_manifest.tables["labels"], Counter())
            if isinstance(label, LabelPoint) and label.role == "train"
        ),
        key=feature_key,
    )
    expected_y = np.asarray(
        [label.observed_sales_units for label in train_labels], dtype=np.float64
    )
    captured = []
    original_fit = models.fit_worker

    def recording_fit(x, y, family, policy):
        captured.append(y.copy())
        return original_fit(x, y, family, policy)

    monkeypatch.setattr(models, "fit_worker", recording_fit)
    directory = build_comparison(features, split, tmp_path / "models", small_policy())
    manifest = verify_comparison(directory, features, split)
    before = {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in directory.rglob("*")
        if p.is_file()
    }
    assert build_comparison(features, split, tmp_path / "models", small_policy()) == directory
    assert {
        p.relative_to(directory).as_posix(): p.read_bytes()
        for p in directory.rglob("*")
        if p.is_file()
    } == before
    assert manifest.predictions.row_count == 462 * 5
    assert manifest.descriptor.status == "passed" and manifest.forecast_model_status == "not_ready"
    assert manifest.descriptor.selections[0].selected == "last_observed"
    records = [json.loads(line) for line in before["predictions.jsonl"].splitlines()]
    sets = {
        name: {
            tuple(
                row[k]
                for k in (
                    "fold",
                    "role",
                    "forecast_origin",
                    "product_id",
                    "selling_location_id",
                    "channel",
                    "target_date",
                )
            )
            for row in records
            if row["model"] == name
        }
        for name in (*BaselinePolicy().candidates, *LEARNED)
    }
    assert len({frozenset(keys) for keys in sets.values()}) == 1
    for family in LEARNED:
        receipt = manifest.pipelines["fold-a:" + family]
        pipeline = ModelPipeline.model_validate_json(before[receipt.path])
        assert pipeline.descriptor.preprocessing.train_rows == 14
        assert pipeline.descriptor.output_columns[-1] == "horizon_days"
        adapter = ForecastAdapter(pipeline)
        train = [
            row
            for row in input_models(features, "features")
            if isinstance(row, InputRow)
            and pipeline.descriptor.preprocessing.fold.role(row.forecast_origin.date()) == "train"
        ]
        validation = [
            row
            for row in input_models(features, "features")
            if isinstance(row, InputRow)
            and pipeline.descriptor.preprocessing.fold.role(row.forecast_origin.date())
            == "validation"
        ]
        with pytest.raises(SnapshotError, match="not_known_at_origin"):
            adapter.predict(train, feature_set_id=manifest.descriptor.feature_set_id)
        assert (
            len(adapter.diagnose_train(train, feature_set_id=manifest.descriptor.feature_set_id))
            == 14
        )
        forecasts = adapter.forecast(validation, feature_set_id=manifest.descriptor.feature_set_id)
        assert {row.horizon_days for row in forecasts} == set(range(1, 15))
        assert {row.target_type for row in forecasts} == {"observed_sales_units"}
        assert [
            (
                row.forecast_origin,
                row.target_date,
                row.horizon_days,
                row.product_id,
                row.selling_location_id,
                row.channel,
            )
            for row in forecasts
        ] == [
            (
                row.forecast_origin,
                row.target_date,
                row.horizon_days,
                row.product_id,
                row.selling_location_id,
                row.channel,
            )
            for row in validation
        ]
        with pytest.raises(SnapshotError, match="feature_binding"):
            adapter.predict(validation, feature_set_id="features-sha256-" + "f" * 64)
        assert all(
            row["prediction_kind"] == "in_sample_diagnostic"
            for row in records
            if row["model"] == family and row["role"] == "train"
        )
        assert manifest.resources["fold-a:" + family].peak_rss_bytes <= small_policy().fit_rss_bytes
    forged = tmp_path / "forged"
    shutil.copytree(directory, forged)
    payload = json.loads(before["comparison_manifest.json"])
    name = "fold-a:random_forest"
    path = payload["pipelines"][name]["path"]
    pipeline = json.loads(before[path])
    node = next(
        node
        for node in pipeline["descriptor"]["estimator"]["trees"][0]["nodes"]
        if node["feature"] is None
    )
    node["value"] += 100.0
    pipeline["model_id"] = "model-sha256-" + canonical_sha256(pipeline["descriptor"])
    raw = canonical_bytes(pipeline)
    (forged / path).write_bytes(raw)
    payload["pipelines"][name].update(size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    payload["descriptor"]["models"][name] = pipeline["model_id"]
    for record in records:
        if record["model"] == "random_forest":
            record["model_id"] = pipeline["model_id"]
    content = b"".join(canonical_bytes(record) + b"\n" for record in records)
    (forged / "predictions.jsonl").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    payload["predictions"]["files"][0].update(size_bytes=len(content), sha256=digest)
    payload["predictions"]["content_sha256"] = digest
    payload["descriptor"]["predictions_content_sha256"] = digest
    payload["comparison_id"] = "forecast-model-comparison-sha256-" + canonical_sha256(
        payload["descriptor"]
    )
    (forged / "comparison_manifest.json").write_text(json.dumps(payload))
    from retailops_ai.forecasting.model_contract import ModelRunManifest

    (forged / "model_card.json").write_bytes(
        canonical_bytes(model_card(ModelRunManifest.model_validate_json(json.dumps(payload))))
        + b"\n"
    )
    load_comparison(forged)
    with pytest.raises(SnapshotError, match="retraining_replay_mismatch"):
        verify_comparison(forged, features, split)
    assert len(captured) == 8
    for actual in captured:
        np.testing.assert_array_equal(actual, expected_y)
