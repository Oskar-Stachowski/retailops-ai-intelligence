"""Preregister equal forecast search budgets and compile existing full-population fits.

Compilation performs no training, selection, Source read or final admission.
The executing journal must bind these plans and independently verified selection
evidence. A caller-provided finalist name is never evidence of a winning model.
"""

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, Symbol
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_contract import CampaignSourceRecipe
from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastFitPlan, Family
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import (
    CampaignPortfolioProtocol,
    PortfolioSourceRecipe,
)
from retailops_ai.evaluation_campaign.campaign_tune_contract import CampaignForecastTunePolicy
from retailops_ai.evaluation_campaign.development_profiles import (
    DevelopmentProfilePreparation,
    DevelopmentProfileSource,
)
from retailops_ai.evaluation_campaign.preparation_execution import write_once
from retailops_ai.forecasting.model_contract import HGBConfig, RFConfig

FAMILIES = ("rf", "hgb", "tensorflow")
SCALES = (25, 50, 100)


class ForecastSearchRecipe(Contract):
    name: Symbol
    family: Family
    rf: RFConfig = RFConfig(max_depth=10, min_samples_leaf=4)
    hgb: HGBConfig = HGBConfig()
    epochs: Annotated[int, Field(ge=1, le=25)] = 25
    batch_size: Annotated[int, Field(ge=1, le=256)] = 64
    patience: Annotated[int, Field(ge=1, le=5)] = 4
    learning_rate: Annotated[float, Field(gt=0, le=0.01)] = 0.001

    def effective_sha256(self) -> str:
        values = self.model_dump(mode="json")
        fields = (
            (self.family,)
            if self.family != "tensorflow"
            else ("epochs", "batch_size", "patience", "learning_rate")
        )
        return canonical_sha256({name: values[name] for name in fields})


class ForecastDevelopmentSearch(Contract):
    version: Literal["ai09-fair-multiscale-forecast-search-1.0.0"] = (
        "ai09-fair-multiscale-forecast-search-1.0.0"
    )
    preparation_25_sha256: Sha256
    preparation_50_sha256: Sha256
    canonical_portfolio_protocol_sha256: Sha256
    partition_policy_sha256: Sha256
    critical_group_policy_sha256: Sha256
    recipes: Annotated[tuple[ForecastSearchRecipe, ...], Field(min_length=6, max_length=6)]
    training_initialization_seed: Literal[42] = 42
    data_seed: Literal[42] = 42
    worker_environment_lock_sha256: dict[Family, Sha256]
    resources_by_products: dict[Literal["25", "50", "100"], CampaignGenerationResources]
    maximum_fit_attempts: Literal[12] = 12
    attempts_per_recipe_and_scale: Literal[1] = 1
    order: tuple[Literal[25], Literal[50], Literal[100]] = (25, 50, 100)
    recipes_per_family_by_products: tuple[Literal[2], Literal[1], Literal[1]] = (2, 1, 1)
    screening: Literal[
        "per_family_tune_mean_mse_existing_bias_coverage_gates_then_frozen_recipe_order"
    ] = "per_family_tune_mean_mse_existing_bias_coverage_gates_then_frozen_recipe_order"
    screening_policy: CampaignForecastTunePolicy = CampaignForecastTunePolicy()
    finalist_rule: Literal["same_one_recipe_per_family_at_50_and_100_no_new_hyperparameters"] = (
        "same_one_recipe_per_family_at_50_and_100_no_new_hyperparameters"
    )
    final_choice: Literal["unchanged_native_tune_calibration_and_full_three_use_qualification"] = (
        "unchanged_native_tune_calibration_and_full_three_use_qualification"
    )
    failed_attempts: Literal["consume_slot_retain_cost_and_error_no_automatic_retry"] = (
        "consume_slot_retain_cost_and_error_no_automatic_retry"
    )
    family_failure: Literal[
        "retain_failure_and_stop_advancement_if_any_family_has_no_valid_trial"
    ] = "retain_failure_and_stop_advancement_if_any_family_has_no_valid_trial"
    preparation_cost: Literal["prepare_once_per_variant_and_scale_report_cold_and_ready_costs"] = (
        "prepare_once_per_variant_and_scale_report_cold_and_ready_costs"
    )
    paired_population: Literal["all_common_native_keys_per_scale_no_history_or_row_subsampling"] = (
        "all_common_native_keys_per_scale_no_history_or_row_subsampling"
    )
    independent_evaluation_used_for_screening: FalseFlag = False
    final_test_used_for_screening: FalseFlag = False
    execution_authorized: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def fair_budget(self) -> Self:
        if (
            tuple(r.family for r in self.recipes)
            != ("rf", "rf", "hgb", "hgb", "tensorflow", "tensorflow")
            or len({r.name for r in self.recipes}) != 6
            or set(self.worker_environment_lock_sha256) != set(FAMILIES)
            or set(self.resources_by_products) != {"25", "50", "100"}
            or self.preparation_25_sha256 == self.preparation_50_sha256
        ):
            raise ValueError("forecast_search_complete_equal_family_scale_inventory")
        for family in FAMILIES:
            if len({r.effective_sha256() for r in self.recipes if r.family == family}) != 2:
                raise ValueError("forecast_search_requires_two_effectively_distinct_recipes")
        for resources in self.resources_by_products.values():
            if (
                resources.tree_rss_bytes > 12 * 1024**3
                or resources.scratch_bytes > 8 * 1024**3
                or resources.wall_seconds > 1200
                or resources.minimum_available_memory_bytes < 1024**3
                or resources.minimum_free_disk_bytes < 6 * 1024**3
            ):
                raise ValueError("forecast_search_resource_ceiling_or_reserve")
        return self

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))


