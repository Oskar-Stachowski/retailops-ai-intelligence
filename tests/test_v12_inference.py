"""Private inference/review mechanics on explicit export, provenance and predictor doubles."""

import json
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_v12_evidence import verifier_receipt
from test_v12_runtime import COHORT, installed_double, plan, replace_child, result, runtime_fixture
from test_v12_runtime import artifacts as artifacts
from test_v12_runtime import prepared_input as prepared_input
from test_v12_runtime import tables as tables
from test_v12_runtime import timeline as timeline

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs import v12_inference, v12_runtime
from retailops_ai.forecast_jobs.inputs import PreparedInputs, publish_inputs_package, scoped_inputs
from retailops_ai.forecast_jobs.source_freshness import observations
from retailops_ai.forecast_jobs.v12_contracts import V12ExecutionLimits
from retailops_ai.forecast_jobs.v12_executor import inference_schema
from retailops_ai.forecast_jobs.v12_inference_contracts import V12InferenceResult
from retailops_ai.forecasting.manifest_contract import FoldPlan
from retailops_ai.model_lifecycle import v12_evidence, v12_release
from retailops_ai.model_lifecycle.contracts import GATES
from retailops_ai.model_lifecycle.v12_release_contracts import (
    V12ApprovalRequest,
    V12InferenceContext,
    V12InferenceRelease,
    V12Qualification,
    V12SourcePolicy,
)
from retailops_ai.source_snapshot.files import file_hash

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "sha256:" + "b" * 64


def freshness_inputs(base):
    """Upgrade independent invented input provenance; never a real source qualification."""
    raw = base.model_dump(mode="json")
    parent = raw["feature_manifest"]["descriptor"]["parent"]
    descriptor = {
        "schema_version": "1.1.0",
        "parent_source_dataset_id": parent["source_dataset_id"],
        "purpose": "unit_provenance_double_only",
        "watermarks": {},
    }
    parent["curated_descriptor_sha256"] = canonical_sha256(descriptor)
    parent["curated_dataset_id"] = "curated-sha256-" + canonical_sha256(descriptor)
    raw["feature_manifest"]["feature_set_id"] = "features-sha256-" + canonical_sha256(
        raw["feature_manifest"]["descriptor"]
    )
    raw.update(
        schema_version="1.1",
        source_freshness=dict(
            schema_version="1.0",
            policy_id="forecast-source-watermark-v1",
            stream="daily_demand_observations",
            as_of_time=raw["as_of_time"],
            curated_descriptor=descriptor,
            watermark=None,
            observations=[item.model_dump(mode="json") for item in observations(base.histories)],
        ),
    )
    raw["profile_id"] = "batch-profile-sha256-" + canonical_sha256(
        {k: v for k, v in raw.items() if k != "profile_id"}
    )
    return PreparedInputs.model_validate_json(canonical_bytes(raw))


@pytest.fixture
def inputs(prepared_input):
    return freshness_inputs(prepared_input)


def outside_fold():
    raw = plan().model_dump(mode="json")
    raw["development_holdout"] = {
        "start": (plan().development_holdout.start + timedelta(days=1)).isoformat(),
        "end": (plan().development_holdout.end + timedelta(days=1)).isoformat(),
    }
    return FoldPlan.model_validate_json(canonical_bytes(raw))


def ready_export(tmp_path, inputs, **kwargs):
    schema = {
        "type": "object",
        "properties": {
            "row": {
                "type": "object",
                "properties": {
                    "role": {"enum": ["validation", "development_holdout"]},
                    "actual": {"type": "null"},
                    "label_available_at": {"type": "null"},
                    "channel": {"enum": ["store"]},
                },
                "required": ["role", "actual", "label_available_at"],
            },
        },
    }
    kwargs.setdefault("signature_schema", schema)
    kwargs.setdefault("fold", outside_fold())
    root, _, recipe_id = runtime_fixture(tmp_path, inputs, **kwargs)
    manifest = json.loads((root / "run_manifest.json").read_text())
    descriptor = manifest["descriptor"]
    descriptor.update(forecast_model_status="ready", quality_qualification_status="passed")
    for name in ("handoff.json", "campaign/metrics.json"):
        report = json.loads((root / name).read_text())
        if name == "handoff.json":
            report.update(forecast_model_status="ready", quality_qualification_status="passed")
        else:
            report["status"] = "passed"
        (root / name).write_bytes(canonical_bytes(report) + b"\n")
    for name in descriptor["files"]:
        size, digest = file_hash(root, name)
        descriptor["files"][name] = dict(size_bytes=size, sha256=digest)
    descriptor["bytes"] = sum(ref["size_bytes"] for ref in descriptor["files"].values())
    manifest["run_id"] = "functional-v12-run-sha256-" + canonical_sha256(descriptor)
    (root / "run_manifest.json").write_bytes(canonical_bytes(manifest) + b"\n")
    return root, manifest["run_id"], recipe_id


