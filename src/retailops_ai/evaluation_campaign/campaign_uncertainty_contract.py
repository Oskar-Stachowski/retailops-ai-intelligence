"""Preregistered paired cluster diagnostics; no access or promotion permission."""

from datetime import date
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import EvaluationRole

Method = Literal["time_block", "series_cluster"]
Metric = Literal[
    "median_mae_delta",
    "median_wape_delta",
    "relative_median_mae_change",
    "mean_mse_delta",
    "normalized_mean_bias_delta",
    "interval_score_delta",
    "interval_coverage_delta",
]
METRICS: tuple[Metric, ...] = (
    "median_mae_delta",
    "median_wape_delta",
    "relative_median_mae_change",
    "mean_mse_delta",
    "normalized_mean_bias_delta",
    "interval_score_delta",
    "interval_coverage_delta",
)


class CampaignForecastUncertaintyPolicy(Contract):
    version: Literal["ai09-paired-cluster-uncertainty-1.0.0"] = (
        "ai09-paired-cluster-uncertainty-1.0.0"
    )
    methods: tuple[Literal["time_block"], Literal["series_cluster"]] = (
        "time_block",
        "series_cluster",
    )
    time_block_days: Annotated[int, Field(ge=14, le=365)] = 28
    time_block_anchor: date = date(2000, 1, 3)
    time_basis: Literal["forecast_origin_utc_calendar_day"] = "forecast_origin_utc_calendar_day"
    time_blocks: Literal["fixed_disjoint_calendar_blocks_all_series_jointly"] = (
        "fixed_disjoint_calendar_blocks_all_series_jointly"
    )
    series_clusters: Literal["whole_product_selling_location_channel_all_origins_and_horizons"] = (
        "whole_product_selling_location_channel_all_origins_and_horizons"
    )
    pairing: Literal["same_cluster_multiplicities_for_candidate_reference_and_actual"] = (
        "same_cluster_multiplicities_for_candidate_reference_and_actual"
    )
    partial_boundary_blocks: Literal["retain_full_membership_never_drop_rows"] = (
        "retain_full_membership_never_drop_rows"
    )
    resamples: Annotated[int, Field(ge=199, le=9999)] = 999
    resampling_seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 137
    random_generator: Literal["numpy_PCG64"] = "numpy_PCG64"
    confidence_level: Annotated[float, Field(ge=0.80, le=0.99)] = 0.95
    quantile_method: Literal["linear_percentile"] = "linear_percentile"
    minimum_eligible_time_blocks: Annotated[int, Field(ge=2, le=1000)] = 8
    minimum_eligible_series_clusters: Annotated[int, Field(ge=2, le=10000)] = 30
    nominal_interval_coverage: Annotated[float, Field(ge=0.90, le=0.90)] = 0.90
    max_rows: Annotated[int, Field(ge=1, le=100000000)] = 10000000
    max_cluster_cells: Annotated[int, Field(ge=2, le=1000000)] = 100000
    max_index_bytes: Annotated[int, Field(ge=4096, le=8 * 1024**3)] = 1024**3
    max_resampled_cluster_visits: Annotated[int, Field(ge=398, le=100000000)] = 10000000
    assumptions: tuple[
        Literal["time_blocks_approximate_weak_dependence_no_stationarity_guarantee"],
        Literal["series_clusters_assume_between_cluster_independence"],
        Literal["methods_are_separate_sensitivity_reports_not_joint_multiway_inference"],
    ] = (
        "time_blocks_approximate_weak_dependence_no_stationarity_guarantee",
        "series_clusters_assume_between_cluster_independence",
        "methods_are_separate_sensitivity_reports_not_joint_multiway_inference",
    )
    nominal_coverage_guaranteed: FalseFlag = False
    independent_row_resampling: FalseFlag = False
    final_access_authorized: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)


class CampaignUncertaintyScope(Contract):
    data_seed: Literal[42, 137, 2026]
    role: EvaluationRole
    dataset_id: Annotated[
        str, Field(pattern=r"^ai09-(physical|final)-forecast-sha256-[0-9a-f]{64}$")
    ]
    source_recipe_sha256: Sha256
    frozen_configuration_sha256: Sha256
    scenario: Literal["normal", "promotion", "demand_shock", "inventory_constraint", "all"]
    dimension: Literal[
        "global",
        "horizon",
        "category",
        "channel",
        "volume",
        "scenario",
        "history",
        "availability",
        "inventory",
        "lead_time",
        "intermittency",
    ]
    value: Annotated[str, Field(min_length=1, max_length=256)]

    @model_validator(mode="after")
    def phase(self) -> Self:
        if (self.role == "final_test") != self.dataset_id.startswith("ai09-final-") or (
            self.role == "development_evaluation" and self.data_seed != 42
        ):
            raise ValueError("campaign_uncertainty_dataset_role_or_seed_mismatch")
        return self

    def content_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


