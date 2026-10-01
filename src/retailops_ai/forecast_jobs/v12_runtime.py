"""Load an explicitly selected export recipe and run bounded, label-free offline inference."""

from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, overload

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecast_jobs.inputs import PreparedInputs
from retailops_ai.forecast_jobs.supervisor import ExecutionError, tree_rss
from retailops_ai.forecast_jobs.v12_contracts import (
    MAX_REQUEST_BYTES,
    MAX_RESULT_BYTES,
    MAX_ROWS,
    MAX_STDERR_BYTES,
    V12Execution,
    V12ExecutionLimits,
    V12RuntimePin,
    V12RuntimeResult,
)
from retailops_ai.forecast_jobs.v12_inference_contracts import V12InferenceResult
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.forecasting.manifest_contract import FeaturePolicy
from retailops_ai.model_lifecycle.v12_evidence import V12Evidence, load_evidence
from retailops_ai.model_lifecycle.v12_release_contracts import V12InferenceContext, V12SourcePolicy
from retailops_ai.source_snapshot.files import checked_directory, decode_json, file_hash, read_json


def prediction_key(
    pin: V12RuntimePin,
    row: InputRow,
    *,
    role: Literal["development_holdout", "inference"] = "development_holdout",
) -> str:
    return canonical_bytes(
        [
            pin.fold.name,
            role,
            row.forecast_origin.isoformat(),
            row.product_id,
            row.selling_location_id,
            row.channel,
            row.target_date.isoformat(),
        ]
    ).decode()


def validate_inputs(
    pin: V12RuntimePin, inputs: PreparedInputs, *, source_policy: V12SourcePolicy | None = None
) -> None:
    parent = inputs.feature_manifest.descriptor.parent
    if (
        len(inputs.rows) > MAX_ROWS
        or parent.source_dataset_id != pin.source_dataset_id
        or parent.snapshot_id != pin.snapshot_id
        or inputs.feature_manifest.descriptor.code.dependency_lock_sha256
        != pin.dependency_lock_sha256
        or inputs.feature_manifest.descriptor.resolved_policy != FeaturePolicy()
        or (
            source_policy is not None
            and (
                pin.forecast_model_status != "ready"
                or inputs.schema_version != "1.1"
                or inputs.source_freshness is None
                or inputs.feature_manifest.feature_set_id != source_policy.feature_set_id
                or parent.curated_descriptor_sha256 != source_policy.curated_descriptor_sha256
            )
        )
    ):
        raise ValueError("v12_runtime_input_parent_policy_or_budget")
    if (
        inputs.as_of_time <= pin.fold.selection_cutoff
        or inputs.as_of_time > datetime.now(UTC)
        or (
            source_policy is None
            and not pin.fold.development_holdout.start
            <= inputs.as_of_time.date()
            <= pin.fold.development_holdout.end
        )
    ):
        raise ValueError("v12_runtime_origin_outside_bound_holdout")


def _pin(evidence: V12Evidence, cohort_id: str, fold: str, recipe_id: str) -> V12RuntimePin:
    reference = evidence.card["recipes"][cohort_id][fold]["artifact"]
    name = reference["path"]
    receipt = evidence.files[name]
    if (
        reference["recipe_id"] != recipe_id
        or reference["sha256"] != receipt.sha256
        or reference["size_bytes"] != receipt.size_bytes
    ):
        raise ValueError("v12_runtime_recipe_pin_mismatch")
    recipe = read_json(evidence.root, name)
    if (
        recipe["recipe_id"] != recipe_id
        or recipe["recipe_id"]
        != "functional-v12-recipe-sha256-"
        + canonical_sha256({key: value for key, value in recipe.items() if key != "recipe_id"})
        or recipe["fold"] != fold
        or recipe["selection_cutoff"]
        != evidence.card["recipes"][cohort_id][fold]["selection_cutoff"]
        or recipe["policy"] != evidence.freeze["descriptor"]["method_policy"]
        or cohort_id not in recipe["support"]["cohort_ids"]
    ):
        raise ValueError("v12_runtime_recipe_binding")
    # This loader derives empirical inputs; a learned HGB point needs a separate pinned loader.
    if recipe["policy"]["mean_variant"] == "hgb_blend":
        raise ValueError("v12_runtime_hgb_input_not_supported")
    descriptor = evidence.manifest["descriptor"]
    lineage = evidence.card["cohort_lineage"][cohort_id]
    folds = evidence.freeze["descriptor"]["split_policy"]["folds"]
    selected = [value for value in folds if value["name"] == fold]
    if len(selected) != 1:
        raise ValueError("v12_runtime_fold_inventory")
    selected_fold = selected[0]
    return V12RuntimePin.model_validate_json(
        canonical_bytes(
            dict(
                run_id=evidence.run_id,
                campaign_id=descriptor["campaign_id"],
                freeze_id=descriptor["freeze_id"],
                replay_id=descriptor["replay_id"],
                cohort_id=cohort_id,
                fold=selected_fold,
                recipe_id=recipe_id,
                recipe_path=name,
                recipe=receipt.model_dump(mode="json"),
                manifest=evidence.manifest_receipt.model_dump(mode="json"),
                signature=evidence.files["signature.json"].model_dump(mode="json"),
                code_sha256=descriptor["code"]["code_sha256"],
                dependency_lock_sha256=descriptor["code"]["dependency_lock_sha256"],
                source_dataset_id=lineage["source_dataset_id"],
                snapshot_id=lineage["snapshot_id"],
                forecast_model_status=descriptor["forecast_model_status"],
            )
        )
    )