@pytest.fixture
def loaded(tmp_path, inputs, monkeypatch):
    root, run_id, recipe_id = ready_export(tmp_path, inputs)
    monkeypatch.setattr(v12_evidence, "verify_with_wheel", lambda root, *_: verifier_receipt(root))
    return v12_runtime.load_v12(
        root,
        Path(sys.executable),
        run_id=run_id,
        cohort_id=COHORT,
        fold=plan().name,
        recipe_id=recipe_id,
    )


def inference_result(loaded, inputs, context):
    raw = result(loaded, inputs).model_dump(mode="json")
    for field in ("version", "purpose", "serving_eligible"):
        raw.pop(field)
    for prediction, row in zip(raw["predictions"], inputs.rows, strict=True):
        prediction["key"] = v12_runtime.prediction_key(loaded.pin, row, role="inference")
    raw["predictions_sha256"] = canonical_sha256(raw["predictions"])
    raw["inference"] = context.model_dump(mode="json")
    return V12InferenceResult.model_validate_json(canonical_bytes(raw))


def qualify_fixture(
    tmp_path, inputs, loaded, monkeypatch, *, transport=True, allow_new_source=False
):
    source_calls = []
    package = publish_inputs_package(inputs, tmp_path / "input-packages")

    def verified_sources(feature_dir, curated_dir, output_root, **kwargs):
        source_calls.append((feature_dir, curated_dir, kwargs))
        return publish_inputs_package(inputs, output_root)

    monkeypatch.setattr(v12_release, "build_inputs_package", verified_sources)
    if transport:
        monkeypatch.setattr(
            v12_runtime,
            "_execute",
            lambda model, request, *, inference, **_: inference_result(
                model, request.inputs, inference
            ),
        )
    destination = v12_release.qualify_v12(
        loaded.root,
        loaded.python,
        run_id=loaded.pin.run_id,
        cohort_id=COHORT,
        fold=loaded.pin.fold.name,
        recipe_id=loaded.pin.recipe_id,
        inputs_dir=package,
        feature_dir=tmp_path / "features-double",
        curated_dir=tmp_path / "curated-double",
        output_root=tmp_path / "qualifications",
        valid_until=datetime.now(UTC) + timedelta(days=1),
        allow_new_source=allow_new_source,
    )
    assert len(source_calls) == 1
    assert source_calls[0][2]["as_of"] == inputs.as_of_time
    return destination


@pytest.fixture
def qualification(tmp_path, inputs, loaded, monkeypatch):
    # _execute is imported by the inference module; patch there for explicit transport double.
    monkeypatch.setattr(
        v12_inference,
        "_execute",
        lambda model, request, *, inference, **_: inference_result(
            model, request.inputs, inference
        ),
    )
    return qualify_fixture(tmp_path, inputs, loaded, monkeypatch)


def actor():
    return Principal(
        "unit-promoter",
        frozenset({"promoter"}),
        frozenset({"model:decide"}),
        frozenset(),
        frozenset(),
        frozenset(),
    )


def review_fixture(tmp_path, qualification):
    q = v12_release._qualification(qualification)
    root = tmp_path / "review-evidence"
    (root / "reports").mkdir(parents=True)
    gates = {}
    for gate in GATES:
        raw = (
            canonical_bytes(
                dict(
                    qualification_id=q.qualification_id,
                    gate=gate,
                    status="passed",
                    evidence="explicit_unit_review_report_not_real_acceptance",
                )
            )
            + b"\n"
        )
        (root / "reports" / (gate + ".json")).write_bytes(raw)
        gates[gate] = dict(status="passed", report=v12_release.receipt(raw).model_dump(mode="json"))
    request = V12ApprovalRequest.model_validate_json(
        canonical_bytes(
            dict(
                qualification_id=q.qualification_id,
                image_digest=IMAGE,
                gates=gates,
                reason="Independent explicit unit review fixture.",
            )
        )
    )
    return root, request


