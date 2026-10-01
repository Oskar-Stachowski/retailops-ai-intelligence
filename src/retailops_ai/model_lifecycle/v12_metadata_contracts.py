"""Scoped v12 catalog and retained campaign evaluations; neither attests a live runtime."""

import hashlib
import json
from collections import Counter
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import (
    CommitSha,
    Contract,
    FalseFlag,
    Sha256,
    SourceID,
    Symbol,
    UtcTime,
)
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.forecast_jobs.v12_contracts import RecipeID, RunID
from retailops_ai.model_lifecycle.contracts import Version
from retailops_ai.model_lifecycle.evaluation_contracts import EvaluationFreshness, EvaluationScope
from retailops_ai.model_lifecycle.read_contracts import (
    CatalogFreshness,
    CatalogPagination,
    CatalogScope,
)
from retailops_ai.model_lifecycle.v12_evidence import V12ArtifactReceipt
from retailops_ai.model_lifecycle.v12_lifecycle_contracts import DatabaseReleaseID, ModelName
from retailops_ai.model_lifecycle.v12_release_contracts import ImageDigest

MAX_EVALUATION_BYTES = 8 * 1024**2
EvaluationID = Annotated[str, Field(pattern=r"^v12-evaluation-sha256-[0-9a-f]{64}$")]
Count = Annotated[int, Field(ge=0, le=50_000_000)]
Nonnegative = Annotated[float, Field(ge=0)]


def evaluation_identity(model: str, run: str) -> str:
    return "v12-evaluation-sha256-" + canonical_sha256(
        dict(model_name=model, original_export_run_id=run)
    )


class V12ApprovedRelease(Contract):
    release_id: DatabaseReleaseID
    model_version: Version
    image_digest: ImageDigest


class V12CatalogVersion(Contract):
    model_name: ModelName
    model_version: Version
    status: Literal["approved_release_recorded", "previously_published"]
    flavor: Literal["forecast-functional-v12"] = "forecast-functional-v12"
    original_export_run_id: RunID
    mlflow_run_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    campaign_mlflow_run_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    cohort_id: Symbol
    fold: Symbol
    recipe_id: RecipeID
    recipe_artifact: V12ArtifactReceipt
    source_dataset_id: SourceID
    snapshot_id: Annotated[str, Field(pattern=r"^snapshot-sha256-[0-9a-f]{64}$")]
    code_sha256: Sha256
    dependency_lock_sha256: Sha256
    approval_sha256: Sha256
    runtime_pin_sha256: Sha256
    approval_valid_until: UtcTime
    evaluation_id: EvaluationID
    evaluation_scope: Literal["whole_campaign_scope_required"] = "whole_campaign_scope_required"
    visible_last_published_at: UtcTime
    freshness: CatalogFreshness


class V12CatalogModel(Contract):
    model_name: ModelName
    visible_version_count: Annotated[int, Field(ge=1, le=1000)]
    approved_release: V12ApprovedRelease | None
    registry_aliases: None = None
    deployed_model_version: None = None
    deployment_status: Literal["not_attested"] = "not_attested"
    drift_status: Literal["not_run"] = "not_run"
    freshness: CatalogFreshness
    generated_at: UtcTime


class V12ModelPage(Contract):
    version: Literal["forecast-v12-model-page-1.0.0"] = "forecast-v12-model-page-1.0.0"
    items: tuple[V12CatalogModel, ...] = Field(max_length=200)
    pagination: CatalogPagination
    generated_at: UtcTime
    data_status: Literal["available", "no_data"]
    view_sha256: Sha256


class V12VersionPage(Contract):
    version: Literal["forecast-v12-version-page-1.0.0"] = "forecast-v12-version-page-1.0.0"
    items: tuple[V12CatalogVersion, ...] = Field(max_length=200)
    pagination: CatalogPagination
    generated_at: UtcTime
    data_status: Literal["available", "no_data"]
    view_sha256: Sha256