class CampaignPairedConfidenceMetric(Contract):
    status: Literal["evaluated", "not_evaluable"]
    point_delta: float | None
    lower: float | None
    upper: float | None
    valid_replicates: Annotated[int, Field(ge=0)]
    invalid_replicates: Annotated[int, Field(ge=0)]
    reasons: tuple[str, ...]

    @model_validator(mode="after")
    def interval(self) -> Self:
        evaluated = self.status == "evaluated"
        if (
            evaluated != (self.lower is not None and self.upper is not None)
            or evaluated
            and (
                self.point_delta is None
                or self.valid_replicates == 0
                or self.invalid_replicates
                or self.reasons
            )
            or not evaluated
            and (self.lower is not None or self.upper is not None or not self.reasons)
            or self.lower is not None
            and self.upper is not None
            and self.lower > self.upper
        ):
            raise ValueError("campaign_uncertainty_interval_status_mismatch")
        return self


class CampaignPairedMethodReport(Contract):
    method: Method
    clusters: Annotated[int, Field(ge=0)]
    eligible_clusters: Annotated[int, Field(ge=0)]
    rows: Annotated[int, Field(ge=0)]
    eligible_rows: Annotated[int, Field(ge=0)]
    actual_units: Annotated[int, Field(ge=0)]
    cluster_inventory_sha256: Sha256
    derived_resampling_seed: Annotated[int, Field(ge=0, le=2**128 - 1)]
    resampling_trace_sha256: Sha256
    resamples_executed: Annotated[int, Field(ge=0)]
    status: Literal["evaluated", "not_evaluable"]
    metrics: dict[Metric, CampaignPairedConfidenceMetric]

    @model_validator(mode="after")
    def inventory(self) -> Self:
        if (
            set(self.metrics) != set(METRICS)
            or self.eligible_clusters > self.clusters
            or self.eligible_rows > self.rows
            or self.status
            != (
                "evaluated"
                if all(m.status == "evaluated" for m in self.metrics.values())
                else "not_evaluable"
            )
            or any(
                m.valid_replicates + m.invalid_replicates != self.resamples_executed
                for m in self.metrics.values()
            )
        ):
            raise ValueError("campaign_uncertainty_method_inventory_mismatch")
        return self


class CampaignForecastUncertaintyReport(Contract):
    version: Literal["ai09-paired-cluster-uncertainty-report-1.0.0"] = (
        "ai09-paired-cluster-uncertainty-report-1.0.0"
    )
    scope: CampaignUncertaintyScope
    policy: CampaignForecastUncertaintyPolicy
    policy_sha256: Sha256
    rows: Annotated[int, Field(ge=0)]
    eligible_rows: Annotated[int, Field(ge=0)]
    keys_sha256: Sha256
    eligible_keys_sha256: Sha256
    paired_input_trace_sha256: Sha256
    methods: tuple[CampaignPairedMethodReport, CampaignPairedMethodReport]
    all_scope_keys_consumed: Literal[True] = True
    independent_rows_or_seeds_pooled: FalseFlag = False
    final_access_authorized: FalseFlag = False
    quality_qualified: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def complete(self) -> Self:
        if (
            self.policy.content_sha256() != self.policy_sha256
            or tuple(m.method for m in self.methods) != self.policy.methods
            or self.rows > self.policy.max_rows
            or self.eligible_rows > self.rows
            or any(
                (m.rows, m.eligible_rows) != (self.rows, self.eligible_rows) for m in self.methods
            )
            or self.methods[0].actual_units != self.methods[1].actual_units
            or any(m.resamples_executed not in (0, self.policy.resamples) for m in self.methods)
            or sum(m.clusters * m.resamples_executed for m in self.methods)
            > self.policy.max_resampled_cluster_visits
            or sum(m.clusters for m in self.methods) > self.policy.max_cluster_cells
        ):
            raise ValueError("campaign_uncertainty_report_population_or_policy_mismatch")
        return self

    def content_sha256(self) -> str:
        value = self.model_dump(mode="json")
        type(self).model_validate_json(canonical_bytes(value))
        return canonical_sha256(value)