def approve_fixture(tmp_path, qualification, loaded, *, operator=None, **kwargs):
    reports, request = review_fixture(tmp_path, qualification)
    return v12_release.approve_v12(
        qualification,
        loaded.root,
        loaded.python,
        actor=operator or actor(),
        request=request,
        reports_dir=reports,
        output_root=tmp_path / "approved",
        **kwargs,
    )


def test_ready_inference_uses_new_role_outside_evaluation_window_without_promoting(loaded, inputs):
    assert (
        not loaded.pin.fold.development_holdout.start
        <= inputs.as_of_time.date()
        <= loaded.pin.fold.development_holdout.end
    )
    with pytest.raises(ValueError, match="origin_outside_bound_holdout"):
        loaded.predict(inputs)
    policy = v12_inference.source_policy(loaded, inputs)
    v12_runtime.validate_inputs(loaded.pin, inputs, source_policy=policy)


def test_inference_schema_changes_only_role_and_never_source_signature(loaded):
    original = json.loads((loaded.root / "signature.json").read_text())["input_schema"]
    before = deepcopy(original)
    derived = inference_schema(original)
    assert original == before
    assert derived["properties"]["row"]["properties"]["role"] == {"enum": ["inference"]}
    derived["properties"]["row"]["properties"]["role"] = original["properties"]["row"][
        "properties"
    ]["role"]
    assert derived == original


@pytest.mark.parametrize("field", ["role", "actual", "label_available_at", "required"])
def test_unknown_original_signature_cannot_be_reinterpreted_as_inference(loaded, field):
    schema = json.loads((loaded.root / "signature.json").read_text())["input_schema"]
    row = schema["properties"]["row"]
    if field == "required":
        row["required"].remove("actual")
    else:
        row["properties"][field] = {"type": "number"}
    with pytest.raises(ValueError, match="original_signature_boundary"):
        inference_schema(schema)


@pytest.mark.parametrize("field", ["purpose", "serving_eligible", "release_id", "qualification_id"])
def test_context_does_not_let_a_probe_claim_approval(loaded, inputs, field):
    raw = dict(
        purpose="serving_load_predict_acceptance",
        serving_eligible=False,
        source_policy=v12_inference.source_policy(loaded, inputs).model_dump(mode="json"),
    )
    raw[field] = {
        "purpose": "qualified_forecast_v12",
        "serving_eligible": True,
        "release_id": "v12-inference-release-sha256-" + "a" * 64,
        "qualification_id": "v12-qualification-sha256-" + "a" * 64,
    }[field]
    with pytest.raises(ValueError):
        V12InferenceContext.model_validate_json(canonical_bytes(raw))


def test_not_ready_never_reaches_source_reconstruction_or_predict(tmp_path, inputs, monkeypatch):
    root, run_id, recipe_id = runtime_fixture(tmp_path, inputs)
    monkeypatch.setattr(v12_evidence, "verify_with_wheel", lambda root, *_: verifier_receipt(root))
    monkeypatch.setattr(
        v12_release, "build_inputs_package", lambda *_args, **_kw: pytest.fail("no source reads")
    )
    with pytest.raises(ValueError, match="model_not_ready"):
        v12_release.qualify_v12(
            root,
            Path(sys.executable),
            run_id=run_id,
            cohort_id=COHORT,
            fold=plan().name,
            recipe_id=recipe_id,
            inputs_dir=tmp_path,
            feature_dir=tmp_path,
            curated_dir=tmp_path,
            output_root=tmp_path / "qualifications",
            valid_until=datetime.now(UTC) + timedelta(days=1),
        )
    assert not (tmp_path / "qualifications").exists()


def test_qualification_preserves_pins_private_receipts_and_is_not_approval(qualification, inputs):
    q = v12_release._qualification(qualification)
    assert q.serving_eligible is False and q.smoke_profile_id == inputs.profile_id
    assert q.pin.serving_eligible is False
    assert qualification.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in qualification.iterdir())