class V12PointMetrics(Contract):
    complete: bool
    mae: Nonnegative | None
    mse: Nonnegative | None
    bias_units: float | None
    normalized_bias: float | None
    wape: Nonnegative | None
    zero_actual_excess_units: Nonnegative | None


class V12IntervalMetrics(Contract):
    complete: bool
    mean_width: Nonnegative | None
    width_to_mean_actual: Nonnegative | None
    mean_score: Nonnegative | None
    coverage: Annotated[float, Field(ge=0, le=1)] | None


class V12FunctionalMetrics(Contract):
    rows: Count
    actual_sum: Annotated[int, Field(ge=0)]
    median: V12PointMetrics
    mean: V12PointMetrics
    interval: V12IntervalMetrics


class V12CalibrationEvidence(Contract):
    minimum_rows: Count | None
    missing_evidence_rows: Count


class V12MetricDiagnostics(Contract):
    mean_mae_above_legacy_regression_limit: bool | None
    median_bias_above_legacy_mean_limit: bool | None


class V12EvaluationSegment(Contract):
    fold: Symbol
    role: Literal["validation", "development_holdout"]
    dimension: Literal["global", "horizon", "category", "channel", "volume"]
    value: Annotated[str, Field(min_length=1, max_length=256)]
    protocol_version: Literal["forecast-quality-2.0.0"]
    scope: Literal["segment_component_not_campaign_qualification"]
    retained_median_baseline: bool
    eligible_rows: Count
    total_rows: Count
    eligibility_coverage: Annotated[float, Field(ge=0, le=1)] | None
    candidate: V12FunctionalMetrics
    baseline: V12FunctionalMetrics
    relative_median_mae_change: float | None
    perfect_median_baseline_tied: bool
    diagnostics: V12MetricDiagnostics
    legacy_width_ratio_exceeded: bool | None
    status: Literal["passed", "failed", "not_ready"]
    has_measurable_failures: bool
    not_ready_reasons: tuple[Symbol, ...] = Field(max_length=32)
    failed_reasons: tuple[Symbol, ...] = Field(max_length=32)
    calibration_evidence: dict[Literal["candidate", "baseline"], V12CalibrationEvidence]

    @model_validator(mode="after")
    def counts(self) -> Self:
        if (
            self.eligible_rows > self.total_rows
            or self.candidate.rows != self.eligible_rows
            or self.baseline.rows != self.eligible_rows
            or self.candidate.actual_sum != self.baseline.actual_sum
            or set(self.calibration_evidence) != {"candidate", "baseline"}
        ):
            raise ValueError("v12_evaluation_segment_binding")
        return self


class V12CohortLineage(Contract):
    cohort_id: Symbol
    prepared_cohort_id: Annotated[str, Field(pattern=r"^forecast-cohort-sha256-[0-9a-f]{64}$")]
    source_dataset_id: SourceID
    snapshot_id: Annotated[str, Field(pattern=r"^snapshot-sha256-[0-9a-f]{64}$")]


class V12EvaluationDescriptor(Contract):
    model_name: ModelName
    original_export_run_id: RunID
    campaign_id: Annotated[str, Field(pattern=r"^functional-v12-campaign-sha256-[0-9a-f]{64}$")]
    freeze_id: Annotated[str, Field(pattern=r"^functional-v12-freeze-sha256-[0-9a-f]{64}$")]
    replay_id: Annotated[str, Field(pattern=r"^functional-v12-replay-sha256-[0-9a-f]{64}$")]
    exported_at: UtcTime
    scope: EvaluationScope
    cohort_lineage: tuple[V12CohortLineage, ...] = Field(min_length=1, max_length=64)
    membership_rows: Count
    membership_sha256: Sha256
    source_code_commit: CommitSha
    ai_code_commit: CommitSha
    code_sha256: Sha256
    dependency_lock_sha256: Sha256
    export_manifest: V12ArtifactReceipt
    metrics_report: V12ArtifactReceipt
    quality_status: Literal["passed", "not_ready"]
    forecast_model_status: Literal["ready", "not_ready"]
    input_complete: bool
    execution_failure_recorded: bool
    segment_counts: dict[Literal["passed", "failed", "not_ready"], Count]
    failed_reasons: dict[Symbol, Count]
    registered_model_version: None = None
    serving_eligible: FalseFlag = False
    portfolio_final_test: Literal["not_included_not_opened"] = "not_included_not_opened"
    metric_verification: Literal["pinned_original_export_and_replay_verified"] = (
        "pinned_original_export_and_replay_verified"
    )
    quality_verification: Literal["recorded_status_not_requalified"] = (
        "recorded_status_not_requalified"
    )