def compile_forecast_search_fits(
    design: ForecastDevelopmentSearch,
    *,
    source: CampaignSourceRecipe,
    export_operation_id: Symbol,
    finalist_names: tuple[str, ...] | None = None,
    preparation: DevelopmentProfilePreparation | None = None,
    canonical_protocol: CampaignPortfolioProtocol | None = None,
) -> tuple[CampaignForecastFitPlan, ...]:
    """Compile recipes only; actual finalist evidence is required by the executor.

    Stage 25 includes every preregistered recipe. Later stages require exactly
    one original recipe from each family and preserve its hyperparameters/seed.
    This function cannot admit a run, establish advancement or change a journal.
    """
    design = ForecastDevelopmentSearch.model_validate_json(design.model_dump_json())
    cls = (
        DevelopmentProfileSource
        if isinstance(source, DevelopmentProfileSource)
        else PortfolioSourceRecipe
        if isinstance(source, PortfolioSourceRecipe)
        else CampaignSourceRecipe
    )
    source = cls.model_validate_json(source.model_dump_json())
    if source.phase != "development" or source.products not in SCALES:
        raise ValueError("forecast_search_requires_declared_development_scale")
    if isinstance(source, PortfolioSourceRecipe) and source.variant != "ordinary":
        raise ValueError("forecast_search_training_source_requires_ordinary_variant")
    resources_key: Literal["25", "50", "100"] = (
        "25" if source.products == 25 else "50" if source.products == 50 else "100"
    )
    if source.products in (25, 50):
        expected = (
            design.preparation_25_sha256 if source.products == 25 else design.preparation_50_sha256
        )
        if preparation is None:
            raise ValueError("forecast_search_preparation_binding_required")
        preparation = DevelopmentProfilePreparation.model_validate_json(
            preparation.model_dump_json()
        )
        if preparation.content_sha256() != expected or preparation.sources[0] != source:
            raise ValueError("forecast_search_preparation_or_source_changed")
    else:
        if canonical_protocol is None:
            raise ValueError("forecast_search_original_canonical_portfolio_required")
        canonical_protocol = CampaignPortfolioProtocol.model_validate_json(
            canonical_protocol.model_dump_json()
        )
        if (
            canonical_protocol.content_sha256() != design.canonical_portfolio_protocol_sha256
            or canonical_protocol.sources[0] != source
            or canonical_protocol.selection_policy_sha256 != design.critical_group_policy_sha256
        ):
            raise ValueError("forecast_search_original_canonical_portfolio_changed")
    selected = design.recipes
    if source.products == 25:
        if finalist_names is not None:
            raise ValueError("forecast_search_cannot_drop_screening_recipes")
    else:
        if finalist_names is None or len(set(finalist_names)) != 3:
            raise ValueError("forecast_search_requires_three_original_finalists")
        selected = tuple(r for r in design.recipes if r.name in finalist_names)
        if (
            tuple(r.family for r in selected) != FAMILIES
            or tuple(r.name for r in selected) != finalist_names
        ):
            raise ValueError("forecast_search_finalists_must_cover_ordered_original_families")
    return tuple(
        CampaignForecastFitPlan(
            source_recipe_sha256=source.content_sha256(),
            export_operation_id=export_operation_id,
            family=recipe.family,
            initialization_seed=design.training_initialization_seed,
            worker_environment_lock_sha256=design.worker_environment_lock_sha256[recipe.family],
            resources=design.resources_by_products[resources_key],
            **recipe.model_dump(exclude={"name", "family"}),
        )
        for recipe in selected
    )


def freeze_forecast_search(path: Path, design: ForecastDevelopmentSearch) -> str:
    """Write a private immutable declaration before observations; grant no execution."""
    design = ForecastDevelopmentSearch.model_validate_json(design.model_dump_json())
    write_once(path, design.model_dump(mode="json"))
    return design.content_sha256()