def test_verified_parent_failure_is_not_replaced_by_declared_input_ids(
    loaded, inputs, tmp_path, monkeypatch
):
    package = publish_inputs_package(inputs, tmp_path / "inputs")

    def fail(*args, **kwargs):
        raise ValueError("original_source_verification_failed")

    monkeypatch.setattr(v12_release, "build_inputs_package", fail)
    with pytest.raises(ValueError, match="original_source_verification_failed"):
        v12_release.qualify_v12(
            loaded.root,
            loaded.python,
            run_id=loaded.pin.run_id,
            cohort_id=COHORT,
            fold=plan().name,
            recipe_id=loaded.pin.recipe_id,
            inputs_dir=package,
            feature_dir=tmp_path,
            curated_dir=tmp_path,
            output_root=tmp_path / "qualifications",
            valid_until=datetime.now(UTC) + timedelta(days=1),
        )
    assert not (tmp_path / "qualifications").exists()


def test_reconstructed_source_inputs_must_match_the_supplied_package(
    loaded, inputs, tmp_path, monkeypatch
):
    package = publish_inputs_package(inputs, tmp_path / "inputs")
    monkeypatch.setattr(
        v12_release,
        "build_inputs_package",
        lambda _f, _c, out, **_kw: publish_inputs_package(
            scoped_inputs(inputs, inputs.scope, 7), out
        ),
    )
    with pytest.raises(ValueError, match="inputs_not_from_verified_parents"):
        v12_release.qualify_v12(
            loaded.root,
            loaded.python,
            run_id=loaded.pin.run_id,
            cohort_id=COHORT,
            fold=plan().name,
            recipe_id=loaded.pin.recipe_id,
            inputs_dir=package,
            feature_dir=tmp_path,
            curated_dir=tmp_path,
            output_root=tmp_path / "qualifications",
            valid_until=datetime.now(UTC) + timedelta(days=1),
        )
    assert not (tmp_path / "qualifications").exists()


def test_nonrepeatable_prediction_cannot_create_a_qualification(
    loaded, inputs, tmp_path, monkeypatch
):
    calls = []

    def alternating(model, request, *, inference, **kwargs):
        values = inference_result(model, request.inputs, inference).model_dump(mode="json")
        calls.append(1)
        values["predictions"][0]["candidate"]["mean"] += len(calls)
        values["predictions_sha256"] = canonical_sha256(values["predictions"])
        return V12InferenceResult.model_validate_json(canonical_bytes(values))

    monkeypatch.setattr(v12_inference, "_execute", alternating)
    with pytest.raises(ValueError, match="prediction_not_repeatable"):
        qualify_fixture(tmp_path, inputs, loaded, monkeypatch)
    assert not (tmp_path / "qualifications").exists()


def test_child_cannot_substitute_inference_authorization_context(loaded, inputs, monkeypatch):
    context = V12InferenceContext(
        purpose="serving_load_predict_acceptance",
        serving_eligible=False,
        source_policy=v12_inference.source_policy(loaded, inputs),
    )
    raw = inference_result(loaded, inputs, context).model_dump(mode="json")
    raw["inference"]["source_policy"]["input_schema_sha256"] = "0" * 64
    children = replace_child(
        monkeypatch, "import sys; sys.stdout.buffer.write(" + repr(canonical_bytes(raw)) + ")"
    )
    with pytest.raises(ValueError, match="result_approval_binding"):
        v12_inference.predict_acceptance(loaded, inputs, limits=V12ExecutionLimits())
    assert len(children) == 1 and children[0].poll() == 0


def test_all_ten_review_gates_are_required(qualification, tmp_path):
    _, request = review_fixture(tmp_path, qualification)
    for change in ("missing", "failed", "not_ready", "not_evaluable", "extra"):
        raw = request.model_dump(mode="json")
        if change == "missing":
            raw["gates"].pop("segments")
        elif change == "extra":
            raw["gates"]["fake_gate"] = raw["gates"]["segments"]
        else:
            raw["gates"]["segments"]["status"] = change
        with pytest.raises(ValueError):
            V12ApprovalRequest.model_validate_json(canonical_bytes(raw))


@pytest.mark.parametrize(
    "roles,capabilities", [({"viewer"}, {"model:decide"}), ({"promoter"}, set())]
)
def test_operator_requires_both_role_and_capability(
    loaded, qualification, tmp_path, roles, capabilities
):
    principal = Principal(
        "unit-denied",
        frozenset(roles),
        frozenset(capabilities),
        frozenset(),
        frozenset(),
        frozenset(),
    )
    with pytest.raises(ValueError, match="promoter_required"):
        approve_fixture(tmp_path, qualification, loaded, operator=principal)
    assert not (tmp_path / "approved").exists()


