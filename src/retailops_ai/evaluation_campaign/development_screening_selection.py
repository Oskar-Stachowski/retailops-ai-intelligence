"""Rank each preregistered family on complete paired Tune diagnostics.

This is the deterministic decision used after native paired replay. It does
not authenticate a journal or authorize another fit, final access or release.
The executor must bind all receipts, original recipes and charged costs.
"""

import math
from typing import Annotated, Any, Literal, Self

from pydantic import Field, model_validator

from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, Symbol
from retailops_ai.evaluation_campaign.campaign_fit_contract import Family
from retailops_ai.evaluation_campaign.campaign_score_contract import (
    FAMILIES,
    CampaignForecastScoreReceipt,
)
from retailops_ai.evaluation_campaign.campaign_tune_contract import (
    CampaignForecastTunePlan,
    CampaignForecastTuneSelection,
)
from retailops_ai.evaluation_campaign.campaign_tune_data import choose
from retailops_ai.source_snapshot.files import SnapshotError


class ScreeningTrialRank(Contract):
    family: Family
    frozen_trial_index: Literal[0, 1]
    score_operation_id: Symbol
    fit_operation_id: Symbol
    score_receipt_sha256: Sha256
    model_artifact_sha256: Sha256
    mean_squared_error: Annotated[float, Field(ge=0)] | None
    normalized_mean_bias: float | None
    eligible: bool
    reasons: tuple[str, ...]

    def rank_key(self) -> tuple[float, int]:
        if not self.eligible or self.mean_squared_error is None:
            raise ValueError("screening_ineligible_trial_cannot_be_ranked")
        return self.mean_squared_error, self.frozen_trial_index

    @model_validator(mode="after")
    def eligible_trial(self) -> Self:
        if self.eligible != (not self.reasons) or (
            self.eligible and self.mean_squared_error is None
        ):
            raise ValueError("screening_trial_rank_eligibility_mismatch")
        return self


class ScreeningFamilyRanking(Contract):
    version: Literal["ai09-screening-family-ranking-1.0.0"] = "ai09-screening-family-ranking-1.0.0"
    status: Literal["three_families_ranked", "not_ready"]
    rows: Annotated[int, Field(ge=1)]
    eligible_rows: Annotated[int, Field(ge=0)]
    trials: Annotated[tuple[ScreeningTrialRank, ...], Field(min_length=6, max_length=6)]
    winners: dict[Family, ScreeningTrialRank | None]
    reasons: tuple[str, ...]
    global_tune_diagnostics: CampaignForecastTuneSelection
    rule: Literal["per_family_mean_mse_bias_coverage_then_frozen_trial_order"] = (
        "per_family_mean_mse_bias_coverage_then_frozen_trial_order"
    )
    advancement_authorized: FalseFlag = False
    final_test_accessed: FalseFlag = False
    quality_qualified: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def complete_ranking(self) -> Self:
        if (
            set(self.winners) != set(FAMILIES)
            or self.eligible_rows > self.rows
            or tuple((t.family, t.frozen_trial_index) for t in self.trials)
            != tuple((f, i) for f in FAMILIES for i in (0, 1))
            or len({t.fit_operation_id for t in self.trials}) != 6
            or (self.rows, self.eligible_rows)
            != (self.global_tune_diagnostics.rows, self.global_tune_diagnostics.eligible_rows)
        ):
            raise ValueError("screening_complete_family_trial_inventory_required")
        for family in FAMILIES:
            valid = [t for t in self.trials if t.family == family and t.eligible]
            expected = min(valid, key=ScreeningTrialRank.rank_key) if valid else None
            if self.winners[family] != expected:
                raise ValueError("screening_winner_must_follow_frozen_ranking")
        expected_reasons = (
            *self.global_tune_diagnostics.reasons,
            *("no_valid_" + f + "_trial" for f in FAMILIES if self.winners[f] is None),
        )
        if self.reasons != expected_reasons or (self.status == "three_families_ranked") != (
            not self.reasons
        ):
            raise ValueError("screening_family_status_or_reason_mismatch")
        return self


def _number(value: Any, *, nonnegative: bool = False) -> float | None:
    if value is None:
        return None
    if type(value) not in (float, int) or not math.isfinite(value) or (nonnegative and value < 0):
        raise SnapshotError("screening_nonfinite_or_invalid_mean_metric")
    return float(value)


