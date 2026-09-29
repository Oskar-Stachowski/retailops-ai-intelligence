"""Frozen baseline protocol, common coverage and offline evaluation receipts."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Contract,
    FeatureID,
    ForecastKey,
    LabelID,
    Sha256,
    SplitID,
    UtcTime,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.manifest_contract import Membership, Reason, TableReceipt

BASELINES = ("last_observed", "moving_average", "seasonal_naive7")
BaselineName = Literal["last_observed", "moving_average", "seasonal_naive7"]


class BaselinePolicy(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    candidates: tuple[
        Literal["last_observed"], Literal["moving_average"], Literal["seasonal_naive7"]
    ] = ("last_observed", "moving_average", "seasonal_naive7")
    origin_mode: Literal["fixed_origin_no_recursive_actuals"] = "fixed_origin_no_recursive_actuals"
    moving_average_calendar_days: Literal[7, 14, 28] = 7
    moving_average_minimum_known_days: Annotated[int, Field(ge=1, le=28)] = 1
    missing_history: Literal["skip_unknown_never_impute_zero"] = "skip_unknown_never_impute_zero"
    seasonal_missing: Literal["last_known_same_weekday_within_28_days_or_insufficient_data"] = (
        "last_known_same_weekday_within_28_days_or_insufficient_data"
    )
    closed_history: Literal["confirmed_closed_zero_is_observed"] = (
        "confirmed_closed_zero_is_observed"
    )
    common_keys: Literal["all_split_memberships_no_model_specific_drops"] = (
        "all_split_memberships_no_model_specific_drops"
    )
    selection_role: Literal["validation_only_per_fold"] = "validation_only_per_fold"
    selection_metric: Literal["mae"] = "mae"
    tie_break: Literal["candidate_order"] = "candidate_order"
    required_prediction_coverage: Annotated[float, Field(ge=1, le=1)] = 1.0
    incomplete_candidate: Literal["not_ready_no_selection_no_partial_metrics"] = (
        "not_ready_no_selection_no_partial_metrics"
    )
    portfolio_final_test: Literal["not_included_not_opened"] = "not_included_not_opened"

    @model_validator(mode="after")
    def window(self) -> Self:
        if self.moving_average_minimum_known_days > self.moving_average_calendar_days:
            raise ValueError("baseline_minimum_exceeds_calendar_window")
        return self


class BaselineEstimate(Contract):
    predicted_units: Annotated[float, Field(ge=0)] | None
    history_dates: tuple[date, ...]
    reason: (
        Literal["no_known_history", "insufficient_calendar_window", "no_known_same_weekday"] | None
    )

    @model_validator(mode="after")
    def available(self) -> Self:
        if (self.predicted_units is None) != (self.reason is not None) or (
            self.predicted_units is not None and not self.history_dates
        ):
            raise ValueError("baseline_estimate_availability_mismatch")
        if self.history_dates != tuple(sorted(set(self.history_dates))):
            raise ValueError("baseline_history_dates_not_unique_ordered")
        return self


class BaselinePrediction(ForecastKey):
    fold: str
    role: Literal["train", "validation", "development_holdout", "purged"]
    model: BaselineName
    eligible: bool
    exclusion_reasons: tuple[Reason, ...]
    estimate: BaselineEstimate | None

    @model_validator(mode="after")
    def coverage(self) -> Self:
        # Reuse the exact membership key/reason invariants, without copying label outcomes.
        Membership(
            **self.model_dump(include=set(ForecastKey.model_fields)),
            fold=self.fold,
            role=self.role,
            eligible=self.eligible,
            reasons=self.exclusion_reasons,
            label_content_sha256=None if self.role == "purged" else "0" * 64,
        )
        if self.eligible != (self.estimate is not None):
            raise ValueError("baseline_prediction_eligibility_mismatch")
        if self.estimate is not None and any(
            day > self.forecast_origin.date() for day in self.estimate.history_dates
        ):
            raise ValueError("baseline_prediction_uses_future_history")
        return self


class MetricResult(Contract):
    eligible_rows: Annotated[int, Field(ge=0)]
    predicted_rows: Annotated[int, Field(ge=0)]
    status: Literal["passed", "incomplete", "not_evaluable", "selection_not_ready"]
    mae: Annotated[float, Field(ge=0)] | None
    wape: Annotated[float, Field(ge=0)] | None
    absolute_error_sum: Annotated[float, Field(ge=0)] | None
    absolute_actual_sum: Annotated[float, Field(ge=0)] | None
    wape_status: Literal[
        "passed", "zero_denominator", "incomplete", "no_rows", "selection_not_ready"
    ]

    @model_validator(mode="after")
    def completeness(self) -> Self:
        if self.predicted_rows > self.eligible_rows:
            raise ValueError("metric_prediction_count_exceeds_eligible")
        expected = (
            "not_evaluable"
            if not self.eligible_rows
            else "incomplete"
            if self.predicted_rows < self.eligible_rows
            else "passed"
        )
        if self.status != expected and self.status != "selection_not_ready":
            raise ValueError("metric_status_count_mismatch")
        if self.status != "passed":
            if any(
                v is not None
                for v in (self.mae, self.wape, self.absolute_error_sum, self.absolute_actual_sum)
            ) or self.wape_status != (
                "no_rows"
                if self.status == "not_evaluable"
                else "selection_not_ready"
                if self.status == "selection_not_ready"
                else "incomplete"
            ):
                raise ValueError("partial_baseline_metrics_forbidden")
        else:
            if (
                self.mae is None
                or self.absolute_error_sum is None
                or self.absolute_actual_sum is None
            ):
                raise ValueError("complete_baseline_metric_required")
            if self.mae != self.absolute_error_sum / self.eligible_rows:
                raise ValueError("mae_denominator_mismatch")
            expected_wape = (
                self.absolute_error_sum / self.absolute_actual_sum
                if self.absolute_actual_sum
                else None
            )
            if self.wape != expected_wape or self.wape_status != (
                "passed" if self.absolute_actual_sum else "zero_denominator"
            ):
                raise ValueError("wape_denominator_mismatch")
        return self


class BaselineSelection(Contract):
    fold: str
    status: Literal["selected", "not_ready"]
    model: BaselineName | None
    validation_metrics: dict[BaselineName, MetricResult]
    validation_grain_sha256: Sha256
    # This pin is constructed before calculating development-holdout metrics.
    selection_sha256: Sha256

    @model_validator(mode="after")
    def completeness(self) -> Self:
        if set(self.validation_metrics) != set(BASELINES):
            raise ValueError("baseline_selection_candidates_mismatch")
        complete = all(metric.status == "passed" for metric in self.validation_metrics.values())
        if len({metric.eligible_rows for metric in self.validation_metrics.values()}) != 1:
            raise ValueError("baseline_selection_common_count_mismatch")
        if (self.status == "selected") != complete or (self.model is not None) != complete:
            raise ValueError("baseline_selection_status_mismatch")
        return self


class EvaluationCode(Contract):
    version: Literal["forecast-baselines-1.0.0"] = "forecast-baselines-1.0.0"
    code_files: dict[str, Sha256]
    code_sha256: Sha256
    dependency_lock_sha256: Sha256
    python_version: str
    pyarrow_version: str

    @model_validator(mode="after")
    def digest(self) -> Self:
        if not self.code_files or canonical_sha256(self.code_files) != self.code_sha256:
            raise ValueError("baseline_code_hash_mismatch")
        return self


class EvaluationDescriptor(Contract):
    role: Literal["baseline_evaluation"] = "baseline_evaluation"
    feature_set_id: FeatureID
    split_id: SplitID
    label_dataset_id: LabelID
    requested_policy: BaselinePolicy
    resolved_policy: BaselinePolicy
    code: EvaluationCode
    predictions_content_sha256: Sha256
    prediction_rows: Annotated[int, Field(ge=1)]
    coverage_counts: dict[str, Annotated[int, Field(ge=0)]]
    selections: tuple[BaselineSelection, ...]
    # Fold:role:model -> metrics. No portfolio-final-test role is allowed in predictions.
    metrics: dict[str, MetricResult]
    status: Literal["passed", "not_ready"]

    @model_validator(mode="after")
    def report(self) -> Self:
        folds = tuple(selection.fold for selection in self.selections)
        expected = {
            fold + ":" + role + ":" + model
            for fold in folds
            for role in ("train", "validation", "development_holdout")
            for model in BASELINES
        }
        if not folds or len(set(folds)) != len(folds) or set(self.metrics) != expected:
            raise ValueError("baseline_report_fold_role_model_mismatch")
        for selection in self.selections:
            if any(
                self.metrics[selection.fold + ":validation:" + model] != metric
                for model, metric in selection.validation_metrics.items()
            ):
                raise ValueError("baseline_selection_report_metric_mismatch")
        passed = all(selection.status == "selected" for selection in self.selections) and all(
            metric.status == "passed" for metric in self.metrics.values()
        )
        if (self.status == "passed") != passed:
            raise ValueError("baseline_report_status_mismatch")
        return self


class EvaluationManifest(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    evaluation_id: Annotated[str, Field(pattern=r"^forecast-evaluation-sha256-[0-9a-f]{64}$")]
    descriptor: EvaluationDescriptor
    predictions: TableReceipt
    generated_at: UtcTime
    forecast_model_status: Literal["not_ready"] = "not_ready"

    @model_validator(mode="after")
    def identity(self) -> Self:
        desc = self.descriptor
        if self.evaluation_id != "forecast-evaluation-sha256-" + canonical_sha256(
            desc.model_dump(mode="json")
        ):
            raise ValueError("baseline_evaluation_identity_mismatch")
        if (
            self.predictions.content_sha256 != desc.predictions_content_sha256
            or self.predictions.row_count != desc.prediction_rows
            or desc.requested_policy != desc.resolved_policy
        ):
            raise ValueError("baseline_evaluation_receipt_mismatch")
        return self
