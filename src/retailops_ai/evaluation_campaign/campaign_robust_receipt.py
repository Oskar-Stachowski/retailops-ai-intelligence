"""Versioned complete forecast robustness; a header grants no access or promotion."""

import json
from typing import Literal, Self, TypeGuard

from pydantic import model_validator

from retailops_ai.data_contracts.common import Sha256, TrueFlag
from retailops_ai.evaluation_campaign.campaign_evaluation_receipt import (
    CampaignForecastEvaluationReceipt,
    _CampaignForecastEvaluationFields,
)
from retailops_ai.evaluation_campaign.campaign_portfolio_evaluation_contract import (
    CampaignPortfolioForecastEvaluationBinding,
)
from retailops_ai.evaluation_campaign.campaign_uncertainty_contract import (
    CampaignForecastUncertaintyPolicy,
)
from retailops_ai.source_snapshot.files import SnapshotError


class _CampaignForecastRobustEvaluationFields(_CampaignForecastEvaluationFields):
    context_receipt_sha256: Sha256
    context_census_sha256: Sha256
    context_trace_sha256: Sha256
    uncertainty_policy: CampaignForecastUncertaintyPolicy
    critical_segment_inventory_complete: TrueFlag = True
    block_uncertainty_complete: TrueFlag = True

    @model_validator(mode="after")
    def robustness_policy(self) -> Self:
        if (
            self.uncertainty_policy.content_sha256() != self.plan.uncertainty_policy_sha256
            or self.uncertainty_policy.nominal_interval_coverage
            != self.plan.quality_policy.nominal_coverage
            or self.rows > self.uncertainty_policy.max_rows
        ):
            raise ValueError("campaign_robust_evaluation_policy_or_population_mismatch")
        return self


class CampaignForecastRobustEvaluationReceipt(_CampaignForecastRobustEvaluationFields):
    version: Literal["ai09-campaign-forecast-evaluation-receipt-2.0.0"] = (
        "ai09-campaign-forecast-evaluation-receipt-2.0.0"
    )


class _CampaignForecastPortfolioEvaluationFields(_CampaignForecastEvaluationFields):
    portfolio_binding: CampaignPortfolioForecastEvaluationBinding

    def _development_source_matches(self) -> bool:
        return (
            self.portfolio_binding.matches(self.configuration, self.plan)
            and self.portfolio_binding.evaluation_dataset_id == self.dataset_id
            and self.portfolio_binding.operation_id == self.operation_id
            and self.portfolio_binding.recipe == self.recipe
        )

    def _additional_artifacts(self) -> set[str]:
        return {"portfolio.json"}

    @model_validator(mode="after")
    def development_only(self) -> Self:
        if self.plan.phase != "development":
            raise ValueError("campaign_portfolio_evaluation_requires_development")
        return self


class CampaignForecastPortfolioEvaluationReceipt(_CampaignForecastPortfolioEvaluationFields):
    version: Literal["ai09-campaign-portfolio-forecast-evaluation-receipt-1.0.0"] = (
        "ai09-campaign-portfolio-forecast-evaluation-receipt-1.0.0"
    )


class CampaignForecastPortfolioRobustEvaluationReceipt(
    _CampaignForecastPortfolioEvaluationFields, _CampaignForecastRobustEvaluationFields
):
    version: Literal["ai09-campaign-portfolio-forecast-robust-evaluation-receipt-1.0.0"] = (
        "ai09-campaign-portfolio-forecast-robust-evaluation-receipt-1.0.0"
    )


ForecastEvaluationReceipt = (
    CampaignForecastEvaluationReceipt
    | CampaignForecastRobustEvaluationReceipt
    | CampaignForecastPortfolioEvaluationReceipt
    | CampaignForecastPortfolioRobustEvaluationReceipt
)

ForecastRobustEvaluationReceipt = (
    CampaignForecastRobustEvaluationReceipt | CampaignForecastPortfolioRobustEvaluationReceipt
)


def is_robust_evaluation(
    receipt: ForecastEvaluationReceipt,
) -> TypeGuard[ForecastRobustEvaluationReceipt]:
    return isinstance(
        receipt,
        (CampaignForecastRobustEvaluationReceipt, CampaignForecastPortfolioRobustEvaluationReceipt),
    )


def parse_forecast_evaluation_receipt(raw: bytes) -> ForecastEvaluationReceipt:
    """Only concrete known versions; a flag cannot upgrade the component receipt."""
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise SnapshotError("campaign_evaluation_receipt_version_invalid")
    if value.get("version") == "ai09-campaign-forecast-evaluation-receipt-1.0.0":
        return CampaignForecastEvaluationReceipt.model_validate_json(raw)
    if value.get("version") == "ai09-campaign-forecast-evaluation-receipt-2.0.0":
        return CampaignForecastRobustEvaluationReceipt.model_validate_json(raw)
    if value.get("version") == "ai09-campaign-portfolio-forecast-evaluation-receipt-1.0.0":
        return CampaignForecastPortfolioEvaluationReceipt.model_validate_json(raw)
    if value.get("version") == "ai09-campaign-portfolio-forecast-robust-evaluation-receipt-1.0.0":
        return CampaignForecastPortfolioRobustEvaluationReceipt.model_validate_json(raw)
    raise SnapshotError("campaign_evaluation_receipt_version_invalid")
