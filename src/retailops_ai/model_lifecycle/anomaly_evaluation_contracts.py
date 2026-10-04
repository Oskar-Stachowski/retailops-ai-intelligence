"""Public projections of recomputed anomaly quality, without private episode truth."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.anomaly_detectors.contract import DetectorID, Family
from retailops_ai.anomaly_evaluation.quality import QualityPolicy
from retailops_ai.data_contracts.common import Contract, Sha256, SourceID, Symbol, UtcTime
from retailops_ai.model_lifecycle.contracts import Receipt, RunID, Version
from retailops_ai.model_lifecycle.read_contracts import CatalogFreshness
from retailops_ai.source_snapshot.files import json_sha256

QualityID = Annotated[str, Field(pattern=r"^anomaly-quality-sha256-[0-9a-f]{64}$")]
Count = Annotated[int, Field(ge=0, le=100000)]
Rate = Annotated[float, Field(ge=0, le=1)]
Segment = Annotated[str, Field(pattern=r"^(sale_completed|return_completed)[/:](PLN|EUR)$")]


class AnomalyEvaluationScope(Contract):
    product_ids: tuple[Symbol, ...] = Field(min_length=1, max_length=100)
    selling_location_ids: tuple[Symbol, ...] = Field(min_length=1, max_length=100)
    channels: tuple[Literal["store", "online", "marketplace", "wholesale"], ...] = Field(
        min_length=1, max_length=4
    )

    @model_validator(mode="after")
    def canonical(self) -> Self:
        if any(
            values != tuple(sorted(set(values)))
            for values in (self.product_ids, self.selling_location_ids, self.channels)
        ):
            raise ValueError("anomaly_evaluation_scope_order")
        return self


class AnomalyMetrics(Contract):
    precision: Rate | None
    recall: Rate | None
    false_alerts_per_1000: Annotated[float, Field(ge=0, le=1000)] | None
    high_severity_precision: Rate | None
    episode_recall: Rate | None
    evaluable_coverage: Rate | None


class AnomalyEvaluationDescriptor(Contract):
    version: Literal["anomaly-evaluation-read-1.0.0"] = "anomaly-evaluation-read-1.0.0"
    evaluation_id: QualityID
    model_name: Literal["retailops-sales-anomaly"] = "retailops-sales-anomaly"
    registered_model_version: Version
    mlflow_run_id: RunID
    detector_id: DetectorID
    family: Family
    scope: AnomalyEvaluationScope
    source_dataset_ids: tuple[SourceID, ...] = Field(min_length=6, max_length=6)
    quality_status: Literal["passed"] = "passed"
    qualification_scope: Literal["synthetic_ai_07_portfolio_v1"] = "synthetic_ai_07_portfolio_v1"
    metric_verification: Literal["recomputed_from_saved_decisions_and_offline_truth"] = (
        "recomputed_from_saved_decisions_and_offline_truth"
    )
    generated_at: UtcTime
    model_artifact: Receipt
    quality_policy: QualityPolicy
    metrics: AnomalyMetrics
    counts: dict[Symbol, Count] = Field(max_length=32)
    episodes_per_type: dict[Symbol, Count] = Field(max_length=5)
    detected_episodes_per_type: dict[Symbol, Count] = Field(max_length=5)
    positive_observations_per_type: dict[Symbol, Count] = Field(max_length=5)
    per_segment: dict[Segment, dict[Symbol, Count]] = Field(max_length=4)


class AnomalyEvaluationEvidence(Contract):
    evidence_sha256: Sha256
    descriptor: AnomalyEvaluationDescriptor

    @model_validator(mode="after")
    def identity(self) -> Self:
        if self.evidence_sha256 != json_sha256(self.descriptor.model_dump(mode="json")):
            raise ValueError("anomaly_evaluation_evidence_identity")
        return self


class AnomalyEvaluationSummary(Contract):
    evaluation_id: QualityID
    evidence_sha256: Sha256
    model_name: Literal["retailops-sales-anomaly"] = "retailops-sales-anomaly"
    registered_model_version: Version
    mlflow_run_id: RunID
    detector_id: DetectorID
    family: Family
    scope: AnomalyEvaluationScope
    source_dataset_ids: tuple[SourceID, ...] = Field(min_length=6, max_length=6)
    quality_status: Literal["passed"] = "passed"
    qualification_scope: Literal["synthetic_ai_07_portfolio_v1"] = "synthetic_ai_07_portfolio_v1"
    metric_verification: Literal["recomputed_from_saved_decisions_and_offline_truth"] = (
        "recomputed_from_saved_decisions_and_offline_truth"
    )
    generated_at: UtcTime
    freshness: CatalogFreshness


class AnomalyEvaluationDetail(AnomalyEvaluationSummary):
    model_artifact: Receipt
    quality_policy: QualityPolicy
    metrics: AnomalyMetrics
    counts: dict[Symbol, Count] = Field(max_length=32)
    episodes_per_type: dict[Symbol, Count] = Field(max_length=5)
    detected_episodes_per_type: dict[Symbol, Count] = Field(max_length=5)
    positive_observations_per_type: dict[Symbol, Count] = Field(max_length=5)
    per_segment: dict[Segment, dict[Symbol, Count]] = Field(max_length=4)