class V12EvaluationEvidence(Contract):
    version: Literal["forecast-v12-evaluation-evidence-1.0.0"] = (
        "forecast-v12-evaluation-evidence-1.0.0"
    )
    evaluation_id: EvaluationID
    evidence_sha256: Sha256
    descriptor: V12EvaluationDescriptor
    original_metrics: dict[str, Any]

    @model_validator(mode="after")
    def identity(self) -> Self:
        d, m = self.descriptor, self.original_metrics
        segments = tuple(
            V12EvaluationSegment.model_validate_json(json.dumps(s)) for s in m["segments"]
        )
        if (
            not 1 <= len(segments) <= 10000
            or self.evaluation_id != evaluation_identity(d.model_name, d.original_export_run_id)
            or self.evidence_sha256
            != canonical_sha256(self.model_dump(mode="json", exclude={"evidence_sha256"}))
            or hashlib.sha256(canonical_bytes(m) + b"\n").hexdigest() != d.metrics_report.sha256
            or len(canonical_bytes(m) + b"\n") != d.metrics_report.size_bytes
            or m["status"] != d.quality_status
            or m["input_complete"] != d.input_complete
            or (m["execution_failure"] is not None) != d.execution_failure_recorded
            or m["processed_rows"] != d.membership_rows
            or m["segment_counts"] != d.segment_counts
            or m["failed_reasons"] != d.failed_reasons
            or dict(Counter(s.status for s in segments)) != d.segment_counts
            or dict(Counter(r for s in segments for r in s.failed_reasons)) != d.failed_reasons
            or sorted(m["cohorts"]) != [c.cohort_id for c in d.cohort_lineage]
            or len({c.source_dataset_id for c in d.cohort_lineage}) != len(d.cohort_lineage)
            or len({c.snapshot_id for c in d.cohort_lineage}) != len(d.cohort_lineage)
            or d.forecast_model_status != ("ready" if d.quality_status == "passed" else "not_ready")
            or d.portfolio_final_test != m["portfolio_final_test"]
            or len(canonical_bytes(self.model_dump(mode="json"))) > MAX_EVALUATION_BYTES
        ):
            raise ValueError("v12_evaluation_evidence_identity_or_binding")
        return self


class V12EvaluationQuery(CatalogScope):
    quality_status: Literal["passed", "not_ready"] | None = None
    limit: Annotated[int, Field(ge=1, le=200)] = 50
    offset: Annotated[int, Field(ge=0, le=256)] = 0
    view_sha256: Sha256 | None = None


class V12EvaluationSummary(V12EvaluationDescriptor):
    evaluation_id: EvaluationID
    evidence_sha256: Sha256
    freshness: EvaluationFreshness


class V12EvaluationDetail(V12EvaluationSummary):
    segments: tuple[V12EvaluationSegment, ...] = Field(min_length=1, max_length=10000)


class V12EvaluationPage(Contract):
    version: Literal["forecast-v12-evaluation-page-1.0.0"] = "forecast-v12-evaluation-page-1.0.0"
    items: tuple[V12EvaluationSummary, ...] = Field(max_length=200)
    pagination: CatalogPagination
    generated_at: UtcTime
    data_status: Literal["available", "no_data"]
    view_sha256: Sha256
