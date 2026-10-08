"""Durable whole-role evidence; operation completion is separate from qualification."""

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, Symbol
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPlan,
    CampaignForecastFrozenConfiguration,
    CampaignForecastQualityPolicy,
    EvaluationRole,
)
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)


class CampaignSelectionComponents(Contract):
    """Concrete compatible identities; the enclosing receipt supplies selection evidence."""

    model_artifact_sha256: Sha256
    preprocessing_sha256: Sha256
    calibration_sha256: Sha256
    threshold_policy_sha256: Sha256
    feature_schema_sha256: Sha256


def trial_metrics_name(index: int) -> str:
    if type(index) is not int or not 0 <= index < 64:
        raise ValueError("campaign_evaluation_trial_inventory_index_invalid")
    return f"trials/trial-{index:03d}.json"


class CampaignForecastEvaluationRecipe(Contract):
    """Preregister identities of parent operations, never the unknown result hashes.

    Embedding a configuration's result hash here would create a cycle through
    the protocol hash stored in its fit/selection/calibration receipts.
    The separately resolved v19 worker plan pins those results after completion.
    """

    version: Literal["ai09-campaign-forecast-evaluation-recipe-1.0.0"] = (
        "ai09-campaign-forecast-evaluation-recipe-1.0.0"
    )
    phase: Literal["development", "final"]
    role: EvaluationRole
    source_recipe_sha256: Sha256
    export_operation_id: Symbol
    tune_operation_id: Symbol
    calibration_operation_id: Symbol
    tune_score_operation_ids: Annotated[tuple[Symbol, ...], Field(min_length=1, max_length=64)]
    quality_policy: CampaignForecastQualityPolicy = CampaignForecastQualityPolicy()
    segment_policy_sha256: Sha256
    uncertainty_policy_sha256: Sha256
    worker_environment_lock_sha256: Sha256
    max_rows: Annotated[int, Field(ge=1, le=100000000)] = 10000000
    max_index_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)] = 4 * 1024**3
    max_output_bytes: Annotated[int, Field(ge=1024, le=32 * 1024**3)] = 8 * 1024**3
    batch_windows: Annotated[int, Field(ge=1, le=256)] = 64
    resources: CampaignGenerationResources
    population: Literal["every_role_key_every_frozen_trial_no_sampling"] = (
        "every_role_key_every_frozen_trial_no_sampling"
    )
    training_or_preprocessing_refitted: FalseFlag = False
    architecture_reselected: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def scope(self) -> Self:
        if (
            (self.phase == "final") != (self.role == "final_test")
            or len(set(self.tune_score_operation_ids)) != len(self.tune_score_operation_ids)
            or self.tune_operation_id == self.calibration_operation_id
        ):
            raise ValueError("campaign_evaluation_recipe_scope_or_inventory_mismatch")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)

    def resolve(
        self, configuration: CampaignForecastFrozenConfiguration
    ) -> CampaignForecastEvaluationPlan:
        if (
            configuration.tune_operation_id != self.tune_operation_id
            or configuration.calibration_operation_id != self.calibration_operation_id
            or tuple(trial.tune_score_operation_id for trial in configuration.trials)
            != self.tune_score_operation_ids
            or configuration.worker_environment_lock_sha256 != self.worker_environment_lock_sha256
            or configuration.quality_policy_sha256 != self.quality_policy.content_sha256()
        ):
            raise ValueError("campaign_evaluation_recipe_completed_parent_mismatch")
        fields = self.model_dump(
            mode="json",
            exclude={
                "version",
                "tune_operation_id",
                "calibration_operation_id",
                "tune_score_operation_ids",
            },
        )
        fields["frozen_configuration_sha256"] = configuration.content_sha256()
        return CampaignForecastEvaluationPlan.model_validate_json(canonical_bytes(fields))