@dataclass(frozen=True)
class LoadedV12Forecast:
    root: Path
    python: Path
    pin: V12RuntimePin

    def predict(
        self, inputs: PreparedInputs, *, limits: V12ExecutionLimits | None = None
    ) -> V12RuntimeResult:
        request = V12Execution.model_validate_json(
            V12Execution(
                pin=self.pin, inputs=inputs, limits=limits or V12ExecutionLimits()
            ).model_dump_json()
        )
        validate_inputs(request.pin, request.inputs)
        return _execute(self, request)


def load_v12(
    root: Path,
    python: Path,
    *,
    run_id: str,
    cohort_id: str,
    fold: str,
    recipe_id: str,
    verify_timeout_seconds: int = 3600,
) -> LoadedV12Forecast:
    """Full archive verification precedes selection; no automatic cohort/fold/model choice."""
    evidence = load_evidence(root, python, timeout_seconds=verify_timeout_seconds)
    if evidence.run_id != run_id:
        raise ValueError("v12_runtime_run_pin_mismatch")
    pin = _pin(evidence, cohort_id, fold, recipe_id)
    evidence.verify_bytes()
    return LoadedV12Forecast(evidence.root, python, pin)


@overload
def _execute(
    loaded: LoadedV12Forecast,
    request: V12Execution,
    *,
    inference: None = None,
    tick: Callable[[], None] | None = None,
) -> V12RuntimeResult: ...


@overload
def _execute(
    loaded: LoadedV12Forecast,
    request: V12Execution,
    *,
    inference: V12InferenceContext,
    tick: Callable[[], None] | None = None,
) -> V12InferenceResult: ...


def _execute(
    loaded: LoadedV12Forecast,
    request: V12Execution,
    *,
    inference: V12InferenceContext | None = None,
    tick: Callable[[], None] | None = None,
) -> V12RuntimeResult | V12InferenceResult:
    root = checked_directory(loaded.root)
    if not loaded.python.is_absolute() or not loaded.python.is_file():
        raise ExecutionError("v12_runtime_interpreter_required")
    for name, receipt in (
        ("run_manifest.json", request.pin.manifest),
        ("signature.json", request.pin.signature),
        (request.pin.recipe_path, request.pin.recipe),
    ):
        if file_hash(root, name) != (receipt.size_bytes, receipt.sha256):
            raise ExecutionError("v12_runtime_loaded_artifact_changed")
    document = {"root": str(root), **request.model_dump(mode="json")}
    if inference is not None:
        document["inference"] = inference.model_dump(mode="json")
    payload = canonical_bytes(document)
    if len(payload) > MAX_REQUEST_BYTES:
        raise ExecutionError("v12_runtime_request_limit")
    executor = Path(__file__).with_name("v12_executor.py")
    env = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1"}
    with tempfile.TemporaryDirectory(prefix="retailops-v12-executor-") as temporary:
        work = Path(temporary)
        for name, raw in (("request.json", payload), ("stdout", b""), ("stderr", b"")):
            (work / name).write_bytes(raw)
            (work / name).chmod(0o600)
        env["TMPDIR"] = str(work)
        with (
            (work / "request.json").open("rb") as stdin,
            (work / "stdout").open("wb") as stdout,
            (work / "stderr").open("wb") as stderr,
        ):
            started = time.monotonic()
            child = subprocess.Popen(  # noqa: S603 - operator interpreter and fixed trusted script
                [str(loaded.python), "-I", "-B", str(executor)],
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                cwd=work,
                env=env,
                start_new_session=True,
            )
            try:
                while child.poll() is None:
                    if tick is not None:
                        tick()
                    if time.monotonic() - started > request.limits.wall_seconds:
                        raise ExecutionError("v12_runtime_wall_limit")
                    if tree_rss(child.pid) > request.limits.rss_bytes:
                        raise ExecutionError("v12_runtime_memory_limit")
                    if (work / "stdout").stat().st_size > MAX_RESULT_BYTES or (
                        work / "stderr"
                    ).stat().st_size > MAX_STDERR_BYTES:
                        raise ExecutionError("v12_runtime_output_limit")
                    try:
                        child.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        continue
                if time.monotonic() - started > request.limits.wall_seconds:
                    raise ExecutionError("v12_runtime_wall_limit")
                if child.returncode:
                    raise ExecutionError("v12_runtime_child_failed")
                if (work / "stdout").stat().st_size > MAX_RESULT_BYTES or (
                    work / "stderr"
                ).stat().st_size > MAX_STDERR_BYTES:
                    raise ExecutionError("v12_runtime_output_limit")
                raw = canonical_bytes(decode_json((work / "stdout").read_bytes()))
                result: V12RuntimeResult | V12InferenceResult
                if inference is None:
                    result = V12RuntimeResult.model_validate_json(raw)
                else:
                    result = V12InferenceResult.model_validate_json(raw)
                    if result.inference != inference:
                        raise ExecutionError("v12_inference_result_approval_binding")
                role: Literal["development_holdout", "inference"] = (
                    "inference" if inference is not None else "development_holdout"
                )
                if (
                    result.pin != request.pin
                    or result.profile_id != request.inputs.profile_id
                    or [p.key for p in result.predictions]
                    != [prediction_key(request.pin, row, role=role) for row in request.inputs.rows]
                    or result.peak_rss_bytes > request.limits.rss_bytes
                ):
                    raise ExecutionError("v12_runtime_result_pin_or_count")
                if tick is not None:
                    tick()
                for name, receipt in (
                    ("run_manifest.json", request.pin.manifest),
                    ("signature.json", request.pin.signature),
                    (request.pin.recipe_path, request.pin.recipe),
                ):
                    if file_hash(root, name) != (receipt.size_bytes, receipt.sha256):
                        raise ExecutionError("v12_runtime_loaded_artifact_changed")
                return result
            finally:
                if child.poll() is None:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                child.wait(timeout=5)