def rank_screening_families(
    trials: list[dict[str, Any]],
    scores: dict[str, CampaignForecastScoreReceipt],
    plan: CampaignForecastTunePlan,
) -> ScreeningFamilyRanking:
    """Retain all six candidates, including bias/coverage rejections and ties.

    Input metrics must come from the existing full paired Tune replay. Receipt
    declarations alone do not prove that replay; this pure function grants no
    execution authority. A family may advance despite losing to the baseline:
    larger-scale comparison and independent qualification are separate gates.
    """
    plan = CampaignForecastTunePlan.model_validate_json(plan.model_dump_json())
    scores = {
        key: CampaignForecastScoreReceipt.model_validate_json(value.model_dump_json())
        for key, value in scores.items()
    }
    if (
        len(plan.score_operation_ids) != 2
        or set(scores) != set(plan.score_operation_ids)
        or [t["score_operation_id"] for t in trials] != list(plan.score_operation_ids)
    ):
        raise SnapshotError("screening_requires_two_original_ordered_tune_trios")
    population = (
        "rows",
        "eligible_rows",
        "keys_sha256",
        "eligible_keys_sha256",
        "role_population_sha256",
    )
    identity = set()
    fits: list[str] = []
    points = {}
    for index, trial in enumerate(trials):
        score = scores[plan.score_operation_ids[index]]
        if (
            score.operation_id != plan.score_operation_ids[index]
            or score.plan.role != "tune"
            or score.plan.source_recipe_sha256 != plan.source_recipe_sha256
            or score.plan.export_operation_id != plan.export_operation_id
            or score.plan.worker_environment_lock_sha256 != plan.worker_environment_lock_sha256
            or score.rows > plan.max_rows
            or any(trial[k] != getattr(score, k) for k in population)
        ):
            raise SnapshotError("screening_original_score_or_population_binding")
        identity.add(
            (
                score.protocol_sha256,
                score.export_receipt_sha256,
                score.dataset_id,
                score.runtime_code_sha256,
            )
        )
        fits.extend(score.plan.fit_operation_ids.values())
        segments = trial["metrics"]["segments"]
        if not segments or segments[0]["horizon"] != "all":
            raise SnapshotError("screening_missing_full_population_metrics")
        for family in FAMILIES:
            point = segments[0]["models"]["rf_mean" if family == "rf" else family]["mean"]
            mse = _number(point["mse"], nonnegative=True)
            bias = _number(point["normalized_bias"])
            if type(point["complete"]) is not bool or type(point["predicted_rows"]) is not int:
                raise SnapshotError("screening_invalid_mean_completeness")
            if (
                point["complete"]
                != (score.eligible_rows > 0 and point["predicted_rows"] == score.eligible_rows)
                or not 0 <= point["predicted_rows"] <= score.eligible_rows
            ):
                raise SnapshotError("screening_mean_prediction_population_mismatch")
            points[family, index] = (point["complete"], mse, bias)
    if len(identity) != 1 or len(fits) != len(set(fits)):
        raise SnapshotError("screening_original_trial_identity_or_fit_inventory")
    # The existing selector validates all horizon populations, shared keys,
    # baseline predictions and the unchanged native coverage/baseline gates.
    native = choose(trials, scores, plan)
    ranked = []
    for family in FAMILIES:
        for index, operation in enumerate(plan.score_operation_ids):
            score = scores[operation]
            complete, mse, bias = points[family, index]
            reasons = list(native.reasons)
            if not complete or mse is None:
                reasons.append("incomplete_mean_predictions")
            if bias is not None and abs(bias) > plan.policy.maximum_absolute_normalized_mean_bias:
                reasons.append("normalized_mean_bias_above_limit")
            ranked.append(
                ScreeningTrialRank(
                    family=family,
                    frozen_trial_index=0 if index == 0 else 1,
                    score_operation_id=operation,
                    fit_operation_id=score.plan.fit_operation_ids[family],
                    score_receipt_sha256=score.content_sha256(),
                    model_artifact_sha256=score.model_artifact_sha256[family],
                    mean_squared_error=mse,
                    normalized_mean_bias=bias,
                    eligible=not reasons,
                    reasons=tuple(reasons),
                )
            )
    winners = {}
    for family in FAMILIES:
        valid = [t for t in ranked if t.family == family and t.eligible]
        winners[family] = min(valid, key=ScreeningTrialRank.rank_key) if valid else None
    result_reasons = (
        *native.reasons,
        *("no_valid_" + family + "_trial" for family in FAMILIES if winners[family] is None),
    )
    return ScreeningFamilyRanking(
        status="not_ready" if result_reasons else "three_families_ranked",
        rows=native.rows,
        eligible_rows=native.eligible_rows,
        trials=tuple(ranked),
        winners=winners,
        reasons=result_reasons,
        global_tune_diagnostics=native,
    )
