"""Complete, bounded v12 computation receipts; publication and read API are separate."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    FalseFlag,
    RunID,
    Sha256,
    Symbol,
    TrueFlag,
    UtcTime,
)
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.data_contracts.run import RunError
from retailops_ai.forecast_jobs.contracts import BatchInput, BatchScope, ProfileID, QueuePolicy
from retailops_ai.forecast_jobs.inputs import (
    InputContent,
    PreparedInputs,
    prepared,
    scoped_inputs,
    series,
)
from retailops_ai.forecast_jobs.v12_contracts import MAX_REQUEST_BYTES, MAX_ROWS, V12Execution, Zero
from retailops_ai.forecast_jobs.v12_inference_contracts import V12InferenceResult
from retailops_ai.forecast_jobs.v12_runtime import prediction_key, validate_inputs
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import (
    TEST_MODEL,
    DatabaseReleaseID,
    V12Binding,
    V12ModelRelease,
)
from retailops_ai.model_lifecycle.v12_release_contracts import ImageDigest

MAX_RECEIPT_BYTES = 8 * 1024**2
ReceiptID = Annotated[str, Field(pattern=r"^v12-computation-sha256-[0-9a-f]{64}$")]


class V12BatchRun(Contract):
    version: Literal["forecast-v12-batch-run-1.0.0"] = "forecast-v12-batch-run-1.0.0"
    purpose: Literal["qualified_v12_computation"] = "qualified_v12_computation"
    run_id: RunID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    attempt: Annotated[int, Field(ge=1, le=5)]
    requested_at: UtcTime
    requested_by: Symbol
    started_at: UtcTime | None = None
    completed_at: UtcTime | None = None
    input_ref: BatchInput
    resolved_model: V12Binding
    release_id: DatabaseReleaseID
    image_digest: ImageDigest
    environment: Literal["local", "test"]
    policy: QueuePolicy
    output_id: ReceiptID | None = None
    error: RunError | None = None

    @model_validator(mode="after")
    def coherence(self) -> Self:
        if (
            self.resolved_model.model_name == TEST_MODEL and self.environment != "test"
        ) or self.image_digest != self.resolved_model.approval.approval.image_digest:
            raise ValueError("v12_batch_model_environment_or_image")
        if self.input_ref.as_of_time > self.requested_at or self.attempt > self.policy.max_attempts:
            raise ValueError("v12_batch_origin_or_attempt")
        if self.started_at is not None and self.started_at < self.requested_at:
            raise ValueError("v12_batch_start_time")
        if self.completed_at is not None and self.completed_at < (
            self.started_at or self.requested_at
        ):
            raise ValueError("v12_batch_completion_time")
        if self.status == "queued":
            valid = all(
                v is None for v in (self.started_at, self.completed_at, self.output_id, self.error)
            )
        elif self.status == "running":
            valid = self.started_at is not None and all(
                v is None for v in (self.completed_at, self.output_id, self.error)
            )
        elif self.status == "succeeded":
            valid = (
                self.started_at is not None
                and self.completed_at is not None
                and self.output_id is not None
                and self.error is None
            )
        else:
            valid = (
                self.completed_at is not None
                and self.output_id is None
                and self.error is not None
                and (self.status != "failed" or self.started_at is not None)
            )
            valid = valid and (self.status == "cancelled") == (
                self.error is not None and self.error.code == "cancelled"
            )
        if not valid:
            raise ValueError("v12_batch_state")
        return self


def chunks(
    profile: PreparedInputs,
    scope: BatchScope,
    horizon: Literal[7, 14],
    release: V12ModelRelease,
    *,
    tick: Callable[[], None] | None = None,
) -> tuple[PreparedInputs, ...]:
    """Split rectangles without splitting a series' history or inventing incomplete profiles."""
    selected = scoped_inputs(profile, scope, horizon)
    if tick is not None:
        tick()
    pin = release.binding.approval.qualification.pin
    policy = release.binding.approval.qualification.source_policy
    result = []
    for location in scope.selling_location_ids:
        pending = list(scope.product_ids)
        while pending:
            count = min(len(pending), MAX_ROWS // horizon)
            while count:
                if tick is not None:
                    tick()
                part_scope = BatchScope(
                    product_ids=tuple(pending[:count]),
                    selling_location_ids=(location,),
                    channel=scope.channel,
                )
                keys = {(p, location, scope.channel) for p in pending[:count]}
                # The complete parent was validated above; validate each derived rectangle once.
                part = prepared(
                    InputContent(
                        schema_version=selected.schema_version,
                        source_freshness=selected.source_freshness.scoped(part_scope)
                        if selected.source_freshness
                        else None,
                        feature_manifest=selected.feature_manifest,
                        as_of_time=selected.as_of_time,
                        scope=part_scope,
                        horizon_days=horizon,
                        rows=tuple(r for r in selected.rows if series(r) in keys),
                        histories=tuple(h for h in selected.histories if series(h) in keys),
                    )
                )
                validate_inputs(pin, part, source_policy=policy)
                # Reserve room for the fixed local path and inference context in the executor envelope.
                document = {
                    "root": "x" * 4096,
                    "inference": release.binding.approval.context().model_dump(mode="json"),
                    **V12Execution(
                        pin=pin, inputs=part, limits=release.binding.approval.qualification.limits
                    ).model_dump(mode="json"),
                }
                if len(canonical_bytes(document)) <= MAX_REQUEST_BYTES:
                    break
                count -= 1
            if not count:
                raise ValueError("v12_batch_single_series_byte_limit")
            result.append(part)
            del pending[:count]
    return tuple(result)


class V12BatchReceipt(Contract):
    version: Literal["forecast-v12-batch-receipt-1.0.0"] = "forecast-v12-batch-receipt-1.0.0"
    purpose: Literal["qualified_v12_computation"] = "qualified_v12_computation"
    artifact_id: ReceiptID
    complete: TrueFlag = True
    run_id: RunID
    release: V12ModelRelease
    profile_id: ProfileID
    scope: BatchScope
    horizon_days: Literal[7, 14]
    parts: tuple[V12InferenceResult, ...] = Field(min_length=1, max_length=100)
    predictions_sha256: Sha256
    generated_at: UtcTime
    model_refits: Zero = 0
    source_generation: FalseFlag = False
    published_forecast_outputs: Zero = 0

    @model_validator(mode="after")
    def integrity(self) -> Self:
        approval = self.release.binding.approval
        predictions = [p for part in self.parts for p in part.predictions]
        keys = [p.key for p in predictions]
        if (
            len(predictions) > 1400
            or len(keys) != len(set(keys))
            or any(
                part.pin != approval.qualification.pin or part.inference != approval.context()
                for part in self.parts
            )
        ):
            raise ValueError("v12_batch_receipt_duplicate_or_binding")
        if self.predictions_sha256 != canonical_sha256(
            [p.model_dump(mode="json") for p in sorted(predictions, key=lambda p: p.key)]
        ):
            raise ValueError("v12_batch_receipt_predictions_hash")
        if self.artifact_id != "v12-computation-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"artifact_id"})
        ):
            raise ValueError("v12_batch_receipt_identity")
        if len(canonical_bytes(self.model_dump(mode="json"))) > MAX_RECEIPT_BYTES:
            raise ValueError("v12_batch_receipt_byte_limit")
        return self


