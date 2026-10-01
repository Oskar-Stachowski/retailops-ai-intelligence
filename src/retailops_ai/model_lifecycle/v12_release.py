"""Source-verified v12 acceptance and private operator review, before registry activation."""

import hashlib
import os
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from pydantic import TypeAdapter

from retailops_ai.data_contracts.common import UtcTime
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.domain.access import Principal
from retailops_ai.forecast_jobs.inputs import (
    PreparedInputs,
    build_inputs_package,
    verify_inputs_package,
)
from retailops_ai.forecast_jobs.v12_contracts import MAX_REQUEST_BYTES, V12ExecutionLimits
from retailops_ai.forecast_jobs.v12_inference import (
    LoadedV12Inference,
    predict_acceptance,
    source_policy,
)
from retailops_ai.forecast_jobs.v12_inference_contracts import V12InferenceResult
from retailops_ai.forecast_jobs.v12_runtime import LoadedV12Forecast, load_v12, prediction_key
from retailops_ai.model_lifecycle.contracts import GATES, Receipt
from retailops_ai.model_lifecycle.v12_release_contracts import (
    V12ApprovalRequest,
    V12InferenceRelease,
    V12Qualification,
)
from retailops_ai.source_snapshot.files import (
    checked_directory,
    decode_json,
    inventory,
    read_bytes,
    read_json,
    regular_file,
)
from retailops_ai.source_snapshot.publish import fsync_tree, publish_noreplace

QUALIFICATION_FILES = {"qualification.json", "smoke.json", "inputs.json"}


