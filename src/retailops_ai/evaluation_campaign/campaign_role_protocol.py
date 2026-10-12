"""Explicit three-use-case roles and resolved trial dependencies in a new journal.

This additive wire keeps original campaign declarations readable and unchanged.
It audits declared access and ordering, not physical reader enforcement or model
quality. A controller must bind every recipe and artifact to these declarations.
"""

from typing import Annotated, Literal, Self

from pydantic import Field, StrictFloat, StrictInt, model_validator

from retailops_ai.data_contracts.common import Contract, Symbol
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.evaluation_campaign.campaign_contract import CampaignOperationPlan, Role
from retailops_ai.evaluation_campaign.campaign_portfolio_contract import (
    CampaignPortfolioJournal,
    CampaignPortfolioProtocol,
)
from retailops_ai.evaluation_campaign.campaign_stockout_maturity_contract import (
    CampaignStockoutMaturityPolicy,
)
from retailops_ai.evaluation_campaign.partition_contract import ForecastPartitionPolicy, RoleWindow
from retailops_ai.stockout_training.contract import Family, TrainingPolicy, Variant

STOCKOUT_FAMILIES: tuple[Family, ...] = ("logistic_regression", "hist_gradient_boosting")
STOCKOUT_VARIANTS: tuple[Variant, ...] = (
    "without_upstream",
    "with_upstream",
    "raw_sales_only",
)
CALIBRATOR_GRID = (0.01, 0.1, 1.0, 10.0)


class CampaignDevelopmentRoles(Contract):
    windows: Annotated[tuple[RoleWindow, ...], Field(min_length=5, max_length=5)]
    preprocessing: Literal["train_only_for_every_use_case"] = "train_only_for_every_use_case"
    forecast: Literal["train_early_stopping_tune_calibration_independent_evaluation"] = (
        "train_early_stopping_tune_calibration_independent_evaluation"
    )
    anomaly: Literal["train_then_tune_capacity_thresholds_then_independent_evaluation"] = (
        "train_then_tune_capacity_thresholds_then_independent_evaluation"
    )
    stockout: Literal["train_then_early_calibrators_then_tune_selection_then_policy_assessment"] = (
        "train_then_early_calibrators_then_tune_selection_then_policy_assessment"
    )

    @model_validator(mode="after")
    def shared_chronology(self) -> Self:
        # Both native horizons and the stricter before-next-midnight boundary
        # apply. There is one stored set of dates, not independently relabelled
        # windows for different consumers.
        ForecastPartitionPolicy(roles=self.windows)
        CampaignStockoutMaturityPolicy(windows=self.windows)
        return self


class RoleAwareCampaignOperation(CampaignOperationPlan):
    read_roles: Annotated[tuple[Role, ...], Field(min_length=1, max_length=2)]
    maximum_attempts: Annotated[StrictInt, Field(ge=1, le=1)] = 1
    resolved_prerequisites: tuple[Symbol, ...] = ()
    stockout_family: Family | None = None
    stockout_variant: Variant | None = None
    stockout_calibrator_C: StrictFloat | None = None

    @model_validator(mode="after")
    def purpose(self) -> Self:
        # Apply all original guards. Only the new, explicitly typed stockout
        # calibration operation has a different permitted development window.
        # The validation copy never enters a journal or serialized declaration.
        calibration = self.action == "calibrator_fit" and self.use_case == "stockout"
        original_fields = self.model_dump(
            mode="python", include=set(CampaignOperationPlan.model_fields)
        )
        if calibration:
            if self.role != "early_stopping":
                raise ValueError("role_protocol_stockout_calibration_requires_early_window")
            original_fields["role"] = "calibration"
        CampaignOperationPlan.model_validate(original_fields)
        if self.action in ("source_generate", "source_read"):
            expected: tuple[Role, ...] = ("all_parent_data",)
        elif self.action == "model_fit":
            fitting_reads: dict[str, tuple[Role, ...]] = {
                "forecast": ("train", "early_stopping"),
                "anomaly": ("train", "tune"),
                "stockout": ("train",),
            }
            expected = fitting_reads[self.use_case]
        else:
            if self.action == "calibrator_fit" and self.use_case == "anomaly":
                raise ValueError("role_protocol_anomaly_thresholds_belong_to_native_fit")
            if self.action == "model_score" and self.role not in (
                "tune",
                "calibration",
                "development_evaluation",
                "final_evaluation",
            ):
                raise ValueError("role_protocol_scoring_window_not_allowlisted")
            expected = (self.role,)
        if self.read_roles != expected:
            raise ValueError("role_protocol_exact_read_roles_required")
        stockout_fit = self.use_case == "stockout" and self.action in (
            "model_fit",
            "calibrator_fit",
        )
        if stockout_fit != (self.stockout_family is not None and self.stockout_variant is not None):
            raise ValueError("role_protocol_stockout_configuration_required")
        if not stockout_fit and (
            self.stockout_family is not None or self.stockout_variant is not None
        ):
            raise ValueError("role_protocol_unrelated_stockout_configuration")
        if calibration:
            if (
                self.stockout_calibrator_C not in CALIBRATOR_GRID
                or len(self.resolved_prerequisites) != 1
            ):
                raise ValueError("role_protocol_conditional_calibrator_grid_and_base_required")
        elif self.stockout_calibrator_C is not None:
            raise ValueError("role_protocol_unrelated_calibrator_parameter")
        selection = (
            self.use_case == "stockout" and self.action == "model_score" and self.role == "tune"
        )
        if self.resolved_prerequisites and not (calibration or selection):
            raise ValueError("role_protocol_resolved_dependencies_only_for_stockout_trials")
        if len(set(self.resolved_prerequisites)) != len(self.resolved_prerequisites) or set(
            self.resolved_prerequisites
        ).intersection(self.prerequisites):
            raise ValueError("role_protocol_duplicate_or_ambiguous_dependency")
        return self


