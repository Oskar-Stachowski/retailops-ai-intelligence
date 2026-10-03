"""Input integrity and runtime mechanics. Mock qualifications never enter a real Registry."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from pydantic import ValidationError
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from test_forecast_features import DAY, SERIES, rows
from test_forecast_features import tables as tables
from test_forecast_manifests import artifacts as artifacts
from test_forecast_manifests import plan
from test_forecast_manifests import timeline as timeline
from threadpoolctl import threadpool_limits

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecast_jobs.contracts import BatchScope
from retailops_ai.forecast_jobs.inputs import (
    InputContent,
    PreparedInputs,
    build_inputs_package,
    prepared,
    publish_inputs_package,
    verify_inputs_package,
)
from retailops_ai.forecast_jobs.runtime import RuntimePin, load_release
from retailops_ai.forecasting.contract import make_origin
from retailops_ai.forecasting.evaluation_contract import BaselinePolicy
from retailops_ai.forecasting.features import OriginFeatures
from retailops_ai.forecasting.manifests import verify_feature_set
from retailops_ai.forecasting.model_contract import (
    HGBConfig,
    ModelPipeline,
    ModelPolicy,
    PipelineDescriptor,
    RFConfig,
)
from retailops_ai.forecasting.model_trees import ForecastAdapter, export_estimator
from retailops_ai.forecasting.models import model_code
from retailops_ai.forecasting.preprocessing import fit_train_samples, transform
from retailops_ai.forecasting.splits import label_point, qualify
from retailops_ai.model_lifecycle.baseline import BaselineInput, BaselinePipeline
from retailops_ai.model_lifecycle.contracts import MODEL, Binding, Qualification, release_for
from retailops_ai.model_lifecycle.mechanics import capsule
from retailops_ai.source_snapshot.files import SnapshotError


@pytest.fixture
def prepared_input(artifacts, timeline):
    features, *_ = artifacts
    manifest = verify_feature_set(features)
    origin = make_origin(DAY + timedelta(days=32))
    view = OriginFeatures(timeline, origin)
    history = view.history(SERIES)
    return prepared(
        InputContent(
            feature_manifest=manifest,
            as_of_time=origin.availability_cutoff,
            scope=BatchScope(product_ids=("p-1",), selling_location_ids=("s-1",), channel="store"),
            horizon_days=14,
            rows=tuple(view.targets(history)),
            histories=(history,),
        )
    )


def baseline(inputs):
    raw = {
        "format": "forecast-baseline-v1",
        "feature_set_id": "features-sha256-" + "a" * 64,
        "model": "last_observed",
        "policy": BaselinePolicy().model_dump(mode="json"),
        "selection_cutoff": (inputs.as_of_time - timedelta(days=1))
        .isoformat()
        .replace("+00:00", "Z"),
    }
    return BaselinePipeline.model_validate_json(
        json.dumps(dict(raw, model_id="model-sha256-" + canonical_sha256(raw)))
    )


class MockRegistry:
    def __init__(self, files):
        self.files, self.calls, self.failure = files, [], None

    def validate(self, binding):
        self.calls.append(binding.model_version)
        if self.failure:
            raise ValueError(self.failure)

    def artifact(self, uri, name, *, limit):
        self.calls.append(name)
        return self.files[name]


def mocked_release(inputs, pipeline, file_changes=None):
    """Explicit unit stub; it is not actual quality evidence or a real promoted version."""
    learned = isinstance(pipeline, ModelPipeline)
    policy = inputs.feature_manifest.descriptor.resolved_policy
    files = {
        "model.json": pipeline.model_dump_json().encode(),
        "config.json": canonical_bytes({"feature_policy": policy.model_dump(mode="json")}),
        "signature.json": canonical_bytes(
            {
                "input_schema_sha256": canonical_sha256(
                    (BaselineInput if not learned else type(inputs.rows[0])).model_json_schema()
                ),
                "feature_set_id": pipeline.descriptor.feature_set_id
                if learned
                else pipeline.feature_set_id,
                "target_type": "observed_sales_units",
                "output": "nonnegative_units",
            }
        ),
    }
    if file_changes:
        files.update(file_changes)
    qualification, _ = capsule(1.0, "unit-runtime-mechanics")
    raw = qualification.model_dump(mode="json")

    def receipt(name):
        return {"size_bytes": len(files[name]), "sha256": hashlib.sha256(files[name]).hexdigest()}

    raw.update(
        purpose="qualified_forecast",
        original_run_kind="historical_evidence",
        flavor="forecast-json-v1" if learned else "baseline-json-v1",
        model_family=pipeline.descriptor.family if learned else "baseline",
        feature_set_id=pipeline.descriptor.feature_set_id if learned else pipeline.feature_set_id,
        split_id=pipeline.descriptor.split_id if learned else raw["split_id"],
        dependency_lock_sha256=inputs.feature_manifest.descriptor.code.dependency_lock_sha256,
        model=receipt("model.json"),
        config=receipt("config.json"),
        signature=receipt("signature.json"),
        config_sha256=receipt("config.json")["sha256"],
    )
    q = Qualification.model_validate_json(json.dumps(raw))
    binding = Binding(
        model_name=MODEL,
        model_version="1",
        mlflow_run_id="a" * 32,
        source_uri="mlflow-artifacts:/1/" + "a" * 32 + "/artifacts/lifecycle",
        qualification_sha256=hashlib.sha256(q.model_dump_json().encode()).hexdigest(),
        qualification=q,
    )
    release = release_for(
        decision_id="decision-unit-runtime-01",
        binding=binding.model_dump(mode="json"),
        image_digest="sha256:" + "b" * 64,
        previous_release_id=None,
        previous_version=None,
        restored_from_release_id=None,
    )
    pin = RuntimePin(
        image_digest=release.image_digest, dependency_lock_sha256=q.dependency_lock_sha256
    )
    return release, pin, MockRegistry(files)


def test_self_contained_inputs_atomic_replay_tamper_and_symlink_refusal(prepared_input, tmp_path):
    root = publish_inputs_package(prepared_input, tmp_path / "inputs")
    before = (root / "inputs.json").read_bytes()
    assert publish_inputs_package(prepared_input, tmp_path / "inputs") == root
    assert (root / "inputs.json").read_bytes() == before
    assert verify_inputs_package(root) == prepared_input
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / "inputs.json").stat().st_mode & 0o777 == 0o600
    raw = json.loads(before)
    raw["rows"] = raw["rows"][:-1]
    (root / "inputs.json").write_text(json.dumps(raw))
    with pytest.raises(ValidationError):
        verify_inputs_package(root)
    (root / "inputs.json").unlink()
    target = tmp_path / "copy.json"
    target.write_bytes(before)
    (root / "inputs.json").symlink_to(target)
    with pytest.raises(SnapshotError, match="symlink"):
        verify_inputs_package(root)


@pytest.mark.parametrize("change", ["future", "history", "scope", "duplicate", "identity"])
def test_recomputed_hash_cannot_hide_invalid_inputs(prepared_input, change):
    raw = prepared_input.model_dump(mode="json")
    if change == "future":
        raw["rows"][0]["values"][0]["source_available_at"] = "2099-01-01T00:00:00Z"
    elif change == "history":
        raw["histories"][0]["points"][0]["observed_units"] += 7
    elif change == "scope":
        raw["scope"]["product_ids"] = ["outside"]
    elif change == "duplicate":
        raw["rows"].append(raw["rows"][0])
    else:
        raw["profile_id"] = "batch-profile-sha256-" + "a" * 64
    if change != "identity":
        raw["profile_id"] = "batch-profile-sha256-" + canonical_sha256(
            {k: v for k, v in raw.items() if k != "profile_id"}
        )
    with pytest.raises(ValidationError):
        PreparedInputs.model_validate_json(json.dumps(raw))


def test_loaded_release_is_frozen_when_registry_changes_and_uses_new_feature_id(prepared_input):
    release, pin, registry = mocked_release(prepared_input, baseline(prepared_input))
    loaded = load_release(release, pin, registry)
    calls = list(registry.calls)
    expected = loaded.predict(prepared_input)
    assert len(expected) == 14 and all(v == 71.0 for v in expected)
    assert (
        prepared_input.feature_manifest.feature_set_id
        != release.binding.qualification.feature_set_id
    )
    registry.files["model.json"] = b"corrupted after load"
    assert loaded.predict(prepared_input) == expected
    assert registry.calls == calls
    with pytest.raises(ValueError, match="checksum"):
        load_release(release, pin, registry)


@pytest.mark.parametrize("change", ["image", "lock", "gate", "registry", "signature", "config"])
def test_runtime_refuses_unapproved_or_incompatible_release(prepared_input, change):
    release, pin, registry = mocked_release(prepared_input, baseline(prepared_input))
    if change in {"image", "lock"}:
        pin = pin.model_copy(
            update={
                "image_digest" if change == "image" else "dependency_lock_sha256": "sha256:"
                + "c" * 64
                if change == "image"
                else "c" * 64
            }
        )
    elif change == "gate":
        raw = release.model_dump(mode="json", exclude={"release_id"})
        raw["binding"]["qualification"]["gates"]["segments"]["status"] = "failed"
        release = release_for(**raw)
    elif change == "registry":
        registry.failure = "qualified_model_quality_or_lineage_mismatch"
    else:
        registry.files[change + ".json"] = b"{}"
    with pytest.raises(ValueError):
        load_release(release, pin, registry)


def test_runtime_refuses_closed_calendar_even_with_complete_grain(prepared_input):
    release, pin, registry = mocked_release(prepared_input, baseline(prepared_input))
    loaded = load_release(release, pin, registry)
    raw = prepared_input.model_dump(mode="json", exclude={"profile_id"})
    raw["rows"][0]["target_calendar_eligible"] = False
    next(v for v in raw["rows"][0]["values"] if v["name"] == "target_location_open")["value"] = (
        False
    )
    invalid = prepared(InputContent.model_validate_json(json.dumps(raw)))
    with pytest.raises(ValueError, match="insufficient_stale_or_calendar_closed"):
        loaded.predict(invalid)


@pytest.mark.parametrize("artifact", ["signature", "config"])
def test_runtime_rejects_semantic_mismatch_even_with_matching_receipts(prepared_input, artifact):
    release, pin, registry = mocked_release(
        prepared_input, baseline(prepared_input), {artifact + ".json": b"{}"}
    )
    with pytest.raises(ValueError, match="input_signature|requires_feature_policy"):
        load_release(release, pin, registry)
    assert "model.json" in registry.calls


def test_runtime_rejects_origin_before_model_selection(prepared_input):
    recipe = baseline(prepared_input).model_dump(mode="json", exclude={"model_id"})
    recipe["selection_cutoff"] = prepared_input.as_of_time.isoformat().replace("+00:00", "Z")
    pipeline = BaselinePipeline.model_validate_json(
        json.dumps(dict(recipe, model_id="model-sha256-" + canonical_sha256(recipe)))
    )
    release, pin, registry = mocked_release(prepared_input, pipeline)
    with pytest.raises(ValueError, match="selection_or_origin"):
        load_release(release, pin, registry).predict(prepared_input)


@pytest.mark.parametrize("change", ["input_lock", "input_policy"])
def test_runtime_refuses_new_input_with_incompatible_recipe_or_lock(prepared_input, change):
    release, pin, registry = mocked_release(prepared_input, baseline(prepared_input))
    raw = prepared_input.model_dump(mode="json", exclude={"profile_id"})
    manifest = raw["feature_manifest"]
    descriptor = manifest["descriptor"]
    if change == "input_lock":
        descriptor["code"]["dependency_lock_sha256"] = "d" * 64
    else:
        for policy in ("requested_policy", "resolved_policy"):
            descriptor[policy]["maximum_observation_age_days"] = 1
    manifest["feature_set_id"] = "features-sha256-" + canonical_sha256(descriptor)
    inputs = prepared(InputContent.model_validate_json(json.dumps(raw)))
    with pytest.raises(ValueError, match="feature_compatibility"):
        load_release(release, pin, registry).predict(inputs)


def test_runtime_refuses_stale_history_without_partial_prediction(prepared_input, timeline):
    origin = make_origin(prepared_input.as_of_time.date())
    cutoff = origin.origin_date - timedelta(days=4)
    stale = dict(timeline)
    stale["daily_demand_versions"] = [
        r for r in timeline["daily_demand_versions"] if r["business_date"] <= cutoff
    ]
    view = OriginFeatures(stale, origin)
    history = view.history(SERIES)
    raw = prepared_input.model_dump(mode="json", exclude={"profile_id"})
    raw.update(
        rows=[r.model_dump(mode="json") for r in view.targets(history)],
        histories=[history.model_dump(mode="json")],
    )
    inputs = prepared(InputContent.model_validate_json(json.dumps(raw)))
    release, pin, registry = mocked_release(prepared_input, baseline(prepared_input))
    with pytest.raises(ValueError, match="insufficient_stale_or_calendar_closed"):
        load_release(release, pin, registry).predict(inputs)


@pytest.mark.parametrize("changed", ["parent", "during_stream"])
def test_builder_refuses_wrong_or_changed_verified_parent_before_publication(
    prepared_input, tmp_path, monkeypatch, changed
):
    from retailops_ai.forecast_jobs import inputs as module

    features = prepared_input.feature_manifest
    parent = features.descriptor.parent
    curated = {
        "curated_dataset_id": parent.curated_dataset_id,
        "descriptor": {"parent_source_dataset_id": parent.source_dataset_id},
        "readiness": {"forecast_source": "passed"},
    }
    # These isolated stubs exercise refusal paths; real positive verification has separate evidence.
    monkeypatch.setattr(module, "verify_curated", lambda _: curated)
    if changed == "parent":
        curated["curated_dataset_id"] = "curated-sha256-" + "f" * 64
        monkeypatch.setattr(module, "verify_feature_set", lambda _: features)
        reason = "parent_mismatch"
    else:
        descriptor = features.descriptor.model_dump(mode="json")
        descriptor["parent"]["curated_descriptor_sha256"] = canonical_sha256(curated["descriptor"])
        manifest = features.model_dump(mode="json")
        manifest.update(
            descriptor=descriptor, feature_set_id="features-sha256-" + canonical_sha256(descriptor)
        )
        features = type(features).model_validate_json(json.dumps(manifest))
        replies = iter([features, features.model_copy(update={"generated_at": datetime.now(UTC)})])
        monkeypatch.setattr(module, "verify_feature_set", lambda _: next(replies))
        monkeypatch.setattr(
            module,
            "input_models",
            lambda _, name: iter(
                prepared_input.rows if name == "features" else prepared_input.histories
            ),
        )
        reason = "changed_during_preparation"
    destination = tmp_path / "output"
    with pytest.raises(SnapshotError, match=reason):
        build_inputs_package(
            tmp_path / "features",
            tmp_path / "curated",
            destination,
            as_of=prepared_input.as_of_time,
            scope=prepared_input.scope,
            horizon_days=14,
        )
    assert not destination.exists()


@pytest.mark.parametrize("family", ["random_forest", "hist_gradient_boosting"])
def test_rf_hgb_inference_reuses_fitted_recipe_and_matches_diagnostic_adapter(
    prepared_input, timeline, family
):
    history, train = rows(timeline)
    fold = plan()
    policy = prepared_input.feature_manifest.descriptor.resolved_policy
    samples = [
        (
            r,
            qualify(
                r, history, fold, policy, label_point(r, fold, timeline["daily_demand_versions"])
            ),
        )
        for r in train
    ]
    state = fit_train_samples(
        samples,
        fold=fold,
        policy=policy,
        feature_set_id="features-sha256-" + "a" * 64,
        split_id="split-sha256-" + "b" * 64,
    )
    x = np.asarray(
        [
            (
                *transform(r, state, feature_set_id=state.descriptor.feature_set_id),
                float(r.horizon_days),
            )
            for r in train
        ]
    )
    config = ModelPolicy(
        rf=RFConfig(n_estimators=2, max_depth=2), hgb=HGBConfig(max_iter=2, min_samples_leaf=2)
    )
    with threadpool_limits(limits=1):
        native = (
            RandomForestRegressor(n_estimators=2, max_depth=2, n_jobs=1, random_state=42)
            if family == "random_forest"
            else HistGradientBoostingRegressor(
                max_iter=2,
                min_samples_leaf=2,
                categorical_features=None,
                early_stopping=False,
                random_state=42,
            )
        )
        native.fit(x, np.arange(len(train), dtype=float))
    descriptor = PipelineDescriptor(
        family=family,
        feature_set_id=state.descriptor.feature_set_id,
        split_id=state.descriptor.split_id,
        policy=config,
        preprocessing=state.descriptor,
        train_labels_content_sha256="c" * 64,
        output_columns=(*state.descriptor.output_columns, "horizon_days"),
        estimator=export_estimator(native, family, x.shape[1]),
        code=model_code(),
    )
    pipeline = ModelPipeline(
        model_id="model-sha256-" + canonical_sha256(descriptor.model_dump(mode="json")),
        descriptor=descriptor,
        generated_at=datetime.now(UTC),
    )
    release, pin, registry = mocked_release(prepared_input, pipeline)
    loaded = load_release(release, pin, registry)
    before = pipeline.model_dump_json()
    inputs = list(prepared_input.rows)
    expected = ForecastAdapter(pipeline).predict(
        inputs, feature_set_id=pipeline.descriptor.feature_set_id
    )
    assert loaded.predict(prepared_input) == expected
    assert pipeline.model_dump_json() == before
    changed = prepared_input.model_dump(mode="json", exclude={"profile_id"})
    for row in changed["rows"]:
        next(v for v in row["values"] if v["name"] == "brand")["value"] = "unseen-at-train"
    unseen = prepared(InputContent.model_validate_json(json.dumps(changed)))
    assert len(loaded.predict(unseen)) == 14
    assert loaded._adapter.pipeline.model_dump_json() == before
    with pytest.raises(SnapshotError, match="feature_binding"):
        ForecastAdapter(pipeline).predict(
            inputs, feature_set_id=prepared_input.feature_manifest.feature_set_id
        )