class _CampaignForecastEvaluationFields(Contract):
    use_case: Literal["forecast"] = "forecast"
    protocol_sha256: Sha256
    operation_id: Symbol
    reservation_id: Annotated[str, Field(pattern=r"^campaign-operation-[0-9a-f]{32}$")]
    recipe: CampaignForecastEvaluationRecipe
    plan: CampaignForecastEvaluationPlan
    configuration: CampaignForecastFrozenConfiguration
    export_receipt_sha256: Sha256
    selection_sha256: Sha256 | None
    selection_components: CampaignSelectionComponents
    dataset_id: Annotated[
        str, Field(pattern=r"^ai09-(physical|final)-forecast-sha256-[0-9a-f]{64}$")
    ]
    runtime_code_sha256: Sha256
    role_population_sha256: Sha256
    keys_sha256: Sha256
    eligible_keys_sha256: Sha256
    rows: Annotated[int, Field(ge=1)]
    eligible_rows: Annotated[int, Field(ge=0)]
    trial_prediction_trace_sha256: dict[str, Sha256]
    baseline_trace_sha256: Sha256
    artifact_sha256: Sha256
    artifact_bytes: Annotated[int, Field(ge=1)]
    artifact_files: dict[str, Sha256]
    worker_evidence: dict[str, JsonValue]
    full_role_label_file_passes: Literal[1] = 1
    actual_index_passes: Annotated[int, Field(ge=2, le=65)]
    all_frozen_trials_compared: Literal[True] = True
    all_models_share_all_role_keys: Literal[True] = True
    training_or_preprocessing_refitted: FalseFlag = False
    architecture_reselected: FalseFlag = False
    critical_segment_inventory_complete: bool = False
    block_uncertainty_complete: bool = False
    final_test_accessed: bool
    quality_qualified: bool = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def complete(self) -> Self:
        final = self.plan.phase == "final"
        required = {
            "recipe.json",
            "plan.json",
            "configuration.json",
            "parents.json",
            "population.json",
            "metrics.json",
            "predictions.jsonl",
            *(trial_metrics_name(i) for i in range(len(self.configuration.trials))),
        }
        if (
            self.recipe.resolve(self.configuration) != self.plan
            or self.configuration.content_sha256() != self.plan.frozen_configuration_sha256
            or self.configuration.protocol_sha256 != self.protocol_sha256
            or self.configuration.runtime_code_sha256 != self.runtime_code_sha256
            or self.configuration.worker_environment_lock_sha256
            != self.plan.worker_environment_lock_sha256
            or self.configuration.quality_policy_sha256 != self.plan.quality_policy.content_sha256()
            or not final
            and (
                self.configuration.development_source_recipe_sha256
                != self.plan.source_recipe_sha256
                or self.configuration.development_dataset_id != self.dataset_id
            )
            or final != self.dataset_id.startswith("ai09-final-forecast-")
            or final != (self.selection_sha256 is not None)
            or final != self.final_test_accessed
            or set(self.trial_prediction_trace_sha256)
            != {t.tune_score_operation_id for t in self.configuration.trials}
            or self.actual_index_passes != len(self.configuration.trials) + 1
            or self.eligible_rows > self.rows
            or self.rows > self.plan.max_rows
            or self.artifact_bytes > self.plan.max_output_bytes
            or set(self.artifact_files) != required
            or self.artifact_sha256 != canonical_sha256(self.artifact_files)
            or self.quality_qualified
            and not (self.critical_segment_inventory_complete and self.block_uncertainty_complete)
            or self.selection_components.feature_schema_sha256
            != self.configuration.feature_schema_sha256
            or self.selection_components.threshold_policy_sha256
            != self.configuration.quality_policy_sha256
        ):
            raise ValueError("campaign_evaluation_receipt_binding_inventory_or_scope_mismatch")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)


class CampaignForecastEvaluationReceipt(_CampaignForecastEvaluationFields):
    version: Literal["ai09-campaign-forecast-evaluation-receipt-1.0.0"] = (
        "ai09-campaign-forecast-evaluation-receipt-1.0.0"
    )
