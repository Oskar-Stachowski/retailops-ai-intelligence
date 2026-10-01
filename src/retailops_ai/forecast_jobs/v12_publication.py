"""Immutable complete v12 publication, preserving every functional and its exact reference."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    CuratedID,
    FeatureID,
    ForecastKey,
    RunID,
    Sha256,
    SourceID,
    TrueFlag,
    UtcTime,
)
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecast_jobs.contracts import BatchScope, ProfileID
from retailops_ai.forecast_jobs.inputs import PreparedInputs
from retailops_ai.forecast_jobs.source_freshness import SourceFreshness
from retailops_ai.forecast_jobs.v12_batch import (
    ReceiptID,
    V12BatchReceipt,
    V12BatchRun,
    verify_receipt,
)
from retailops_ai.forecast_jobs.v12_contracts import V12Prediction
from retailops_ai.forecast_jobs.v12_runtime import prediction_key
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import (
    TEST_MODEL,
    DatabaseReleaseID,
    V12Binding,
)
from retailops_ai.model_lifecycle.v12_release_contracts import ImageDigest

OutputID = Annotated[str, Field(pattern=r"^v12-forecasts-sha256-[0-9a-f]{64}$")]
MAX_OUTPUT_BYTES = 8 * 1024**2


def grain(row: ForecastKey) -> tuple[str, str, str, int]:
    return row.product_id, row.selling_location_id, row.channel, row.horizon_days


class V12PublishedRow(ForecastKey):
    prediction: V12Prediction
    execution_profile_id: ProfileID


class V12Publication(Contract):
    version: Literal["forecast-v12-publication-1.0.0"] = "forecast-v12-publication-1.0.0"
    purpose: Literal["qualified_v12_forecast"] = "qualified_v12_forecast"
    complete: TrueFlag = True
    artifact_id: OutputID
    environment: Literal["local", "test"]
    run_id: RunID
    receipt_id: ReceiptID
    release_id: DatabaseReleaseID
    resolved_model: V12Binding
    image_digest: ImageDigest
    profile_id: ProfileID
    source_dataset_id: SourceID
    curated_dataset_id: CuratedID
    feature_set_id: FeatureID
    source_freshness: SourceFreshness
    as_of_time: UtcTime
    generated_at: UtcTime
    scope: BatchScope
    horizon_days: Literal[7, 14]
    predictions_sha256: Sha256
    rows: tuple[V12PublishedRow, ...] = Field(min_length=1, max_length=1400)

    @model_validator(mode="after")
    def integrity(self) -> Self:
        expected = sorted(
            (p, loc, self.scope.channel, h)
            for p in self.scope.product_ids
            for loc in self.scope.selling_location_ids
            for h in range(1, self.horizon_days + 1)
        )
        approval = self.resolved_model.approval
        if (
            [grain(r) for r in self.rows] != expected
            or any(r.forecast_origin != self.as_of_time for r in self.rows)
            or any(
                r.prediction.key != prediction_key(approval.qualification.pin, r, role="inference")
                for r in self.rows
            )
            or any(
                r.prediction.metadata.recipe_id != approval.qualification.pin.recipe_id
                for r in self.rows
            )
            or self.generated_at < self.as_of_time
            or self.image_digest != approval.approval.image_digest
            or (self.resolved_model.model_name == TEST_MODEL and self.environment != "test")
            or self.source_freshness.as_of_time != self.as_of_time
            or self.source_freshness.scoped(self.scope) != self.source_freshness
            or (
                self.source_freshness.watermark is not None
                and self.source_freshness.watermark.as_of_time > self.generated_at
            )
        ):
            raise ValueError("v12_publication_grain_or_binding")
        self.source_freshness.verify_ids(self.source_dataset_id, self.curated_dataset_id)
        if self.predictions_sha256 != canonical_sha256(
            [
                r.prediction.model_dump(mode="json")
                for r in sorted(self.rows, key=lambda r: r.prediction.key)
            ]
        ):
            raise ValueError("v12_publication_predictions_hash")
        if self.artifact_id != "v12-forecasts-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"artifact_id"})
        ):
            raise ValueError("v12_publication_identity")
        if len(canonical_bytes(self.model_dump(mode="json"))) > MAX_OUTPUT_BYTES:
            raise ValueError("v12_publication_byte_limit")
        return self


def verify_publication(output: V12Publication, run: V12BatchRun, receipt: V12BatchReceipt) -> None:
    output = V12Publication.model_validate_json(output.model_dump_json())
    run = V12BatchRun.model_validate_json(run.model_dump_json())
    receipt = V12BatchReceipt.model_validate_json(receipt.model_dump_json())
    i = run.input_ref
    if (
        run.status != "succeeded"
        or run.output_id != receipt.artifact_id
        or receipt.run_id != run.run_id
        or receipt.profile_id != i.profile_id
        or receipt.release.release_id != run.release_id
        or receipt.release.binding != run.resolved_model
        or receipt.release.image_digest != run.image_digest
        or receipt.scope != i.scope
        or receipt.horizon_days != max(i.request.horizons_days)
        or (
            output.run_id,
            output.receipt_id,
            output.release_id,
            output.resolved_model,
            output.image_digest,
            output.environment,
        )
        != (
            run.run_id,
            run.output_id,
            run.release_id,
            run.resolved_model,
            run.image_digest,
            run.environment,
        )
        or (
            output.profile_id,
            output.as_of_time,
            output.scope,
            output.horizon_days,
            output.source_dataset_id,
            output.curated_dataset_id,
            output.feature_set_id,
        )
        != (
            i.profile_id,
            i.as_of_time,
            i.scope,
            max(i.request.horizons_days),
            i.source_dataset_id,
            i.curated_dataset_id,
            i.feature_set_id,
        )
        or output.predictions_sha256 != receipt.predictions_sha256
        or not run.started_at
        or not run.completed_at
        or not run.started_at <= receipt.generated_at <= run.completed_at <= output.generated_at
        or not run.resolved_model.approval.reviewed_at
        <= output.generated_at
        < run.resolved_model.approval.qualification.valid_until
    ):
        raise ValueError("v12_publication_run_receipt_pin")
    original = {p.key: (p, part.profile_id) for part in receipt.parts for p in part.predictions}
    if len(original) != len(output.rows) or any(
        original.get(r.prediction.key) != (r.prediction, r.execution_profile_id)
        for r in output.rows
    ):
        raise ValueError("v12_publication_functionals_changed")


def publication(
    run: V12BatchRun, profile: PreparedInputs, receipt: V12BatchReceipt, now: UtcTime
) -> V12Publication:
    verify_receipt(run, profile, receipt.release, receipt)
    original = {p.key: (p, part.profile_id) for part in receipt.parts for p in part.predictions}
    rows = []
    for row in profile.rows:
        if (
            row.product_id not in run.input_ref.scope.product_ids
            or row.selling_location_id not in run.input_ref.scope.selling_location_ids
            or row.horizon_days > receipt.horizon_days
        ):
            continue
        prediction, part_id = original[
            prediction_key(
                receipt.release.binding.approval.qualification.pin, row, role="inference"
            )
        ]
        rows.append(
            V12PublishedRow(
                **row.model_dump(include=set(ForecastKey.model_fields)),
                prediction=prediction,
                execution_profile_id=part_id,
            )
        )
    if profile.source_freshness is None:
        raise ValueError("v12_publication_verified_source_required")
    raw = dict(
        version="forecast-v12-publication-1.0.0",
        purpose="qualified_v12_forecast",
        complete=True,
        environment=run.environment,
        run_id=run.run_id,
        receipt_id=receipt.artifact_id,
        release_id=run.release_id,
        resolved_model=run.resolved_model.model_dump(mode="json"),
        image_digest=run.image_digest,
        profile_id=profile.profile_id,
        source_dataset_id=run.input_ref.source_dataset_id,
        curated_dataset_id=run.input_ref.curated_dataset_id,
        feature_set_id=run.input_ref.feature_set_id,
        source_freshness=profile.source_freshness.scoped(run.input_ref.scope).model_dump(
            mode="json"
        ),
        as_of_time=run.input_ref.as_of_time.isoformat().replace("+00:00", "Z"),
        generated_at=now.isoformat().replace("+00:00", "Z"),
        scope=run.input_ref.scope.model_dump(mode="json"),
        horizon_days=receipt.horizon_days,
        predictions_sha256=receipt.predictions_sha256,
        rows=[r.model_dump(mode="json") for r in sorted(rows, key=grain)],
    )
    raw["artifact_id"] = "v12-forecasts-sha256-" + canonical_sha256(raw)
    output = V12Publication.model_validate_json(canonical_bytes(raw))
    verify_publication(output, run, receipt)
    return output