def verify_receipt(
    run: V12BatchRun,
    profile: PreparedInputs,
    release: V12ModelRelease,
    output: V12BatchReceipt,
    *,
    tick: Callable[[], None] | None = None,
) -> None:
    output = V12BatchReceipt.model_validate_json(output.model_dump_json())
    if (
        profile.profile_id != run.input_ref.profile_id
        or release.release_id != run.release_id
        or release.binding != run.resolved_model
        or release.image_digest != run.image_digest
    ):
        raise ValueError("v12_batch_receipt_immutable_run_pin")
    expected_parts = chunks(
        profile, run.input_ref.scope, max(run.input_ref.request.horizons_days), release, tick=tick
    )
    if (
        output.run_id != run.run_id
        or output.profile_id != profile.profile_id
        or output.release != release
        or output.scope != run.input_ref.scope
        or output.horizon_days != max(run.input_ref.request.horizons_days)
        or len(output.parts) != len(expected_parts)
    ):
        raise ValueError("v12_batch_receipt_run_or_partition")
    for part, inputs in zip(output.parts, expected_parts, strict=True):
        if tick is not None:
            tick()
        if (
            part.profile_id != inputs.profile_id
            or [p.key for p in part.predictions]
            != [prediction_key(part.pin, row, role="inference") for row in inputs.rows]
            or part.peak_rss_bytes > release.binding.approval.qualification.limits.rss_bytes
        ):
            raise ValueError("v12_batch_receipt_coverage_or_resource")
        if not run.started_at or not run.started_at <= part.generated_at <= output.generated_at:
            raise ValueError("v12_batch_receipt_generation_time")


def computation_receipt(
    run: V12BatchRun,
    profile: PreparedInputs,
    release: V12ModelRelease,
    results: tuple[V12InferenceResult, ...],
    *,
    tick: Callable[[], None] | None = None,
) -> V12BatchReceipt:
    predictions = sorted((p for part in results for p in part.predictions), key=lambda p: p.key)
    raw = dict(
        version="forecast-v12-batch-receipt-1.0.0",
        purpose="qualified_v12_computation",
        complete=True,
        run_id=run.run_id,
        release=release.model_dump(mode="json"),
        profile_id=profile.profile_id,
        scope=run.input_ref.scope.model_dump(mode="json"),
        horizon_days=max(run.input_ref.request.horizons_days),
        parts=[part.model_dump(mode="json") for part in results],
        predictions_sha256=canonical_sha256([p.model_dump(mode="json") for p in predictions]),
        generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        model_refits=0,
        source_generation=False,
        published_forecast_outputs=0,
    )
    raw["artifact_id"] = "v12-computation-sha256-" + canonical_sha256(raw)
    output = V12BatchReceipt.model_validate_json(canonical_bytes(raw))
    verify_receipt(run, profile, release, output, tick=tick)
    return output
