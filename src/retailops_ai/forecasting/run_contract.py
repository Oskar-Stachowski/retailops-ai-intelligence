"""AI 04 evidence export, distinct from a training or inference RunRecord."""

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import CommitSha, Contract, RunID, Sha256, UtcTime
from retailops_ai.data_contracts.identity import canonical_sha256


class ArtifactReceipt(Contract):
    size_bytes: Annotated[int, Field(ge=0)]
    sha256: Sha256


class ForecastRunDescriptor(Contract):
    kind: Literal["forecast_evidence_export"] = "forecast_evidence_export"
    task: Literal["daily_observed_sales_forecast"] = "daily_observed_sales_forecast"
    source_dataset_id: str
    snapshot_id: str
    curated_dataset_id: str
    feature_set_id: str
    label_dataset_id: str
    split_id: str
    comparison_id: str
    backtest_id: str
    quality_id: str
    source_code_commit: CommitSha
    ai_code_commit: CommitSha
    dependency_lock_sha256: Sha256
    data_seed: Annotated[int, Field(ge=0)]
    model_seed: Annotated[int, Field(ge=0)]
    quality_status: Literal["passed", "failed", "not_ready"]
    gate_counts: dict[str, Annotated[int, Field(ge=0)]]
    training_run_id: None = None
    training_started_at: None = None
    training_completed_at: None = None
    portfolio_final_test: Literal["not_included_not_opened"] = "not_included_not_opened"

    @model_validator(mode="after")
    def gate(self) -> Self:
        if set(self.gate_counts) != {"passed", "failed", "not_ready"}:
            raise ValueError("run_gate_counts_invalid")
        expected = (
            "not_ready"
            if self.gate_counts["not_ready"]
            else "failed"
            if self.gate_counts["failed"]
            else "passed"
        )
        if self.quality_status != expected:
            raise ValueError("run_gate_status_mismatch")
        return self


class ForecastRunManifest(Contract):
    schema_version: Literal["1.0.0"] = "1.0.0"
    run_id: RunID
    descriptor: ForecastRunDescriptor
    started_at: UtcTime
    completed_at: UtcTime
    run_status: Literal["export_succeeded"] = "export_succeeded"
    training_executed_in_this_run: Literal[False] = False
    forecast_model_status: Literal["not_ready"] = "not_ready"
    registered_model_version: None = None
    serving_alias: None = None
    mlflow_run_id: None = None
    receipts: dict[str, ArtifactReceipt]

    @model_validator(mode="after")
    def identity(self) -> Self:
        expected = "run-" + canonical_sha256(self.descriptor.model_dump(mode="json"))[:32]
        if self.run_id != expected or self.started_at > self.completed_at:
            raise ValueError("run_identity_or_time_mismatch")
        if len(self.receipts) < 5 or len(self.receipts) > 1000:
            raise ValueError("run_receipt_count_invalid")
        return self
