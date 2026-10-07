"""Strict, versioned preparation contract for the three-use-case evaluation."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import CommitSha, Contract, FalseFlag, Sha256, TrueFlag
from retailops_ai.source_snapshot.files import relative_path

Repository = Literal["retailops-cloud-native-platform", "retailops-ai-intelligence"]
Scenario = Literal["normal", "promotion", "demand_shock", "inventory_constraint"]
SCENARIOS: tuple[Scenario, ...] = ("normal", "promotion", "demand_shock", "inventory_constraint")
USE_CASES: tuple[Literal["forecast"], Literal["anomaly"], Literal["stockout"]] = (
    "forecast",
    "anomaly",
    "stockout",
)
SPECIFICATIONS = {
    ("retailops-cloud-native-platform", "docs/plans/ai/etapy/09-tensorflow-robustness.md"),
    ("retailops-cloud-native-platform", "docs/plans/ai/etapy/07-anomalie-dq.md"),
    ("retailops-cloud-native-platform", "docs/plans/ai/etapy/08-stockout-risk.md"),
    ("retailops-cloud-native-platform", "docs/plans/ai/kontrakty/profile-i-bramki.md"),
    ("retailops-cloud-native-platform", "docs/plans/ai/kontrakty/dane-i-czas.md"),
    ("retailops-cloud-native-platform", "docs/plans/ai/kontrakty/ml-api-lifecycle.md"),
    ("retailops-ai-intelligence", "contracts/forecast/v2/quality.default.json"),
}


class SpecificationPin(Contract):
    repository: Repository
    commit_sha: CommitSha
    relative_path: Annotated[str, Field(min_length=1, max_length=256)]
    sha256: Sha256
    size_bytes: Annotated[int, Field(ge=1, le=1024 * 1024)]

    @model_validator(mode="after")
    def safe_path(self) -> Self:
        relative_path(self.relative_path)
        if (self.repository, self.relative_path) not in SPECIFICATIONS:
            raise ValueError("evaluation_specification_not_allowed")
        return self


class TensorFlowDevelopmentBudget(Contract):
    """A bounded initial recipe proposal, not a measured performance acceptance."""

    scope: Literal["initial_development_recipe_proposal"] = "initial_development_recipe_proposal"
    maximum_trials: Annotated[int, Field(ge=1, le=2)] = 2
    maximum_epochs: Annotated[int, Field(ge=1, le=25)] = 25
    maximum_trial_wall_seconds: Annotated[int, Field(ge=1, le=1200)] = 1200
    maximum_process_tree_rss_mib: Annotated[int, Field(ge=1, le=1024)] = 1024
    cpu_threads: Annotated[int, Field(ge=1, le=1)] = 1
    early_stopping: Literal["development_validation_only"] = "development_validation_only"
    measured_budget_passed: FalseFlag = False


class EvaluationPreparation(Contract):
    version: Literal["ai09-evaluation-preparation-1.0.0"] = "ai09-evaluation-preparation-1.0.0"
    scope: Literal["planning_only_no_dataset_or_split_binding"] = (
        "planning_only_no_dataset_or_split_binding"
    )
    specifications: Annotated[tuple[SpecificationPin, ...], Field(min_length=7, max_length=7)]
    data_seeds: tuple[Literal[42], Literal[137], Literal[2026]] = (42, 137, 2026)
    training_initialization_seeds: tuple[Annotated[int, Field(ge=0, le=2**31 - 1)], ...] = (42,)
    scenarios: tuple[Scenario, ...] = SCENARIOS
    use_cases: tuple[Literal["forecast"], Literal["anomaly"], Literal["stockout"]] = USE_CASES
    final_profile: Literal["ai-training"] = "ai-training"
    tensorflow_horizons: tuple[int, ...] = tuple(range(1, 15))
    tensorflow_architecture: Literal["compact_dense_direct_multi_horizon"] = (
        "compact_dense_direct_multi_horizon"
    )
    tensorflow_budget: TensorFlowDevelopmentBudget = TensorFlowDevelopmentBudget()
    preprocessing_fit_scope: Literal["eligible_train_of_current_fold_only"] = (
        "eligible_train_of_current_fold_only"
    )
    prediction_population: Literal["same_complete_evaluated_keys_for_every_model"] = (
        "same_complete_evaluated_keys_for_every_model"
    )
    seed_reporting: Literal["every_seed_and_scenario_before_any_aggregate"] = (
        "every_seed_and_scenario_before_any_aggregate"
    )
    aggregate_policy: Literal["must_be_reviewed_and_bound_before_final_test"] = (
        "must_be_reviewed_and_bound_before_final_test"
    )
    uncertainty_resampling: Literal["time_block_or_series_cluster_not_independent_rows"] = (
        "time_block_or_series_cluster_not_independent_rows"
    )
    quality_policy: Literal["pin_use_case_rules_and_actual_thresholds_before_final_test"] = (
        "pin_use_case_rules_and_actual_thresholds_before_final_test"
    )
    missing_metrics: Literal["not_evaluable_never_ideal_by_default"] = (
        "not_evaluable_never_ideal_by_default"
    )
    inherited_ai04_exceptions: FalseFlag = False
    final_test_access_authorized: FalseFlag = False
    model_promotion_authorized: FalseFlag = False
    requires_upstream_acceptance: TrueFlag = True

    @model_validator(mode="after")
    def complete_plan(self) -> Self:
        pins = {(pin.repository, pin.relative_path) for pin in self.specifications}
        if pins != SPECIFICATIONS:
            raise ValueError("evaluation_specification_inventory_mismatch")
        for repository in ("retailops-cloud-native-platform", "retailops-ai-intelligence"):
            if (
                len({pin.commit_sha for pin in self.specifications if pin.repository == repository})
                != 1
            ):
                raise ValueError("evaluation_specifications_require_one_revision_per_repository")
        if self.scenarios != SCENARIOS or self.tensorflow_horizons != tuple(range(1, 15)):
            raise ValueError("evaluation_scenarios_or_horizons_changed")
        seeds = self.training_initialization_seeds
        if not seeds or len(seeds) > 2 or len(set(seeds)) != len(seeds):
            raise ValueError("evaluation_training_seed_budget_invalid")
        return self


class PreparationRuntime(Contract):
    code_files: dict[str, Sha256]
    code_sha256: Sha256
    dependency_lock_sha256: Sha256
    python_version: Annotated[str, Field(pattern=r"^3\.11\.[0-9]+$")]


class PreparationDescriptor(Contract):
    plan: EvaluationPreparation
    runtime: PreparationRuntime


class PreparationManifest(Contract):
    preparation_id: Annotated[str, Field(pattern=r"^ai09-preparation-sha256-[0-9a-f]{64}$")]
    descriptor: PreparationDescriptor
