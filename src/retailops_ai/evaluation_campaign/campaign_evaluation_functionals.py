"""Join the frozen mean/median across trials; apply fixed calibration without outcomes."""

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign.campaign_calibration_data import interval
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPrediction,
    CampaignForecastFrozenConfiguration,
    CampaignForecastReference,
    CampaignForecastTrialPrediction,
)
from retailops_ai.evaluation_campaign.development_contract import MODELS
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.forecasting.quality_v2_contract import FunctionalForecast
from retailops_ai.source_snapshot.files import SnapshotError


class FrozenForecastComposer:
    """Validate/hash the frozen configuration once, then join bounded per-key trial records."""

    def __init__(self, configuration: CampaignForecastFrozenConfiguration) -> None:
        self.configuration = CampaignForecastFrozenConfiguration.model_validate_json(
            canonical_bytes(configuration.model_dump(mode="json"))
        )
        self.configuration_sha = self.configuration.content_sha256()
        self.expected = tuple(t.tune_score_operation_id for t in self.configuration.trials)
        self.selection = self.configuration.calibration.selection

    def compose(
        self, trials: tuple[CampaignForecastTrialPrediction, ...]
    ) -> CampaignForecastEvaluationPrediction:
        """One common key from every preregistered trial; no outcome-dependent reselection."""
        if tuple(t.trial_tune_score_operation_id for t in trials) != self.expected:
            raise SnapshotError("campaign_evaluation_frozen_prediction_trial_inventory_mismatch")
        first = trials[0]
        identity = (
            membership_key(first),
            first.role,
            first.example_sha256,
            first.eligible,
            first.exclusion_reasons,
        )
        if any(
            (membership_key(t), t.role, t.example_sha256, t.eligible, t.exclusion_reasons)
            != identity
            or t.values[:3] != first.values[:3]
            for t in trials
        ):
            raise SnapshotError(
                "campaign_evaluation_prediction_key_eligibility_or_baselines_differ"
            )
        selection = self.selection
        if selection.mean is None or selection.median is None:
            raise SnapshotError("campaign_evaluation_selected_functional_missing")
        by_id = {t.trial_tune_score_operation_id: t for t in trials}

        def chosen(head: str) -> float | None:
            choice = selection.mean if head == "mean" else selection.median
            if choice is None:
                raise SnapshotError("campaign_evaluation_selected_functional_missing")
            row = by_id[choice.score_operation_id or self.expected[0]]
            value: float | None = getattr(row.values[MODELS.index(choice.model)], head)
            return value

        mean, median = chosen("mean"), chosen("median")
        if (
            selection.baseline_mean is None
            or selection.baseline_median is None
            or selection.baseline_interval is None
        ):
            raise SnapshotError("campaign_evaluation_selected_reference_missing")
        reference_mean = first.values[MODELS.index(selection.baseline_mean)].mean
        reference_median = first.values[MODELS.index(selection.baseline_median)].median
        reference_band = first.values[MODELS.index(selection.baseline_interval)]
        return CampaignForecastEvaluationPrediction(
            **first.model_dump(
                include=set(CampaignForecastTrialPrediction.model_fields)
                - {"trial_tune_score_operation_id", "values"}
            ),
            frozen_configuration_sha256=self.configuration_sha,
            candidate=FunctionalForecast(
                mean=mean,
                median=median,
                interval=interval(self.configuration.calibration, first.horizon_days, median)
                if median is not None
                else None,
            ),
            reference=CampaignForecastReference(
                mean=reference_mean,
                median=reference_median,
                interval=reference_band.interval,
                interval_center=reference_band.median
                if reference_band.interval is not None
                else None,
            ),
        )
