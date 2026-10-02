"""New input lineage needs explicit source qualification; frozen training lineage never changes."""

import pytest
from test_v12_inference import (
    IMAGE,
    approve_fixture,
    inference_result,
    qualify_fixture,
)
from test_v12_inference import inputs as inputs
from test_v12_inference import loaded as loaded
from test_v12_runtime import artifacts as artifacts
from test_v12_runtime import installed_double
from test_v12_runtime import prepared_input as prepared_input
from test_v12_runtime import tables as tables
from test_v12_runtime import timeline as timeline

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecast_jobs import v12_inference, v12_runtime
from retailops_ai.forecast_jobs.inputs import PreparedInputs
from retailops_ai.forecast_jobs.supervisor import ExecutionError
from retailops_ai.forecast_jobs.v12_contracts import V12Execution, V12ExecutionLimits
from retailops_ai.model_lifecycle import v12_release
from retailops_ai.model_lifecycle.v12_release_contracts import V12InferenceContext, V12SourcePolicy


def new_inputs(inputs, *, source="d", snapshot="e", lock=None):
    """Independent provenance double, not a generated or qualified RetailOps snapshot."""
    raw = inputs.model_dump(mode="json")
    parent = raw["feature_manifest"]["descriptor"]["parent"]
    parent["source_dataset_id"] = "source-sha256-" + source * 64
    parent["snapshot_id"] = "snapshot-sha256-" + snapshot * 64
    descriptor = raw["source_freshness"]["curated_descriptor"]
    descriptor["parent_source_dataset_id"] = parent["source_dataset_id"]
    parent["curated_descriptor_sha256"] = canonical_sha256(descriptor)
    parent["curated_dataset_id"] = "curated-sha256-" + canonical_sha256(descriptor)
    if lock is not None:
        raw["feature_manifest"]["descriptor"]["code"]["dependency_lock_sha256"] = lock
    raw["feature_manifest"]["feature_set_id"] = "features-sha256-" + canonical_sha256(
        raw["feature_manifest"]["descriptor"]
    )
    raw["profile_id"] = "batch-profile-sha256-" + canonical_sha256(
        {key: value for key, value in raw.items() if key != "profile_id"}
    )
    return PreparedInputs.model_validate_json(canonical_bytes(raw))


def test_legacy_policy_roundtrip_retains_exact_bytes_and_no_new_fields(loaded, inputs):
    policy = v12_inference.source_policy(loaded, inputs)
    raw = policy.model_dump(mode="json")
    assert set(raw) == {
        "version",
        "mode",
        "feature_set_id",
        "curated_descriptor_sha256",
        "input_role",
        "input_schema_sha256",
        "source_change",
    }
    assert canonical_bytes(
        V12SourcePolicy.model_validate_json(canonical_bytes(raw)).model_dump(mode="json")
    ) == canonical_bytes(raw)


def test_new_snapshot_requires_explicit_qualification_and_cannot_enter_offline_replay(
    loaded, inputs
):
    fresh = new_inputs(inputs)
    with pytest.raises(ValueError, match="input_parent_policy_or_budget"):
        v12_inference.source_policy(loaded, fresh)
    with pytest.raises(ValueError, match="input_parent_policy_or_budget"):
        loaded.predict(fresh)
    policy = v12_inference.source_policy(loaded, fresh, allow_new_source=True)
    v12_runtime.validate_inputs(loaded.pin, fresh, source_policy=policy)
    assert policy.source_dataset_id != loaded.pin.source_dataset_id
    assert policy.snapshot_id != loaded.pin.snapshot_id


