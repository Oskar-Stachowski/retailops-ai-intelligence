"""Frozen chronological development backtests, fold evidence and pooled metrics."""

from datetime import timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    DateWindow,
    FeatureID,
    LabelID,
    ModelID,
    Sha256,
    SplitID,
    UtcTime,
    end_of_day,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.contract import OriginWindow, Parent
from retailops_ai.forecasting.evaluation_contract import BASELINES, MetricResult
from retailops_ai.forecasting.manifest_contract import FoldPlan, SplitPolicy
from retailops_ai.forecasting.model_contract import LEARNED_NAMES, ModelCode, ModelPolicy

METHODS = (*BASELINES, *LEARNED_NAMES, "validation_selected")
EVALUATION_ROLES = ("validation", "development_holdout")


class BacktestPolicy(Contract):
    version: Literal["forecast-chronological-backtest-1.0.0"] = (
        "forecast-chronological-backtest-1.0.0"
    )
    mode: Literal["expanding", "rolling"] = "expanding"
    folds: Annotated[int, Field(ge=2, le=10)] = 3
    initial_train_days: Annotated[int, Field(ge=1, le=300)] = 6
    validation_days: Annotated[int, Field(ge=1, le=90)] = 6
    development_holdout_days: Annotated[int, Field(ge=1, le=90)] = 6
    step_days: Annotated[int, Field(ge=1, le=90)] = 6
    purge_days: Annotated[int, Field(ge=15, le=90)] = 15
    label_tail_days: Annotated[int, Field(ge=15, le=90)] = 15
    minimum_eligible_rows_per_role: Annotated[int, Field(ge=1, le=100000)] = 1
    anchoring: Literal["calendar_start_unused_origins_retained_as_purged"] = (
        "calendar_start_unused_origins_retained_as_purged"
    )
    selection: Literal["validation_only_independently_per_fold"] = (
        "validation_only_independently_per_fold"
    )
    aggregate: Literal["pool_error_and_actual_sums_no_mean_of_wape"] = (
        "pool_error_and_actual_sums_no_mean_of_wape"
    )
    evaluation_overlap: Literal["same_role_origins_must_not_repeat_across_folds"] = (
        "same_role_origins_must_not_repeat_across_folds"
    )
    history: Literal["earlier_observations_allowed_as_of_each_origin"] = (
        "earlier_observations_allowed_as_of_each_origin"
    )
    portfolio_final_test: Literal["not_included_not_opened"] = "not_included_not_opened"
    development_holdout_use: Literal["diagnostic_previously_observed_data_not_fresh_final_test"] = (
        "diagnostic_previously_observed_data_not_fresh_final_test"
    )
    model: ModelPolicy = ModelPolicy()

    @model_validator(mode="after")
    def non_overlapping_evaluation(self) -> Self:
        if self.step_days < max(self.validation_days, self.development_holdout_days):
            raise ValueError("backtest_repeated_evaluation_origins_forbidden")
        return self


def plan_backtest(window: OriginWindow, policy: BacktestPolicy) -> SplitPolicy:
    """Calendar arithmetic only; configuration is resolved before reading any outcomes."""
    folds = []
    for index in range(policy.folds):
        shift = index * policy.step_days
        train_end = window.start + timedelta(days=policy.initial_train_days - 1 + shift)
        train_start = window.start + timedelta(days=shift if policy.mode == "rolling" else 0)
        validation_start = train_end + timedelta(days=policy.purge_days + 1)
        validation_end = validation_start + timedelta(days=policy.validation_days - 1)
        holdout_start = validation_end + timedelta(days=policy.purge_days + 1)
        holdout_end = holdout_start + timedelta(days=policy.development_holdout_days - 1)
        if holdout_end > window.end:
            raise ValueError("backtest_insufficient_origin_window")
        folds.append(
            FoldPlan(
                name=f"{policy.mode}-{index + 1:02d}",
                train=DateWindow(start=train_start, end=train_end),
                validation=DateWindow(start=validation_start, end=validation_end),
                development_holdout=DateWindow(start=holdout_start, end=holdout_end),
                purge_days=policy.purge_days,
                training_cutoff=end_of_day(validation_start - timedelta(days=1)),
                selection_cutoff=end_of_day(holdout_start - timedelta(days=1)),
                evaluation_cutoff=end_of_day(holdout_end + timedelta(days=policy.label_tail_days)),
            )
        )
    return SplitPolicy(
        folds=tuple(folds), minimum_eligible_rows_per_role=policy.minimum_eligible_rows_per_role
    )


