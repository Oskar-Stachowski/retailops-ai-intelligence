"""Every raw trial/model on every required population before index deletion.

These descriptive raw forecasts have no frozen learned interval calibration.
This collector cannot qualify quality, authorize final access or select a model.
The public phase must verify the source context bundle and retain its full census
before using this internal component or removing a raw prediction index.
"""

from typing import Any

from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastQualityPolicy,
    CampaignForecastTrialPrediction,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_metrics import ForecastMetrics
from retailops_ai.evaluation_campaign.campaign_segment_contract import (
    CampaignForecastKeyContext,
    CampaignForecastSegmentCensus,
)
from retailops_ai.evaluation_campaign.campaign_segments import SegmentCensus, population_ids
from retailops_ai.evaluation_campaign.development_contract import MODELS
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.source_snapshot.files import SnapshotError


class RawTrialCriticalSegments:
    """Bounded statistics for all six forecasts, including empty/excluded groups."""

    def __init__(
        self,
        census: CampaignForecastSegmentCensus,
        *,
        trial_tune_score_operation_id: str,
        quality_policy: CampaignForecastQualityPolicy,
    ) -> None:
        self.census = CampaignForecastSegmentCensus.model_validate_json(census.model_dump_json())
        self.quality_policy = CampaignForecastQualityPolicy.model_validate_json(
            quality_policy.model_dump_json()
        )
        if not trial_tune_score_operation_id:
            raise ValueError("campaign_raw_segment_trial_identity_missing")
        self.trial = trial_tune_score_operation_id
        self.stream = SegmentCensus(self.census.scope, self.census.policy)
        self.models = {
            (p.dimension, p.value): {m: ForecastMetrics() for m in MODELS}
            for p in self.census.populations
        }
        self.failed = self.complete = False

    def add(
        self,
        row: CampaignForecastTrialPrediction,
        actual: int | None,
        context: CampaignForecastKeyContext,
    ) -> None:
        if self.failed or self.complete:
            raise SnapshotError("campaign_raw_segment_stream_unavailable")
        try:
            row = CampaignForecastTrialPrediction.model_validate_json(row.model_dump_json())
            context = CampaignForecastKeyContext.model_validate_json(context.model_dump_json())
            if (
                membership_key(row) != membership_key(context)
                or row.trial_tune_score_operation_id != self.trial
                or row.role != self.census.scope.role
                or row.example_sha256 != context.example_sha256
                or row.eligible != context.eligible
                or row.exclusion_reasons != context.exclusion_reasons
                or row.eligible
                and (type(actual) is not int or actual < 0)
                or not row.eligible
                and actual is not None
            ):
                raise SnapshotError("campaign_raw_segment_context_actual_or_trial_mismatch")
            self.stream.add(context)
            if row.eligible and actual is not None:
                for identity in population_ids(context):
                    for name, forecast in zip(MODELS, row.values, strict=True):
                        self.models[identity][name].add(
                            forecast, actual, self.quality_policy.nominal_coverage
                        )
        except Exception:
            self.failed = True
            raise

    def finish(self) -> dict[str, Any]:
        if self.failed or self.complete:
            raise SnapshotError("campaign_raw_segment_stream_unavailable")
        try:
            consumed = self.stream.finish(
                expected_rows=self.census.rows,
                expected_eligible_rows=self.census.eligible_rows,
                expected_keys_sha256=self.census.keys_sha256,
                expected_eligible_keys_sha256=self.census.eligible_keys_sha256,
            )
            if consumed != self.census:
                raise SnapshotError("campaign_raw_segment_full_context_census_mismatch")
            result = {
                "scope": "full_declared_context_raw_trial_component_not_campaign_qualification",
                "trial_tune_score_operation_id": self.trial,
                "role": self.census.scope.role,
                "context_census_sha256": self.census.content_sha256(),
                "context_scope_sha256": self.census.scope.content_sha256(),
                "quality_policy_sha256": self.quality_policy.content_sha256(),
                "rows": self.census.rows,
                "eligible_rows": self.census.eligible_rows,
                "keys_sha256": self.census.keys_sha256,
                "eligible_keys_sha256": self.census.eligible_keys_sha256,
                "critical_context_inventory_consumed": True,
                "audited_source_context_verified_by_this_component": False,
                "raw_learned_intervals_calibrated": False,
                "architecture_reselected": False,
                "quality_qualified": False,
                "final_access_authorized": False,
                "promotion_allowed": False,
                "stage_ready": False,
                "segments": [
                    {
                        **p.model_dump(mode="json"),
                        "models": {
                            name: value.result()
                            for name, value in self.models[p.dimension, p.value].items()
                        },
                    }
                    for p in self.census.populations
                ],
            }
            self.complete = True
            return result
        except Exception:
            self.failed = True
            raise
