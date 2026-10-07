"""A separate complete-population fit wire; legacy resource ceilings stay frozen."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, Symbol
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.forecasting.features_contract import FEATURE_TYPES
from retailops_ai.forecasting.model_contract import HGBConfig, RFConfig

Family = Literal["rf", "hgb", "tensorflow"]


class CampaignForecastFitPlan(Contract):
    version: Literal["ai09-campaign-forecast-fit-1.0.0"] = "ai09-campaign-forecast-fit-1.0.0"
    source_recipe_sha256: Sha256
    export_operation_id: Symbol
    family: Family
    initialization_seed: Annotated[int, Field(ge=0, le=2**31 - 1)]
    worker_environment_lock_sha256: Sha256
    rf: RFConfig = RFConfig(max_depth=10, min_samples_leaf=4)
    hgb: HGBConfig = HGBConfig()
    hidden_units: tuple[Literal[32], Literal[16]] = (32, 16)
    epochs: Annotated[int, Field(ge=1, le=25)] = 25
    batch_size: Annotated[int, Field(ge=1, le=256)] = 64
    patience: Annotated[int, Field(ge=1, le=5)] = 4
    learning_rate: Annotated[float, Field(gt=0, le=0.01)] = 0.001
    max_train_rows: Annotated[int, Field(ge=1, le=2000000)] = 2000000
    max_windows: Annotated[int, Field(ge=1, le=200000)] = 200000
    max_matrix_bytes: Annotated[int, Field(ge=1024, le=4 * 1024**3)] = 1024**3
    max_index_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)] = 2 * 1024**3
    max_artifact_bytes: Annotated[int, Field(ge=1024, le=512 * 1024**2)] = 128 * 1024**2
    resources: CampaignGenerationResources
    preprocessing: Literal["train_median_imputation_missing_unk_one_hot_and_train_scaling"] = (
        "train_median_imputation_missing_unk_one_hot_and_train_scaling"
    )
    early_stopping_role: Literal["early_stopping"] = "early_stopping"
    training_population: Literal["all_eligible_train_keys_no_sampling"] = (
        "all_eligible_train_keys_no_sampling"
    )
    tensorflow_representation: Literal[
        "history28_and_all14_asof_covariates_masked_direct_mean_median"
    ] = "history28_and_all14_asof_covariates_masked_direct_mean_median"
    final_test_accessed: FalseFlag = False
    quality_qualified: FalseFlag = False
    stage_ready: FalseFlag = False

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)


class CampaignNumericEncoding(Contract):
    name: str
    fill: float
    known_count: Annotated[int, Field(ge=0)]
    center: float
    spread: Annotated[float, Field(gt=0)]


class CampaignCategoricalEncoding(Contract):
    name: str
    categories: tuple[str, ...]

    @model_validator(mode="after")
    def unique(self) -> Self:
        if self.categories != tuple(sorted(set(self.categories))):
            raise ValueError("campaign_encoder_sorted_unique_categories_required")
        return self


class CampaignForecastEncoding(Contract):
    version: Literal["ai09-campaign-train-encoding-1.0.0"] = "ai09-campaign-train-encoding-1.0.0"
    train_keys_sha256: Sha256
    train_labels_sha256: Sha256
    train_rows: Annotated[int, Field(ge=1)]
    numeric: tuple[CampaignNumericEncoding, ...]
    categorical: tuple[CampaignCategoricalEncoding, ...]
    history_fill: float
    history_center: float
    history_spread: Annotated[float, Field(gt=0)]
    target_scale: Annotated[float, Field(ge=1)]
    output_columns: tuple[str, ...]

    @model_validator(mode="after")
    def inventory(self) -> Self:
        numeric = tuple(n for n, t in FEATURE_TYPES.items() if t != "str")
        categorical = tuple(n for n, t in FEATURE_TYPES.items() if t == "str")
        expected = [c for n in numeric for c in (n, n + "__missing")]
        for item in self.categorical:
            expected.extend((item.name + "__missing", item.name + "__unknown"))
            expected.extend(item.name + f"__category_{i}" for i in range(len(item.categories)))
        if (
            tuple(x.name for x in self.numeric) != numeric
            or tuple(x.name for x in self.categorical) != categorical
            or self.output_columns != tuple(expected)
        ):
            raise ValueError("campaign_encoder_feature_inventory_mismatch")
        return self

    @property
    def tensorflow_width(self) -> int:
        return 28 * 3 + 14 * (len(self.output_columns) + 1)

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)


class CampaignForecastFitReceipt(Contract):
    version: Literal["ai09-campaign-forecast-fit-receipt-1.0.0"] = (
        "ai09-campaign-forecast-fit-receipt-1.0.0"
    )
    protocol_sha256: Sha256
    operation_id: Symbol
    reservation_id: Annotated[str, Field(pattern=r"^campaign-operation-[0-9a-f]{32}$")]
    plan: CampaignForecastFitPlan
    export_receipt_sha256: Sha256
    dataset_id: Annotated[str, Field(pattern=r"^ai09-physical-forecast-sha256-[0-9a-f]{64}$")]
    runtime_code_sha256: Sha256
    train_keys_sha256: Sha256
    early_stopping_keys_sha256: Sha256
    train_eligible_rows: Annotated[int, Field(ge=1)]
    early_stopping_eligible_rows: Annotated[int, Field(ge=1)]
    encoding_sha256: Sha256
    model_artifact_sha256: Sha256
    model_artifact_bytes: Annotated[int, Field(ge=1)]
    artifact_files: dict[str, Sha256]
    worker_evidence: dict[str, JsonValue]
    final_test_accessed: FalseFlag = False
    quality_qualified: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def artifact(self) -> Self:
        if (
            not self.artifact_files
            or self.model_artifact_sha256 != canonical_sha256(self.artifact_files)
            or self.model_artifact_bytes > self.plan.max_artifact_bytes
            or max(self.train_eligible_rows, self.early_stopping_eligible_rows)
            > self.plan.max_train_rows
        ):
            raise ValueError("campaign_fit_artifact_or_population_budget_mismatch")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)