class BacktestCode(Contract):
    version: Literal["forecast-backtest-1.0.0"] = "forecast-backtest-1.0.0"
    code_files: dict[str, Sha256]
    code_sha256: Sha256
    model: ModelCode

    @model_validator(mode="after")
    def digest(self) -> Self:
        if not self.code_files or canonical_sha256(self.code_files) != self.code_sha256:
            raise ValueError("backtest_code_digest_mismatch")
        return self


class FoldAudit(Contract):
    plan: FoldPlan
    eligible_train_rows: Annotated[int, Field(ge=1)]
    latest_train_label_available_at: UtcTime
    train_labels_content_sha256: Sha256
    model_ids: dict[str, ModelID]
    common_prediction_keys: Annotated[int, Field(ge=1)]
    common_grain_sha256: Sha256
    eligible_counts: dict[str, Annotated[int, Field(ge=0)]]
    future_training_labels: Literal[0] = 0
    model_specific_key_drops: Literal[0] = 0
    status: Literal["passed"] = "passed"

    @model_validator(mode="after")
    def maturity(self) -> Self:
        if (
            self.latest_train_label_available_at > self.plan.training_cutoff
            or set(self.model_ids) != set(LEARNED_NAMES)
            or set(self.eligible_counts) != {"train", "validation", "development_holdout", "purged"}
            or self.eligible_counts["train"] != self.eligible_train_rows
        ):
            raise ValueError("backtest_fold_maturity_or_coverage_mismatch")
        return self


class BacktestDescriptor(Contract):
    role: Literal["chronological_development_backtest"] = "chronological_development_backtest"
    target_type: Literal["observed_sales_units"] = "observed_sales_units"
    feature_set_id: FeatureID
    parent: Parent
    origin_window: OriginWindow
    policy: BacktestPolicy
    resolved_split: SplitPolicy
    split_id: SplitID
    label_dataset_id: LabelID
    comparison_id: Annotated[str, Field(pattern=r"^forecast-model-comparison-sha256-[0-9a-f]{64}$")]
    comparison_descriptor_sha256: Sha256
    comparison_status: Literal["passed", "not_ready"]
    code: BacktestCode
    folds: tuple[FoldAudit, ...]
    pooled_metrics: dict[str, MetricResult]
    status: Literal["passed", "not_ready"]

    @model_validator(mode="after")
    def protocol(self) -> Self:
        if (
            self.resolved_split != plan_backtest(self.origin_window, self.policy)
            or tuple(audit.plan for audit in self.folds) != self.resolved_split.folds
        ):
            raise ValueError("backtest_resolved_plan_mismatch")
        if set(self.pooled_metrics) != {
            role + ":" + method for role in EVALUATION_ROLES for method in METHODS
        }:
            raise ValueError("backtest_pooled_role_method_mismatch")
        if (self.status == "passed") != (
            self.comparison_status == "passed"
            and all(metric.status == "passed" for metric in self.pooled_metrics.values())
        ):
            raise ValueError("backtest_status_mismatch")
        return self


class BacktestManifest(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    backtest_id: Annotated[str, Field(pattern=r"^forecast-backtest-sha256-[0-9a-f]{64}$")]
    descriptor: BacktestDescriptor
    generated_at: UtcTime
    forecast_model_status: Literal["not_ready"] = "not_ready"

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.backtest_id != "forecast-backtest-sha256-" + canonical_sha256(
            self.descriptor.model_dump(mode="json")
        ):
            raise ValueError("backtest_identity_mismatch")
        return self