@pytest.mark.parametrize("change", ["checksum", "binding", "symlink"])
def test_approval_rejects_unbound_or_substituted_reports(loaded, qualification, tmp_path, change):
    reports, request = review_fixture(tmp_path, qualification)
    path = reports / "reports/segments.json"
    if change == "symlink":
        backup = tmp_path / "copy.json"
        backup.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(backup)
    else:
        raw = json.loads(path.read_text())
        raw["qualification_id"] = "v12-qualification-sha256-" + "0" * 64
        path.write_bytes(canonical_bytes(raw) + b"\n")
        if change == "binding":
            gates = dict(request.gates)
            gates["segments"] = gates["segments"].model_copy(
                update={"report": v12_release.receipt(path.read_bytes())}
            )
            request = request.model_copy(update={"gates": gates})
    with pytest.raises(ValueError):
        v12_release.approve_v12(
            qualification,
            loaded.root,
            loaded.python,
            actor=actor(),
            request=request,
            reports_dir=reports,
            output_root=tmp_path / "approved",
        )


def test_release_load_requires_exact_reviewed_id_image_and_intact_private_capsule(
    loaded, qualification, tmp_path
):
    package = approve_fixture(tmp_path, qualification, loaded)
    serving = v12_release.load_approved_v12(
        package, loaded.root, loaded.python, release_id=package.name, image_digest=IMAGE
    )
    assert serving.release.activated_as_champion is False
    assert serving.release.registered_in_mlflow is False
    assert serving.release.reviewed_by == "unit-promoter"
    for release_id, image in (
        ("v12-inference-release-sha256-" + "0" * 64, IMAGE),
        (package.name, "sha256:" + "0" * 64),
    ):
        with pytest.raises(ValueError, match="release_or_image_pin"):
            v12_release.load_approved_v12(
                package, loaded.root, loaded.python, release_id=release_id, image_digest=image
            )
    (package / "smoke.json").chmod(0o644)
    with pytest.raises(ValueError, match="private_capsule_required"):
        v12_release.load_approved_v12(
            package, loaded.root, loaded.python, release_id=package.name, image_digest=IMAGE
        )


@pytest.mark.parametrize(
    "target", ["smoke.json", "qualification.json", "release.json", "reports/source.json"]
)
def test_release_rejects_corruption_before_loading_predictor(
    loaded, qualification, tmp_path, target
):
    package = approve_fixture(tmp_path, qualification, loaded)
    raw = json.loads((package / target).read_text())
    raw["unexpected_mutation"] = True
    (package / target).write_bytes(canonical_bytes(raw))
    with pytest.raises(ValueError):
        v12_release.load_approved_v12(
            package, loaded.root, loaded.python, release_id=package.name, image_digest=IMAGE
        )


def test_runtime_refuses_source_change_freshness_downgrade_and_unreviewed_limits(
    loaded, qualification, inputs, tmp_path, monkeypatch
):
    package = approve_fixture(tmp_path, qualification, loaded)
    serving = v12_release.load_approved_v12(
        package, loaded.root, loaded.python, release_id=package.name, image_digest=IMAGE
    )
    monkeypatch.setattr(
        v12_inference, "_execute", lambda *_args, **_kw: pytest.fail("must fail before child")
    )
    with pytest.raises(ValueError, match="unreviewed_resource_limit"):
        serving.predict(inputs, limits=V12ExecutionLimits(wall_seconds=120.0))
    raw = inputs.model_dump(mode="json")
    raw["schema_version"] = "1.0"
    raw.pop("source_freshness")
    raw["profile_id"] = "batch-profile-sha256-" + canonical_sha256(
        {k: v for k, v in raw.items() if k != "profile_id"}
    )
    with pytest.raises(ValueError, match="input_parent_policy_or_budget"):
        serving.predict(PreparedInputs.model_validate_json(canonical_bytes(raw)))
    policy = serving.release.qualification.source_policy.model_copy(
        update={"feature_set_id": "features-sha256-" + "0" * 64}
    )
    with pytest.raises(ValueError, match="input_parent_policy_or_budget"):
        v12_runtime.validate_inputs(loaded.pin, inputs, source_policy=policy)