class RoleAwareCampaignProtocol(CampaignPortfolioProtocol):
    role_protocol_version: Literal["ai09-three-use-case-role-portfolio-1.0.0"] = (
        "ai09-three-use-case-role-portfolio-1.0.0"
    )
    development_roles: CampaignDevelopmentRoles
    stockout_training_policy: TrainingPolicy = TrainingPolicy()
    operations: Annotated[
        tuple[RoleAwareCampaignOperation, ...], Field(min_length=7, max_length=256)
    ]

    def content_sha256(self) -> str:
        # Validate Python values before JSON serialization can normalize a
        # copied invalid bool into a numeric budget or model parameter.
        validated = type(self).model_validate(self.model_dump(mode="python"))
        return canonical_sha256(validated.model_dump(mode="json"))

    @model_validator(mode="after")
    def role_inventory(self) -> Self:
        if self.stockout_training_policy != TrainingPolicy():
            raise ValueError("role_protocol_original_stockout_training_policy_required")
        development = next(s for s in self.sources if s.phase == "development")
        ForecastPartitionPolicy(
            roles=self.development_roles.windows,
            label_delay_days=development.label_delay_days,
            purge_days=14 + development.label_delay_days,
        )
        if any(
            w.origins.start < development.history.start
            or w.label_knowledge_cutoff.date() > development.history.end
            for w in self.development_roles.windows
        ):
            raise ValueError("role_protocol_windows_outside_original_development_history")
        prior: dict[str, RoleAwareCampaignOperation] = {}
        for operation in self.operations:
            for key in operation.resolved_prerequisites:
                parent = prior.get(key)
                if parent is None or (
                    parent.phase != operation.phase
                    or parent.source_recipe_sha256 != operation.source_recipe_sha256
                    or parent.use_case != "stockout"
                    or parent.action not in ("model_fit", "calibrator_fit")
                ):
                    raise ValueError("role_protocol_resolved_dependency_scope_or_order")
                if operation.action == "calibrator_fit" and (
                    parent.action != "model_fit"
                    or (parent.stockout_family, parent.stockout_variant)
                    != (operation.stockout_family, operation.stockout_variant)
                ):
                    raise ValueError("role_protocol_calibrator_original_base_required")
            prior[operation.operation_id] = operation
        stockout = [
            o for o in self.operations if o.use_case == "stockout" and o.phase == "development"
        ]
        bases = [o for o in stockout if o.action == "model_fit"]
        calibrators = [o for o in stockout if o.action == "calibrator_fit"]
        if (
            len(bases) != 6
            or {(o.stockout_family, o.stockout_variant) for o in bases}
            != {(f, v) for f in STOCKOUT_FAMILIES for v in STOCKOUT_VARIANTS}
            or any(o.initialization_seed != 42 for o in bases)
        ):
            raise ValueError("role_protocol_complete_six_base_trials_required")
        if len(calibrators) != 24 or {
            (o.stockout_family, o.stockout_variant, o.stockout_calibrator_C) for o in calibrators
        } != {
            (f, v, c) for f in STOCKOUT_FAMILIES for v in STOCKOUT_VARIANTS for c in CALIBRATOR_GRID
        }:
            raise ValueError("role_protocol_complete_twenty_four_calibrators_required")
        selectors = [o for o in stockout if o.action == "model_score" and o.role == "tune"]
        assessments = [o for o in stockout if o.action == "model_score" and o.role == "calibration"]
        if len(selectors) != 1 or len(assessments) != 1:
            raise ValueError("role_protocol_one_stockout_selection_and_policy_assessment")
        selected, assessed = selectors[0], assessments[0]
        if set(selected.resolved_prerequisites) != {o.operation_id for o in (*bases, *calibrators)}:
            raise ValueError("role_protocol_selection_requires_all_thirty_trial_outcomes")
        if selected.operation_id not in assessed.prerequisites or any(
            assessed.operation_id not in o.prerequisites
            for o in stockout
            if o.role == "development_evaluation"
        ):
            raise ValueError("role_protocol_stockout_assessment_before_independent_evaluation")
        return self


class RoleAwareCampaignJournal(CampaignPortfolioJournal):
    protocol: RoleAwareCampaignProtocol

    @model_validator(mode="after")
    def replay(self) -> Self:
        # All original costs, phases, successful prerequisites, attempt budgets,
        # whole development inventory and final freeze guards remain required.
        self._replay_events()
        self._require_development_inventory()
        operations = {o.operation_id: o for o in self.protocol.operations}
        results: dict[str, str] = {}
        for event in self.events:
            if event.kind not in ("reserved", "finished"):
                continue
            operation = operations[str(event.operation_id)]
            if event.kind == "reserved":
                if any(key not in results for key in operation.resolved_prerequisites):
                    raise ValueError("role_protocol_dependency_attempt_must_finish_first")
            else:
                if (
                    event.result == "completed"
                    and operation.action == "calibrator_fit"
                    and any(
                        results.get(key) != "completed" for key in operation.resolved_prerequisites
                    )
                ):
                    raise ValueError(
                        "role_protocol_failed_base_cannot_produce_completed_calibrator"
                    )
                results[operation.operation_id] = str(event.result)
        return self
