"""Read-only, scope-safe saved anomaly results, with explicit unknown values."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.anomaly_detectors.protocol import EventType
from retailops_ai.anomaly_evaluation.contract import Decision
from retailops_ai.data_contracts.common import (
    Contract,
    CuratedID,
    Sha256,
    SourceID,
    Symbol,
    UtcTime,
)
from retailops_ai.model_lifecycle.contracts import Version

BatchID = Annotated[str, Field(pattern=r"^anomaly-batch-sha256-[0-9a-f]{64}$")]
AnomalyID = Annotated[str, Field(pattern=r"^anomaly-sha256-[0-9a-f]{64}$")]
FeatureID = Annotated[str, Field(pattern=r"^qualified-anomaly-inputs-sha256-[0-9a-f]{64}$")]
ReleaseID = Annotated[str, Field(pattern=r"^anomaly-release-sha256-[0-9a-f]{64}$")]
EpisodeID = Annotated[str, Field(pattern=r"^signal-episode-sha256-[0-9a-f]{64}$")]
ErrorCode = Literal[
    "anomaly-scope-denied",
    "anomaly-pipeline-denied",
    "anomaly-idempotency-conflict",
    "anomaly-read-budget",
    "anomaly-view-changed",
    "anomaly-not-found",
    "anomaly-output-unavailable",
]


class Item(Decision):
    anomaly_id: AnomalyID
    batch_id: BatchID
    signal_episode_id: EpisodeID | None
    detector_name: Literal["retailops-sales-anomaly"] = "retailops-sales-anomaly"
    detector_version: Version
    release_id: ReleaseID
    threshold_version: Literal["validation-alert-capacity-1.0.0"] = (
        "validation-alert-capacity-1.0.0"
    )
    source_dataset_id: SourceID
    curated_dataset_id: CuratedID
    qualified_anomaly_input_id: FeatureID
    full_dq_replay_id: Annotated[str, Field(pattern=r"^full-dq-replay-sha256-[0-9a-f]{64}$")]
    generated_at: UtcTime
    as_of: UtcTime
    quality_status: Literal["passed_at_publication"] = "passed_at_publication"
    freshness_status: Literal["current", "stale", "unknown"]
    anomaly_type: (
        Literal[
            "sales_spike",
            "sales_drop",
            "residual_outlier",
            "return_spike",
            "stockout_censored_demand",
            "data_quality_suspicion",
        ]
        | None
    )
    alert_status: Literal["open", "no_alert", "insufficient_data"]
    score_definition: Literal[
        "absolute_causal_standardized_residual", "portable_isolation_forest_path_length"
    ]

    @model_validator(mode="after")
    def outcome(self) -> Self:
        if self.scoring_origin > self.as_of or (self.alert is True) != (
            self.signal_episode_id is not None
        ):
            raise ValueError("anomaly_result_cutoff_or_episode")
        expected = (
            "insufficient_data"
            if self.status == "insufficient_data"
            else "open"
            if self.alert
            else "no_alert"
        )
        if self.alert_status != expected:
            raise ValueError("anomaly_result_alert_status")
        return self


class Query(Contract):
    product_id: Symbol | None = None
    selling_location_id: Symbol | None = None
    channel: Literal["store", "online", "marketplace", "wholesale"] | None = None
    event_type: EventType | None = None
    business_from: date | None = None
    business_to: date | None = None
    batch_id: BatchID | None = None
    anomaly_id: AnomalyID | None = None
    status: Literal["scored", "insufficient_data"] | None = None
    severity: Literal["none", "medium", "high"] | None = None
    limit: Annotated[int, Field(ge=1, le=200)] = 50
    offset: Annotated[int, Field(ge=0, le=10000)] = 0
    view_sha256: Sha256 | None = None

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if (
            (self.business_from is None) != (self.business_to is None)
            or (
                self.business_from is not None
                and self.business_to is not None
                and not 0 <= (self.business_to - self.business_from).days <= 127
            )
            or (self.offset > 0 and self.view_sha256 is None)
        ):
            raise ValueError("anomaly_read_window_or_page_pin")
        return self


class Pagination(Contract):
    limit: Annotated[int, Field(ge=1, le=200)]
    offset: Annotated[int, Field(ge=0, le=10000)]
    total: Annotated[int, Field(ge=0, le=10000)]


class Page(Contract):
    version: Literal["anomaly-read-page-1.0.0"] = "anomaly-read-page-1.0.0"
    items: tuple[Item, ...] = Field(max_length=200)
    pagination: Pagination
    generated_at: UtcTime
    view_sha256: Sha256
    selection: Literal["latest_complete_batch", "pinned_batch", "anomaly_id"]
    data_status: Literal["available", "no_data"]
    episode_policy: Literal[
        "consecutive_calendar_alerts_same_scope_direction_release_no_unknown_gap"
    ] = "consecutive_calendar_alerts_same_scope_direction_release_no_unknown_gap"
    freshness_policy: Literal[
        "scoring_origin_older_than_7_days_is_stale_unknown_remains_unknown"
    ] = "scoring_origin_older_than_7_days_is_stale_unknown_remains_unknown"
