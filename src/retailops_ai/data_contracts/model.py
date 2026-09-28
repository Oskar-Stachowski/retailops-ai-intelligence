"""Immutable model metadata; aliases are not permitted as resolved versions."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    DataLineage,
    FeatureID,
    LabelID,
    ModelID,
    NonNegativeInt,
    Provenance,
    RunID,
    Sha256,
    SplitID,
    UtcTime,
    Versioned,
)
from retailops_ai.data_contracts.identity import canonical_sha256


class ModelRecord(Versioned):
    contract_type: Literal["model"]
    model_id: ModelID
    model_name: Literal["retailops-demand-forecast"]
    model_version: Annotated[str, Field(pattern=r"^[1-9][0-9]*$")]
    target_type: Literal["observed_sales_units"]
    feature_schema_version: Literal["1.0"]
    feature_allowlist_version: Literal["forecast-observed-sales-v1"]
    model_family: Literal["baseline", "random_forest", "hist_gradient_boosting"]
    training_run_id: RunID
    training_lineage: DataLineage
    feature_set_id: FeatureID
    label_dataset_id: LabelID
    split_id: SplitID
    training_cutoff: UtcTime
    selection_cutoff: UtcTime
    model_seed: NonNegativeInt
    artifact_sha256: Sha256
    config_sha256: Sha256
    provenance: Provenance

    @model_validator(mode="after")
    def immutable_model(self) -> Self:
        if self.provenance.source_owner or self.training_cutoff > self.selection_cutoff:
            raise ValueError("invalid_model_provenance_or_cutoffs")
        if self.model_id != "model-sha256-" + canonical_sha256(self.identity_payload()):
            raise ValueError("model_identity_mismatch")
        return self

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"model_id"})
