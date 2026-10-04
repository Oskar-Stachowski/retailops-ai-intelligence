"""Frozen development comparison; no final-test access, selection or release."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    FalseFlag,
    FeatureID,
    ForecastKey,
    Sha256,
    SplitID,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.contract import Parent
from retailops_ai.forecasting.functional_contract import FunctionalPolicy, Head
from retailops_ai.forecasting.manifest_contract import FoldPlan
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast
from retailops_ai.tensorflow_challenger.contract import ChallengerPolicy

MODELS = ("history7", "history28", "weekday28", "rf_mean", "hgb", "tensorflow")
COMPARISON_HEADS: tuple[Head, ...] = ("rf_mean", "hgb_mean", "hgb_median")


class DevelopmentComparisonPolicy(Contract):
    version: Literal["ai09-forecast-development-comparison-1.0.0"] = (
        "ai09-forecast-development-comparison-1.0.0"
    )
    tensorflow: ChallengerPolicy = ChallengerPolicy()
    trees: FunctionalPolicy = FunctionalPolicy()
    tree_heads: tuple[Head, ...] = COMPARISON_HEADS
    reference: Literal["fixed_history28_declared_before_fitting"] = (
        "fixed_history28_declared_before_fitting"
    )
    selection: Literal["none_one_frozen_configuration_per_family"] = (
        "none_one_frozen_configuration_per_family"
    )
    rf_functional: Literal["conditional_mean_only_no_median_substitution"] = (
        "conditional_mean_only_no_median_substitution"
    )
    interval_scope: Literal["raw_empirical_baselines_only_no_calibrated_candidate"] = (
        "raw_empirical_baselines_only_no_calibrated_candidate"
    )
    evaluation_scope: Literal["early_stopping_validation_diagnostic_not_independent_acceptance"] = (
        "early_stopping_validation_diagnostic_not_independent_acceptance"
    )
    max_output_bytes: Annotated[int, Field(ge=1024, le=128 * 1024**2)] = 128 * 1024**2
    final_test_accessed: FalseFlag = False
    promotion_allowed: FalseFlag = False

    @model_validator(mode="after")
    def inventory(self) -> Self:
        if self.tree_heads != COMPARISON_HEADS:
            raise ValueError("development_comparison_head_inventory")
        return self


class DevelopmentPrediction(ForecastKey):
    value: FunctionalForecast


class DevelopmentProtocol(Contract):
    schema_version: Literal["ai09-forecast-development-protocol-1.0.0"] = (
        "ai09-forecast-development-protocol-1.0.0"
    )
    policy: DevelopmentComparisonPolicy
    feature_set_id: FeatureID
    split_id: SplitID
    parent: Parent
    source_parameters: dict[str, JsonValue]
    source_schema_version: str
    fold: FoldPlan
    feature_descriptor_sha256: Sha256
    split_descriptor_sha256: Sha256
    train_population_sha256: Sha256
    validation_population_sha256: Sha256
    core_environment: dict[str, JsonValue]
    tensorflow_lock_sha256: Sha256
    implementation_sha256: Sha256
    final_test_accessed: FalseFlag = False
    promotion_allowed: FalseFlag = False


class ComparisonFile(Contract):
    size_bytes: Annotated[int, Field(ge=0)]
    sha256: Sha256


class DevelopmentComparisonManifest(Contract):
    schema_version: Literal["ai09-forecast-development-comparison-1.0.0"] = (
        "ai09-forecast-development-comparison-1.0.0"
    )
    comparison_id: Annotated[str, Field(pattern=r"^ai09-development-sha256-[0-9a-f]{64}$")]
    protocol: DevelopmentProtocol
    files: dict[str, ComparisonFile]
    status: Literal["completed_development_diagnostic"]
    deployment_status: Literal["not_ready"] = "not_ready"
    final_test_accessed: FalseFlag = False
    promotion_allowed: FalseFlag = False

    @model_validator(mode="after")
    def identity(self) -> Self:
        payload = self.model_dump(mode="json", include={"protocol", "files"})
        if self.comparison_id != "ai09-development-sha256-" + canonical_sha256(payload):
            raise ValueError("development_comparison_identity_mismatch")
        if (
            not self.files
            or sum(f.size_bytes for f in self.files.values())
            > self.protocol.policy.max_output_bytes
        ):
            raise ValueError("development_comparison_output_budget")
        return self