@pytest.mark.parametrize(
    "change", ["version", "mode", "source_missing", "snapshot_missing", "legacy_source"]
)
def test_policy_cannot_mix_legacy_and_new_source_authority(loaded, inputs, change):
    raw = v12_inference.source_policy(loaded, new_inputs(inputs), allow_new_source=True).model_dump(
        mode="json"
    )
    if change in ("version", "legacy_source"):
        raw["version"] = "forecast-v12-source-policy-1.0.0"
        if change == "legacy_source":
            raw["mode"] = "same_verified_feature_package"
    elif change == "mode":
        raw["mode"] = "same_verified_feature_package"
    else:
        raw.pop("source_dataset_id" if change == "source_missing" else "snapshot_id")
    with pytest.raises(ValueError, match="version_or_lineage"):
        V12SourcePolicy.model_validate_json(canonical_bytes(raw))


@pytest.mark.parametrize("change", ["source", "snapshot", "feature", "curated", "lock"])
def test_reviewed_policy_refuses_substitution_of_new_input_parents(loaded, inputs, change):
    fresh = new_inputs(inputs)
    policy = v12_inference.source_policy(loaded, fresh, allow_new_source=True)
    if change == "source":
        changed = new_inputs(inputs, source="c")
    elif change == "snapshot":
        changed = new_inputs(inputs, snapshot="c")
    elif change == "lock":
        changed = new_inputs(inputs, lock="c" * 64)
    else:
        policy = policy.model_copy(
            update={
                "feature_set_id"
                if change == "feature"
                else "curated_descriptor_sha256": "features-sha256-" + "c" * 64
                if change == "feature"
                else "c" * 64,
            }
        )
        changed = fresh
    with pytest.raises(ValueError, match="input_parent_policy_or_budget"):
        v12_runtime.validate_inputs(loaded.pin, changed, source_policy=policy)


def test_new_source_qualification_still_reconstructs_verified_parents(
    loaded, inputs, tmp_path, monkeypatch
):
    fresh = new_inputs(inputs)
    calls = []

    def probe(model, request, *, inference, **kwargs):
        calls.append(inference)
        return inference_result(model, request.inputs, inference)

    monkeypatch.setattr(v12_inference, "_execute", probe)
    path = qualify_fixture(tmp_path, fresh, loaded, monkeypatch, allow_new_source=True)
    q = v12_release._qualification(path)
    assert len(calls) == 2 and calls[0] == calls[1]
    assert q.pin == loaded.pin
    assert q.source_policy.snapshot_id == fresh.feature_manifest.descriptor.parent.snapshot_id
    assert q.source_packages_verified and q.repeatability_verified and not q.serving_eligible
    assert q.development_acceptance is None


def test_installed_child_uses_new_reviewed_input_without_changing_training_pin(
    loaded, inputs, tmp_path, monkeypatch
):
    fresh = new_inputs(inputs)
    installed = installed_double(tmp_path, loaded)
    path = qualify_fixture(
        tmp_path, fresh, installed, monkeypatch, transport=False, allow_new_source=True
    )
    approved = approve_fixture(tmp_path, path, installed)
    serving = v12_release.load_approved_v12(
        approved,
        installed.root,
        installed.python,
        release_id=approved.name,
        image_digest=IMAGE,
    )
    first, second = serving.predict(fresh), serving.predict(fresh)
    assert first.predictions_sha256 == second.predictions_sha256
    assert first.pin == loaded.pin
    assert first.inference.source_policy.snapshot_id != first.pin.snapshot_id
    assert first.model_refits == 0 and first.published_forecast_outputs == 0
    with pytest.raises(ValueError, match="input_parent_policy_or_budget"):
        serving.predict(inputs)
    context = serving.release.context().model_dump(mode="json")
    context["source_policy"]["snapshot_id"] = "snapshot-sha256-" + "c" * 64
    with pytest.raises(ExecutionError):
        v12_runtime._execute(
            installed,
            V12Execution(pin=installed.pin, inputs=fresh, limits=V12ExecutionLimits()),
            inference=V12InferenceContext.model_validate_json(canonical_bytes(context)),
        )
    assert not list((tmp_path / "operator-test-wheel").rglob("*.pyc"))
