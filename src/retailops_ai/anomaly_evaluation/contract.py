"""Frozen observation/episode semantics; unknown truth is never a clean negative."""

from datetime import date, timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.anomaly_detectors.contract import DetectorID, Family
from retailops_ai.anomaly_detectors.protocol import Scope, Window, series_key
from retailops_ai.data_contracts.common import Contract, Sha256, SourceID, Symbol, UtcTime

BusinessType = Literal[
    "one_day_spike",
    "multi_day_spike",
    "sustained_drop",
    "return_spike",
    "inventory_censored_episode",
]


class Decision(Scope):
    """Saved portfolio decision; truth, injection parameters and training are absent."""

    version: Literal["anomaly-portfolio-decision-1.0.0"] = "anomaly-portfolio-decision-1.0.0"
    business_date: date
    scoring_origin: UtcTime
    detector_id: DetectorID
    family: Family
    role: Literal["validation", "final_test", "batch"]
    status: Literal["scored", "insufficient_data"]
    score: Annotated[float, Field(ge=0)] | None
    threshold: Annotated[float, Field(ge=0)] | None
    alert: bool | None
    severity: Literal["none", "medium", "high"] | None
    explanation_codes: tuple[str, ...]
    observed_units: Annotated[int, Field(ge=0)] | None
    expected_units: Annotated[int, Field(ge=0)] | None
    residual_units: int | None
    promotion_offered: bool | None
    on_hand: Annotated[int, Field(ge=0)] | None
    input_status: Literal[
        "ready_input", "insufficient_history", "day_unqualified", "no_declaration"
    ]

    @model_validator(mode="after")
    def valid(self) -> Self:
        if self.scoring_origin.date() <= self.business_date:
            raise ValueError("anomaly_scoring_before_closed_day")
        if self.status == "scored":
            if (
                self.input_status != "ready_input"
                or self.score is None
                or self.threshold is None
                or self.alert != (self.score > self.threshold)
                or self.severity is None
                or (self.severity == "none") != (self.alert is False)
                or self.observed_units is None
                or self.expected_units is None
                or self.residual_units != self.observed_units - self.expected_units
            ):
                raise ValueError("anomaly_portfolio_decision_invalid")
        elif any(v is not None for v in (self.score, self.threshold, self.alert, self.severity)):
            raise ValueError("anomaly_insufficient_input_has_decision")
        return self


class EvaluationPolicy(Contract):
    version: Literal["anomaly-evaluation-1.0.0"] = "anomaly-evaluation-1.0.0"
    early_lead_days: Literal[0] = 0
    late_tolerance_days: Literal[1] = 1
    overlap: Literal["reject_truth_overlap_then_earliest_end_id_for_tolerance"] = (
        "reject_truth_overlap_then_earliest_end_id_for_tolerance"
    )
    repeat_alerts: Literal["report_without_increasing_episode_recall"] = (
        "report_without_increasing_episode_recall"
    )
    false_alert_denominator: Literal["scored_mature_truth_clean_observations"] = (
        "scored_mature_truth_clean_observations"
    )
    average_precision: Literal["score_ties_grouped_stepwise_average_precision"] = (
        "score_ties_grouped_stepwise_average_precision"
    )
    zero_prediction_precision: Literal["not_evaluable"] = "not_evaluable"
    unknown_truth: Literal["excluded_and_counted"] = "excluded_and_counted"
    episode_coverage: Literal["all_mature_episodes_in_complete_requested_census"] = (
        "all_mature_episodes_in_complete_requested_census"
    )


class TruthWindow(Scope):
    window: Window
    available_at: UtcTime


class Episode(Scope):
    episode_id: Symbol
    business_type: BusinessType
    window: Window
    first_evidence_available_at: UtcTime
    label_available_at: UtcTime

    @model_validator(mode="after")
    def clock(self) -> Self:
        if self.label_available_at < self.first_evidence_available_at:
            raise ValueError("anomaly_episode_label_before_evidence")
        if (self.window.end - self.window.start).days > 729:
            raise ValueError("anomaly_episode_window_budget")
        return self


class Truth(Contract):
    version: Literal["anomaly-business-truth-1.0.0"] = "anomaly-business-truth-1.0.0"
    data_class: Literal["simulation_truth"] = "simulation_truth"
    source_dataset_id: SourceID
    source_scenario_sha256: Sha256
    complete_windows: tuple[TruthWindow, ...] = Field(min_length=1, max_length=10000)
    episodes: tuple[Episode, ...] = Field(max_length=10000)

    @model_validator(mode="after")
    def inventory(self) -> Self:
        if sum((w.window.end - w.window.start).days + 1 for w in self.complete_windows) > 1000000:
            raise ValueError("anomaly_truth_census_budget")
        if len({e.episode_id for e in self.episodes}) != len(self.episodes):
            raise ValueError("anomaly_duplicate_episode_id")
        windows: dict[tuple[str, ...], list[Window]] = {}
        for item in self.complete_windows:
            scope = series_key(item)
            if any(
                not (item.window.end < w.start or w.end < item.window.start)
                for w in windows.get(scope, [])
            ):
                raise ValueError("anomaly_truth_census_overlap")
            windows.setdefault(scope, []).append(item.window)
        occupied: dict[tuple[str, ...], list[Window]] = {}
        for episode in self.episodes:
            scope = series_key(episode)
            if not any(
                w.start <= episode.window.start <= episode.window.end <= w.end
                for w in windows.get(scope, [])
            ):
                raise ValueError("anomaly_episode_outside_truth_census")
            if any(
                not (episode.window.end < w.start or w.end < episode.window.start)
                for w in occupied.get(scope, [])
            ):
                raise ValueError("anomaly_business_truth_overlap")
            occupied.setdefault(scope, []).append(episode.window)
        return self


class Metric(Contract):
    value: Annotated[float, Field(allow_inf_nan=False)] | None
    unit: Literal["ratio", "alerts_per_1000_clean_observations", "days", "seconds"]
    status: Literal["evaluable", "not_evaluable"]
    n_total: Annotated[int, Field(ge=0)]
    n_evaluable: Annotated[int, Field(ge=0)]
    coverage: Annotated[float, Field(ge=0, le=1)]
    numerator: Annotated[int, Field(ge=0)] | None = None
    denominator: Annotated[int, Field(ge=0)] | None = None
    reason: str | None

    @model_validator(mode="after")
    def validity(self) -> Self:
        if (
            (self.status == "evaluable") != (self.value is not None)
            or self.n_evaluable > self.n_total
            or self.coverage != (self.n_evaluable / self.n_total if self.n_total else 0)
            or (self.status == "not_evaluable" and not self.reason)
        ):
            raise ValueError("anomaly_metric_validity")
        return self


def dates(window: Window) -> tuple[date, ...]:
    if (window.end - window.start).days > 2000:
        raise ValueError("anomaly_evaluation_window_budget")
    return tuple(
        window.start + timedelta(days=i) for i in range((window.end - window.start).days + 1)
    )
