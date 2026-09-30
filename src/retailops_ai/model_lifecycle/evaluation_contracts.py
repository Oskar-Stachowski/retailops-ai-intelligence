"""Immutable scoped projections of verified AI 04 development evaluations."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    Channel,
    CommitSha,
    Contract,
    CuratedID,
    FalseFlag,
    FeatureID,
    LabelID,
    RunID,
    Sha256,
    SourceID,
    SplitID,
    Symbol,
    UtcTime,
)
from retailops_ai.data_contracts.identity import canonical_sha256
from retailops_ai.forecasting.evaluation_contract import MetricResult
from retailops_ai.forecasting.quality_contract import QualityStatus
from retailops_ai.model_lifecycle.contracts import Receipt
from retailops_ai.model_lifecycle.read_contracts import CatalogPagination, CatalogScope

EvaluationID = Annotated[str, Field(pattern=r"^forecast-quality-sha256-[0-9a-f]{64}$")]
EvaluationErrorCode = Literal[
    "evaluation-read-denied",
    "evaluation-scope-invalid",
    "evaluation-scope-limit",
    "evaluation-not-found",
    "evaluation-view-required",
    "evaluation-view-changed",
    "evaluation-read-budget",
    "evaluation-evidence-invalid",
]
METHODS = frozenset(
    {
        "last_observed",
        "moving_average",
        "seasonal_naive7",
        "random_forest",
        "hist_gradient_boosting",
        "validation_selected",
        "validation_baseline",
    }
)


class EvaluationScope(Contract):
    product_ids: tuple[Symbol, ...] = Field(min_length=1, max_length=10000)
    selling_location_ids: tuple[Symbol, ...] = Field(min_length=1, max_length=1000)
    channels: tuple[Channel, ...] = Field(min_length=1, max_length=2)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if any(
            values != tuple(sorted(set(values)))
            for values in (self.product_ids, self.selling_location_ids, self.channels)
        ):
            raise ValueError("evaluation_scope_not_unique_ordered")
        return self


class EvaluationMetric(Contract):
    role: Literal["validation", "development_holdout"]
    method: Literal[
        "last_observed",
        "moving_average",
        "seasonal_naive7",
        "random_forest",
        "hist_gradient_boosting",
        "validation_selected",
        "validation_baseline",
    ]
    total_rows: Annotated[int, Field(ge=0)]
    excluded_rows: Annotated[int, Field(ge=0)]
    point: MetricResult

    @model_validator(mode="after")
    def counts(self) -> Self:
        if self.total_rows != self.excluded_rows + self.point.eligible_rows:
            raise ValueError("evaluation_metric_membership_count_mismatch")
        return self


class EvaluationDescriptor(Contract):
    schema_version: Literal["1.0"] = "1.0"
    purpose: Literal["historical_development_evidence", "synthetic_acceptance_only"]
    evaluation_id: EvaluationID
    original_export_run_id: RunID
    model_name: Literal["retailops-demand-forecast"] = "retailops-demand-forecast"
    registered_model_version: None = None
    serving_eligible: FalseFlag = False
    target_type: Literal["observed_sales_units"] = "observed_sales_units"
    evaluation_use: Literal["development_diagnostics_previously_observed_data"] = (
        "development_diagnostics_previously_observed_data"
    )
    portfolio_final_test: Literal["not_included_not_opened"] = "not_included_not_opened"
    metric_verification: Literal["replayed_from_saved_predictions_and_labels"] = (
        "replayed_from_saved_predictions_and_labels"
    )
    quality_verification: Literal["recorded_status_not_requalified"] = (
        "recorded_status_not_requalified"
    )
    scope: EvaluationScope
    quality_status: QualityStatus
    source_dataset_id: SourceID
    curated_dataset_id: CuratedID
    feature_set_id: FeatureID
    label_dataset_id: LabelID
    split_id: SplitID
    backtest_id: Annotated[str, Field(pattern=r"^forecast-backtest-sha256-[0-9a-f]{64}$")]
    source_code_commit: CommitSha
    ai_code_commit: CommitSha
    dependency_lock_sha256: Sha256
    generated_at: UtcTime
    export_manifest: Receipt
    quality_manifest: Receipt
    segments_report: Receipt
    membership_content_sha256: Sha256
    evaluation_membership_rows: Annotated[int, Field(ge=1, le=10000000)]
    metrics: tuple[EvaluationMetric, ...] = Field(min_length=14, max_length=14)

    @model_validator(mode="after")
    def coverage(self) -> Self:
        order = [(m.role, m.method) for m in self.metrics]
        expected = sorted(
            (role, method) for role in ("validation", "development_holdout") for method in METHODS
        )
        if order != expected:
            raise ValueError("evaluation_global_metric_inventory_mismatch")
        totals = {}
        for role in ("validation", "development_holdout"):
            values = [m for m in self.metrics if m.role == role]
            if len({(m.total_rows, m.excluded_rows, m.point.eligible_rows) for m in values}) != 1:
                raise ValueError("evaluation_common_metric_coverage_mismatch")
            totals[role] = values[0].total_rows
        if sum(totals.values()) != self.evaluation_membership_rows:
            raise ValueError("evaluation_scope_membership_count_mismatch")
        return self


class EvaluationEvidence(Contract):
    evidence_sha256: Sha256
    descriptor: EvaluationDescriptor

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.evidence_sha256 != canonical_sha256(self.descriptor.model_dump(mode="json")):
            raise ValueError("evaluation_evidence_identity_mismatch")
        return self


class EvaluationQuery(CatalogScope):
    quality_status: QualityStatus | None = None
    limit: Annotated[int, Field(ge=1, le=200)] = 50
    offset: Annotated[int, Field(ge=0, le=256)] = 0
    view_sha256: Sha256 | None = None


class EvaluationFreshness(Contract):
    status: Literal["unknown"] = "unknown"
    reason: Literal["historical_evaluation_runtime_not_observed"] = (
        "historical_evaluation_runtime_not_observed"
    )
    evaluated_at: UtcTime


class EvaluationSummary(Contract):
    evaluation_id: EvaluationID
    evidence_sha256: Sha256
    original_export_run_id: RunID
    model_name: Literal["retailops-demand-forecast"] = "retailops-demand-forecast"
    registered_model_version: None = None
    serving_eligible: FalseFlag = False
    purpose: Literal["historical_development_evidence", "synthetic_acceptance_only"]
    target_type: Literal["observed_sales_units"] = "observed_sales_units"
    evaluation_use: Literal["development_diagnostics_previously_observed_data"] = (
        "development_diagnostics_previously_observed_data"
    )
    portfolio_final_test: Literal["not_included_not_opened"] = "not_included_not_opened"
    metric_verification: Literal["replayed_from_saved_predictions_and_labels"] = (
        "replayed_from_saved_predictions_and_labels"
    )
    quality_verification: Literal["recorded_status_not_requalified"] = (
        "recorded_status_not_requalified"
    )
    scope: EvaluationScope
    quality_status: QualityStatus
    source_dataset_id: SourceID
    curated_dataset_id: CuratedID
    feature_set_id: FeatureID
    label_dataset_id: LabelID
    split_id: SplitID
    backtest_id: Annotated[str, Field(pattern=r"^forecast-backtest-sha256-[0-9a-f]{64}$")]
    source_code_commit: CommitSha
    ai_code_commit: CommitSha
    dependency_lock_sha256: Sha256
    generated_at: UtcTime
    freshness: EvaluationFreshness


class EvaluationDetail(EvaluationSummary):
    metrics: tuple[EvaluationMetric, ...] = Field(min_length=14, max_length=14)


class EvaluationPage(Contract):
    schema_version: Literal["1.0"] = "1.0"
    items: tuple[EvaluationSummary, ...] = Field(max_length=200)
    pagination: CatalogPagination
    generated_at: UtcTime
    data_status: Literal["available", "no_data"]
    view_sha256: Sha256