def test_expired_qualification_cannot_be_reviewed_or_used(
    loaded, qualification, inputs, tmp_path, monkeypatch
):
    package = approve_fixture(tmp_path, qualification, loaded)
    serving = v12_release.load_approved_v12(
        package, loaded.root, loaded.python, release_id=package.name, image_digest=IMAGE
    )

    class Future(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(UTC) + timedelta(days=2)

    monkeypatch.setattr(v12_inference, "datetime", Future)
    with pytest.raises(ValueError, match="expired_or_pin_changed"):
        serving.predict(inputs)
    monkeypatch.setattr(v12_release, "datetime", Future)
    reports, request = review_fixture(tmp_path / "retry", qualification)
    with pytest.raises(ValueError, match="expired_or_future"):
        v12_release.approve_v12(
            qualification,
            loaded.root,
            loaded.python,
            actor=actor(),
            request=request,
            reports_dir=reports,
            output_root=tmp_path / "other-approved",
        )


def test_publication_rejects_bytes_changed_during_export_verification(
    loaded, qualification, tmp_path, monkeypatch
):
    original = v12_release._bound_export

    def mutate(*args, **kwargs):
        model = original(*args, **kwargs)
        value = json.loads((qualification / "smoke.json").read_text())
        value["cold_load_seconds"] += 0.001
        (qualification / "smoke.json").write_bytes(canonical_bytes(value))
        return model

    monkeypatch.setattr(v12_release, "_bound_export", mutate)
    with pytest.raises(ValueError, match="smoke_binding"):
        approve_fixture(tmp_path, qualification, loaded)
    assert not list((tmp_path / "approved").iterdir())


def test_worker_lost_lease_callback_reaps_child(
    loaded, qualification, inputs, tmp_path, monkeypatch
):
    package = approve_fixture(tmp_path, qualification, loaded)
    serving = v12_release.load_approved_v12(
        package, loaded.root, loaded.python, release_id=package.name, image_digest=IMAGE
    )
    # Restore the real supervisor after fixture-only prediction transport.
    monkeypatch.setattr(v12_inference, "_execute", REAL_EXECUTE)
    children = replace_child(monkeypatch, "import time; time.sleep(5)")
    ticks = []

    def fenced():
        ticks.append(1)
        if len(ticks) == 2:
            raise RuntimeError("lease_lost")

    with pytest.raises(RuntimeError, match="lease_lost"):
        serving.predict(inputs, tick=fenced)
    assert len(children) == 1 and children[0].poll() is not None


REAL_EXECUTE = v12_runtime._execute


def test_installed_predictor_double_handles_qualified_inference_outside_holdout(
    loaded, inputs, tmp_path, monkeypatch
):
    installed = installed_double(tmp_path, loaded)
    qualification = qualify_fixture(tmp_path, inputs, installed, monkeypatch, transport=False)
    package = approve_fixture(tmp_path, qualification, installed)
    serving = v12_release.load_approved_v12(
        package, installed.root, installed.python, release_id=package.name, image_digest=IMAGE
    )
    first = serving.predict(inputs)
    second = serving.predict(inputs)
    assert first.predictions_sha256 == second.predictions_sha256
    assert first.inference.serving_eligible is True
    assert first.inference.release_id == package.name
    assert first.pin.serving_eligible is False  # Original export never gains permission.
    assert len(first.predictions) == 14 and first.model_refits == 0
    assert first.published_forecast_outputs == 0
    assert all(json.loads(p.key)[1] == "inference" for p in first.predictions)
    assert first.predictions[0].candidate.mean == first.predictions[0].baseline.mean + 1
    assert not list((tmp_path / "operator-test-wheel").rglob("*.pyc"))


@pytest.mark.parametrize(
    "name,model",
    [
        ("source_policy", V12SourcePolicy),
        ("context", V12InferenceContext),
        ("result", V12InferenceResult),
        ("qualification", V12Qualification),
        ("approval_request", V12ApprovalRequest),
        ("release", V12InferenceRelease),
    ],
)
def test_inference_schema_snapshots(name, model):
    assert (
        ROOT / "contracts/forecast/v12_inference" / (name + ".schema.json")
    ).read_bytes() == canonical_bytes(model.model_json_schema()) + b"\n"