def receipt(raw: bytes) -> Receipt:
    return Receipt(size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def _json(value: object) -> bytes:
    return canonical_bytes(value) + b"\n"


def _time(value: datetime) -> str:
    return str(
        TypeAdapter(UtcTime).dump_python(TypeAdapter(UtcTime).validate_python(value), mode="json")
    )


def _private(root: Path, names: set[str]) -> None:
    inventory(root, names)
    info = root.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("v12_private_capsule_required")
    for name in names:
        with regular_file(root, name) as stream:
            info = os.fstat(stream.fileno())
            if (
                info.st_uid != os.geteuid()
                or not stat.S_ISREG(info.st_mode)
                or info.st_mode & 0o077
            ):
                raise ValueError("v12_private_capsule_required")


def _publish(
    output: Path, identity: str, files: dict[str, bytes], parents: tuple[Path, ...]
) -> Path:
    output = output.absolute()
    if any(output.is_relative_to(parent.absolute()) for parent in parents):
        raise ValueError("v12_release_output_inside_input")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    checked_directory(output)
    with tempfile.TemporaryDirectory(prefix=".v12-release-", dir=output) as temporary:
        staging = Path(temporary)
        staging.chmod(0o700)
        for name, raw in files.items():
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            destination.write_bytes(raw)
            destination.chmod(0o600)
        # Bind the copied bytes before publishing, including mutations during a long verifier.
        qualification = _qualification(staging)
        if "release.json" in files:
            release = V12InferenceRelease.model_validate_json(
                canonical_bytes(read_json(staging, "release.json"))
            )
            if release.qualification != qualification:
                raise ValueError("v12_release_publication_qualification_changed")
            _reports(staging, release.approval)
        fsync_tree(staging)
        destination = output / identity
        try:
            publish_noreplace(staging, destination)
        except FileExistsError:
            _private(destination, set(files))
            if any(read_bytes(destination, name) != raw for name, raw in files.items()):
                raise ValueError("v12_release_publication_conflict") from None
        return destination


def qualify_v12(
    root: Path,
    python: Path,
    *,
    run_id: str,
    cohort_id: str,
    fold: str,
    recipe_id: str,
    inputs_dir: Path,
    feature_dir: Path,
    curated_dir: Path,
    output_root: Path,
    valid_until: datetime,
    limits: V12ExecutionLimits | None = None,
    verify_timeout_seconds: int = 3600,
) -> Path:
    """Verify the whole export and both data parents, then probe the inference role twice."""
    limits = limits or V12ExecutionLimits()
    valid_until = TypeAdapter(UtcTime).validate_python(valid_until)
    loaded = load_v12(
        root,
        python,
        run_id=run_id,
        cohort_id=cohort_id,
        fold=fold,
        recipe_id=recipe_id,
        verify_timeout_seconds=verify_timeout_seconds,
    )
    if loaded.pin.forecast_model_status != "ready":
        raise ValueError("v12_qualification_model_not_ready")
    inputs = verify_inputs_package(inputs_dir)
    # A declared source ID alone is insufficient: reconstruct from verified immutable parents.
    with tempfile.TemporaryDirectory(prefix="ai05-v12-source-acceptance-") as temporary:
        verified = build_inputs_package(
            feature_dir,
            curated_dir,
            Path(temporary).resolve(),
            as_of=inputs.as_of_time,
            scope=inputs.scope,
            horizon_days=inputs.horizon_days,
        )
        if verify_inputs_package(verified) != inputs:
            raise ValueError("v12_qualification_inputs_not_from_verified_parents")
    first = predict_acceptance(loaded, inputs, limits=limits)
    second = predict_acceptance(loaded, inputs, limits=limits)
    if first.predictions_sha256 != second.predictions_sha256:
        raise ValueError("v12_qualification_prediction_not_repeatable")
    smoke_raw = _json(first.model_dump(mode="json"))
    inputs_raw = _json(inputs.model_dump(mode="json"))
    if len(inputs_raw) > MAX_REQUEST_BYTES:
        raise ValueError("v12_qualification_inputs_byte_limit")
    raw = dict(
        version="forecast-v12-qualification-1.0.0",
        purpose="serving_load_predict_acceptance",
        pin=loaded.pin.model_dump(mode="json"),
        source_policy=source_policy(loaded, inputs).model_dump(mode="json"),
        limits=limits.model_dump(mode="json"),
        smoke_profile_id=inputs.profile_id,
        smoke_scope=inputs.scope.model_dump(mode="json"),
        smoke_rows=len(first.predictions),
        smoke=receipt(smoke_raw).model_dump(mode="json"),
        inputs=receipt(inputs_raw).model_dump(mode="json"),
        created_at=_time(datetime.now(UTC)),
        valid_until=_time(valid_until),
        source_packages_verified=True,
        full_export_verified=True,
        repeatability_verified=True,
        serving_eligible=False,
    )
    raw["qualification_id"] = "v12-qualification-sha256-" + canonical_sha256(raw)
    qualification = V12Qualification.model_validate_json(canonical_bytes(raw))
    return _publish(
        output_root,
        qualification.qualification_id,
        {
            "qualification.json": _json(qualification.model_dump(mode="json")),
            "smoke.json": smoke_raw,
            "inputs.json": inputs_raw,
        },
        (root, inputs_dir, feature_dir, curated_dir),
    )


def _qualification(root: Path) -> V12Qualification:
    qualification = V12Qualification.model_validate_json(
        canonical_bytes(read_json(root, "qualification.json"))
    )
    smoke_raw, inputs_raw = read_bytes(root, "smoke.json"), read_bytes(root, "inputs.json")
    inputs = PreparedInputs.model_validate_json(canonical_bytes(decode_json(inputs_raw)))
    smoke = V12InferenceResult.model_validate_json(canonical_bytes(decode_json(smoke_raw)))
    if (
        receipt(smoke_raw) != qualification.smoke
        or receipt(inputs_raw) != qualification.inputs
        or smoke.pin != qualification.pin
        or smoke.inference.purpose != "serving_load_predict_acceptance"
        or smoke.inference.source_policy != qualification.source_policy
        or smoke.profile_id != inputs.profile_id
        or smoke.profile_id != qualification.smoke_profile_id
        or inputs.scope != qualification.smoke_scope
        or len(smoke.predictions) != qualification.smoke_rows
        or len(inputs.rows) != qualification.smoke_rows
        or [p.key for p in smoke.predictions]
        != [prediction_key(qualification.pin, row, role="inference") for row in inputs.rows]
        or inputs.feature_manifest.feature_set_id != qualification.source_policy.feature_set_id
        or smoke.peak_rss_bytes > qualification.limits.rss_bytes
        or smoke.cold_load_seconds + smoke.compute_seconds > qualification.limits.wall_seconds
        or smoke.generated_at > qualification.created_at
    ):
        raise ValueError("v12_qualification_smoke_binding")
    return qualification


def _bound_export(
    qualification: V12Qualification, root: Path, python: Path, timeout: int
) -> LoadedV12Forecast:
    if not qualification.created_at <= datetime.now(UTC) < qualification.valid_until:
        raise ValueError("v12_qualification_expired_or_future")
    pin = qualification.pin
    loaded = load_v12(
        root,
        python,
        run_id=pin.run_id,
        cohort_id=pin.cohort_id,
        fold=pin.fold.name,
        recipe_id=pin.recipe_id,
        verify_timeout_seconds=timeout,
    )
    if loaded.pin != pin:
        raise ValueError("v12_qualification_export_pin_changed")
    return loaded


def _reports(root: Path, request: V12ApprovalRequest) -> dict[str, bytes]:
    files = {}
    for gate in sorted(GATES):
        name = f"reports/{gate}.json"
        raw = read_bytes(root, name)
        report = decode_json(raw)
        if (
            receipt(raw) != request.gates[gate].report
            or report.get("qualification_id") != request.qualification_id
            or report.get("gate") != gate
            or report.get("status") != "passed"
        ):
            raise ValueError("v12_approval_report_binding")
        files[name] = raw
    return files


def approve_v12(
    qualification_dir: Path,
    root: Path,
    python: Path,
    *,
    actor: Principal,
    request: V12ApprovalRequest,
    reports_dir: Path,
    output_root: Path,
    verify_timeout_seconds: int = 3600,
) -> Path:
    """Authenticated explicit review records approval only, without changing any alias or head."""
    if "promoter" not in actor.roles or "model:decide" not in actor.capabilities:
        raise ValueError("v12_approval_promoter_required")
    request = V12ApprovalRequest.model_validate_json(request.model_dump_json())
    _private(qualification_dir, QUALIFICATION_FILES)
    qualification = _qualification(qualification_dir)
    if request.qualification_id != qualification.qualification_id:
        raise ValueError("v12_approval_wrong_qualification")
    loaded = _bound_export(qualification, root, python, verify_timeout_seconds)
    inputs = verify_inputs_from_capsule(qualification_dir)
    if source_policy(loaded, inputs) != qualification.source_policy:
        raise ValueError("v12_approval_source_policy_changed")
    inventory(reports_dir, {f"reports/{gate}.json" for gate in GATES})
    reports = _reports(reports_dir, request)
    raw = dict(
        version="forecast-v12-inference-release-1.0.0",
        purpose="qualified_forecast_v12",
        qualification=qualification.model_dump(mode="json"),
        approval=request.model_dump(mode="json"),
        reviewed_by=actor.principal_id,
        reviewed_at=_time(datetime.now(UTC)),
        serving_eligible=True,
        registered_in_mlflow=False,
        activated_as_champion=False,
    )
    raw["release_id"] = "v12-inference-release-sha256-" + canonical_sha256(raw)
    release = V12InferenceRelease.model_validate_json(canonical_bytes(raw))
    files = {name: read_bytes(qualification_dir, name) for name in QUALIFICATION_FILES}
    files.update(reports)
    files["release.json"] = _json(release.model_dump(mode="json"))
    return _publish(output_root, release.release_id, files, (root, qualification_dir, reports_dir))


def verify_inputs_from_capsule(root: Path) -> PreparedInputs:
    return PreparedInputs.model_validate_json(canonical_bytes(read_json(root, "inputs.json")))


def verify_approved_capsule(release_dir: Path, *, release_id: str) -> V12InferenceRelease:
    """Verify the private approval bytes; full export/wheel acceptance remains a separate step."""
    _private(
        release_dir, QUALIFICATION_FILES | {"release.json"} | {f"reports/{g}.json" for g in GATES}
    )
    release = V12InferenceRelease.model_validate_json(
        canonical_bytes(read_json(release_dir, "release.json"))
    )
    if release.release_id != release_id:
        raise ValueError("v12_inference_release_or_image_pin")
    qualification = _qualification(release_dir)
    if qualification != release.qualification:
        raise ValueError("v12_inference_qualification_pin")
    _reports(release_dir, release.approval)
    return release


def load_approved_v12(
    release_dir: Path,
    root: Path,
    python: Path,
    *,
    release_id: str,
    image_digest: str,
    verify_timeout_seconds: int = 3600,
) -> LoadedV12Inference:
    release = verify_approved_capsule(release_dir, release_id=release_id)
    if release.approval.image_digest != image_digest:
        raise ValueError("v12_inference_release_or_image_pin")
    qualification = release.qualification
    loaded = _bound_export(qualification, root, python, verify_timeout_seconds)
    if (
        source_policy(loaded, verify_inputs_from_capsule(release_dir))
        != qualification.source_policy
    ):
        raise ValueError("v12_inference_source_policy_changed")
    return LoadedV12Inference(loaded, release, image_digest)
