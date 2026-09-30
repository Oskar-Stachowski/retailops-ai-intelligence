"""Bounded output partitions and a complete, pinned, content-addressed publication manifest."""

import json
from typing import Annotated, Any, Literal, Self

from pydantic import (
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from retailops_ai.data_contracts.common import (
    Contract,
    CuratedID,
    FeatureID,
    PredictionDatasetID,
    RunID,
    Sha256,
    SourceID,
    TrueFlag,
    UtcTime,
)
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecast_jobs.contracts import (
    BatchRun,
    BatchScope,
    MechanicsPrediction,
    ProfileID,
    ReleaseID,
)
from retailops_ai.forecast_jobs.execution_contracts import RuntimeResult
from retailops_ai.forecast_jobs.inputs import PreparedInputs, scoped_inputs
from retailops_ai.forecast_jobs.source_freshness import SourceFreshness, versioned_schema
from retailops_ai.model_lifecycle.contracts import MODEL, Binding

PARTITION_ROWS = 256
MAX_PARTITION_BYTES = 256 * 1024


def grain(row: MechanicsPrediction) -> tuple[str, str, str, int]:
    return row.product_id, row.selling_location_id, row.channel, row.horizon_days


def scope_key(scope: BatchScope, horizon: int) -> str:
    return canonical_sha256({"scope": scope.model_dump(mode="json"), "horizon_days": horizon})


class Partition(Contract):
    ordinal: Annotated[int, Field(ge=0, le=5)]
    predictions: tuple[MechanicsPrediction, ...] = Field(min_length=1, max_length=PARTITION_ROWS)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        keys = [grain(r) for r in self.predictions]
        if keys != sorted(set(keys)) or len(self.model_dump_json().encode()) > MAX_PARTITION_BYTES:
            raise ValueError("forecast_partition_grain_or_byte_limit")
        return self


class PartitionReceipt(Contract):
    ordinal: Annotated[int, Field(ge=0, le=5)]
    rows: Annotated[int, Field(ge=1, le=PARTITION_ROWS)]
    sha256: Sha256


def receipt(partition: Partition) -> PartitionReceipt:
    return PartitionReceipt(
        ordinal=partition.ordinal,
        rows=len(partition.predictions),
        sha256=canonical_sha256(partition.model_dump(mode="json")),
    )


class OutputManifest(Contract):
    model_config = ConfigDict(json_schema_extra=versioned_schema)
    schema_version: Literal["1.0", "1.1"] = "1.0"
    source_freshness: SourceFreshness | None = None
    artifact_id: PredictionDatasetID
    kind: Literal["predictions"] = "predictions"
    complete: TrueFlag = True
    purpose: Literal["qualified_forecast"] = "qualified_forecast"
    forecast_quality_approved: TrueFlag = True
    run_id: RunID
    release_id: ReleaseID
    profile_id: ProfileID
    execution_profile_id: ProfileID
    source_dataset_id: SourceID
    curated_dataset_id: CuratedID
    feature_set_id: FeatureID
    resolved_model: Binding
    image_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    as_of_time: UtcTime
    generated_at: UtcTime
    scope: BatchScope
    horizon_days: Literal[7, 14]
    row_count: Annotated[int, Field(ge=1, le=1400)]
    predictions_sha256: Sha256
    partitions: tuple[PartitionReceipt, ...] = Field(min_length=1, max_length=6)
    cold_load_seconds: Annotated[float, Field(ge=0)]
    compute_seconds: Annotated[float, Field(ge=0)]
    peak_rss_bytes: Annotated[int, Field(ge=1, le=1024 * 1024**2)]

    @model_serializer(mode="wrap")
    def versioned_content(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        raw: dict[str, Any] = handler(self)
        if self.schema_version == "1.0":
            raw.pop("source_freshness", None)
        return raw

    @model_validator(mode="after")
    def integrity(self) -> Self:
        if (self.schema_version == "1.1") != (self.source_freshness is not None):
            raise ValueError("forecast_manifest_freshness_version_mismatch")
        if self.source_freshness is not None:
            self.source_freshness.verify_ids(self.source_dataset_id, self.curated_dataset_id)
            if (
                self.source_freshness.as_of_time != self.as_of_time
                or self.source_freshness.scoped(self.scope) != self.source_freshness
                or (
                    self.source_freshness.watermark is not None
                    and self.source_freshness.watermark.as_of_time > self.generated_at
                )
            ):
                raise ValueError("forecast_manifest_freshness_binding_mismatch")
        binding = self.resolved_model
        if (
            binding.model_name != MODEL
            or binding.qualification.purpose != self.purpose
            or any(g.status != "passed" for g in binding.qualification.gates.values())
            or self.generated_at < self.as_of_time
            or self.row_count
            != len(self.scope.product_ids)
            * len(self.scope.selling_location_ids)
            * self.horizon_days
            or [r.ordinal for r in self.partitions] != list(range(len(self.partitions)))
            or sum(r.rows for r in self.partitions) != self.row_count
        ):
            raise ValueError("forecast_manifest_pin_or_coverage_mismatch")
        if self.artifact_id != "predictions-sha256-" + canonical_sha256(
            self.model_dump(mode="json", exclude={"artifact_id"})
        ):
            raise ValueError("forecast_manifest_identity_mismatch")
        return self


class Publication(Contract):
    manifest: OutputManifest
    partitions: tuple[Partition, ...] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def complete(self) -> Self:
        m = self.manifest
        rows = tuple(r for p in self.partitions for r in p.predictions)
        expected = sorted(
            (p, loc, m.scope.channel, h)
            for p in m.scope.product_ids
            for loc in m.scope.selling_location_ids
            for h in range(1, m.horizon_days + 1)
        )
        if (
            tuple(receipt(p) for p in self.partitions) != m.partitions
            or [grain(r) for r in rows] != expected
            or any(r.forecast_origin != m.as_of_time for r in rows)
            or canonical_sha256([r.model_dump(mode="json") for r in rows]) != m.predictions_sha256
            or len(canonical_bytes(m.model_dump(mode="json"))) > 256 * 1024
        ):
            raise ValueError("forecast_publication_incomplete_or_changed")
        return self


def publication(
    run: BatchRun, profile: PreparedInputs, result: RuntimeResult, now: UtcTime
) -> Publication:
    """Only a queue lease owner may persist this in the same transaction as run success."""
    run = BatchRun.model_validate_json(run.model_dump_json())
    result = RuntimeResult.model_validate_json(result.model_dump_json())
    inputs = scoped_inputs(profile, run.input_ref.scope, max(run.input_ref.request.horizons_days))
    parent = profile.feature_manifest.descriptor.parent
    if (
        run.purpose != "qualified_forecast"
        or run.status != "running"
        or result.purpose != "qualified_forecast_computation"
        or result.profile_id != inputs.profile_id
        or result.release_id != run.release_id
        or profile.profile_id != run.input_ref.profile_id
        or (
            run.input_ref.source_dataset_id,
            run.input_ref.curated_dataset_id,
            run.input_ref.feature_set_id,
            run.input_ref.as_of_time,
        )
        != (
            parent.source_dataset_id,
            parent.curated_dataset_id,
            profile.feature_manifest.feature_set_id,
            profile.as_of_time,
        )
        or len(result.quantities) != len(inputs.rows)
    ):
        raise ValueError("forecast_publication_result_binding_mismatch")
    predictions = tuple(
        MechanicsPrediction.model_validate_json(
            json.dumps(
                dict(
                    row.model_dump(
                        mode="json",
                        include={
                            "product_id",
                            "selling_location_id",
                            "channel",
                            "forecast_origin",
                            "business_timezone",
                            "cutoff_policy",
                            "target_date",
                            "horizon_days",
                        },
                    ),
                    predicted_units=value,
                )
            )
        )
        for row, value in zip(inputs.rows, result.quantities, strict=True)
    )
    partitions = tuple(
        Partition(ordinal=i // PARTITION_ROWS, predictions=predictions[i : i + PARTITION_ROWS])
        for i in range(0, len(predictions), PARTITION_ROWS)
    )
    raw = dict(
        schema_version=profile.schema_version,
        kind="predictions",
        complete=True,
        purpose="qualified_forecast",
        forecast_quality_approved=True,
        run_id=run.run_id,
        release_id=run.release_id,
        profile_id=profile.profile_id,
        execution_profile_id=inputs.profile_id,
        source_dataset_id=run.input_ref.source_dataset_id,
        curated_dataset_id=run.input_ref.curated_dataset_id,
        feature_set_id=run.input_ref.feature_set_id,
        resolved_model=run.resolved_model.model_dump(mode="json"),
        image_digest=run.image_digest,
        as_of_time=run.input_ref.as_of_time.isoformat().replace("+00:00", "Z"),
        generated_at=now.isoformat().replace("+00:00", "Z"),
        scope=run.input_ref.scope.model_dump(mode="json"),
        horizon_days=inputs.horizon_days,
        row_count=len(predictions),
        predictions_sha256=canonical_sha256([r.model_dump(mode="json") for r in predictions]),
        partitions=[receipt(p).model_dump(mode="json") for p in partitions],
        cold_load_seconds=result.cold_load_seconds,
        compute_seconds=result.compute_seconds,
        peak_rss_bytes=result.peak_rss_bytes,
    )
    if inputs.source_freshness is not None:
        raw["source_freshness"] = inputs.source_freshness.model_dump(mode="json")
    manifest = OutputManifest.model_validate_json(
        json.dumps(dict(raw, artifact_id="predictions-sha256-" + canonical_sha256(raw)))
    )
    return Publication(manifest=manifest, partitions=partitions)
