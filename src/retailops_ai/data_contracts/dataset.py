"""Logical manifest identity; artifact byte checks are the future importer's job."""

from datetime import date
from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Classification,
    Contract,
    DatasetID,
    FalseFlag,
    NonNegativeInt,
    Sha256,
    Symbol,
    UtcTime,
    Versioned,
)
from retailops_ai.data_contracts.identity import IdentityDescriptor


class ArtifactReference(Contract):
    path: Annotated[str, Field(min_length=1, max_length=512)]
    byte_sha256: Sha256
    size_bytes: NonNegativeInt
    rows: NonNegativeInt
    schema_version: Literal["1.0"]
    logical_table: Symbol
    classification: Classification
    grain: list[Symbol] = Field(min_length=1, max_length=10)
    date_field: Symbol
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def safe_reference(self) -> Self:
        path = PurePosixPath(self.path)
        if (
            path.is_absolute()
            or any(p in {"", ".", ".."} for p in self.path.split("/"))
            or "\\" in self.path
            or ":" in self.path
            or "\x00" in self.path
        ):
            raise ValueError("unsafe_artifact_path")
        if self.date_from > self.date_to or len(set(self.grain)) != len(self.grain):
            raise ValueError("invalid_artifact_range_or_grain")
        return self


class ReadinessFlags(Contract):
    forecast: Literal["passed", "failed", "not_ready", "not_evaluable"]
    inventory_ready: FalseFlag
    anomaly: Literal["not_ready"]
    stockout: Literal["not_ready"]
    policy_version: Symbol


class DatasetManifest(Versioned):
    contract_type: Literal["dataset"]
    dataset_id: DatasetID
    identity: IdentityDescriptor
    classification: Classification
    generated_at: UtcTime
    watermarks: dict[Symbol, UtcTime] = Field(min_length=1)
    artifacts: list[ArtifactReference] = Field(min_length=1)
    readiness: ReadinessFlags

    @model_validator(mode="after")
    def manifest_coherence(self) -> Self:
        if self.readiness.forecast == "passed" and sum(a.rows for a in self.artifacts) == 0:
            raise ValueError("empty_dataset_cannot_be_forecast_ready")
        if self.dataset_id != self.identity.content_id():
            raise ValueError("dataset_identity_mismatch")
        if self.classification != self.identity.classification:
            raise ValueError("identity_classification_mismatch")
        role = self.identity.role
        if role == "source":
            if self.classification not in {
                "facts",
                "simulation_truth",
                "raw_events",
                "source_operational_output",
            }:
                raise ValueError("source_classification_mismatch")
            if self.classification != "facts" and self.readiness.forecast == "passed":
                raise ValueError("nonfacts_cannot_be_forecast_ready")
        elif self.classification != role:
            raise ValueError("derived_classification_mismatch")
        if role in {"features", "labels", "predictions"}:
            full_grain = {
                "product_id",
                "selling_location_id",
                "channel",
                "forecast_origin",
                "target_date",
            }
            date_field = "forecast_origin" if role == "features" else "target_date"
            if any(
                not full_grain <= set(a.grain) or a.date_field != date_field for a in self.artifacts
            ):
                raise ValueError("forecast_artifact_grain_or_date_field_mismatch")
        if any(a.classification != self.classification for a in self.artifacts):
            raise ValueError("mixed_artifact_classifications")
        if len({a.path for a in self.artifacts}) != len(self.artifacts):
            raise ValueError("duplicate_artifact_path")
        if any(t > self.generated_at for t in self.watermarks.values()):
            raise ValueError("watermark_after_generation")
        return self
