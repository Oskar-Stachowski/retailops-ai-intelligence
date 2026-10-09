"""Whole-census native anomaly metrics and independent saved-score replay.

This numerical component does not authorize a Source read, fitting or final
access. The campaign adapter must first verify its public feature/truth parents
and reserve the operation. Native metrics are not three-use qualification.
"""

import hashlib
import re
from collections.abc import Iterable
from datetime import timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from retailops_ai.anomaly_detectors.contract import Family
from retailops_ai.anomaly_detectors.protocol import Scope, Window, scoring_origin, series_key
from retailops_ai.anomaly_evaluation.contract import (
    Decision,
    EvaluationPolicy,
    OrdinaryTruth,
    Truth,
    validate_truth,
)
from retailops_ai.anomaly_evaluation.evaluator import evaluate
from retailops_ai.anomaly_evaluation.verification import verify_scores
from retailops_ai.anomaly_portfolio.model import Model
from retailops_ai.data_contracts.common import Contract, FalseFlag, Sha256, SourceID, UtcTime
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign.campaign_anomaly_scoring import AnomalyScoredRow
from retailops_ai.qualified_anomalies.contract import Policy

MAX_NATIVE_EVALUATION_ROWS = 1000000
REPLAY_BATCH_ROWS = 8192


class CampaignAnomalyCensusPlan(Contract):
    """Declared population and identities, resolved before consuming decisions.

    Native batch is the post-selection development scoring role, whereas native
    validation belongs to threshold selection. Final uses native final_test.
    Source/truth/feature hashes are bindings for the enclosing audited adapter;
    this component cannot verify a public feature parent from a hash alone.
    """

    version: Literal["ai09-native-anomaly-census-plan-1.0.0"] = (
        "ai09-native-anomaly-census-plan-1.0.0"
    )
    phase: Literal["development", "final"]
    source_dataset_id: SourceID
    source_recipe_sha256: Sha256
    source_scenario_sha256: Sha256
    feature_parent_sha256: Sha256
    model_sha256: Sha256
    truth_sha256: Sha256
    scopes: Annotated[tuple[Scope, ...], Field(min_length=1, max_length=65536)]
    window: Window
    as_of: UtcTime
    family: Family
    evaluation_policy: EvaluationPolicy = EvaluationPolicy()
    max_rows: Annotated[int, Field(ge=1, le=MAX_NATIVE_EVALUATION_ROWS)]
    population: Literal["every_declared_scope_every_day_including_abstentions"] = (
        "every_declared_scope_every_day_including_abstentions"
    )

    @property
    def rows(self) -> int:
        return len(self.scopes) * ((self.window.end - self.window.start).days + 1)

    @property
    def native_role(self) -> Literal["batch", "final_test"]:
        return "batch" if self.phase == "development" else "final_test"

    @model_validator(mode="after")
    def full_population(self) -> Self:
        keys = [series_key(s) for s in self.scopes]
        if (
            keys != sorted(set(keys))
            or (self.window.end - self.window.start).days > 2000
            or self.rows > self.max_rows
            or any(
                scoring_origin(self.window.end, s.event_type, Policy()) > self.as_of
                for s in self.scopes
            )
        ):
            raise ValueError("campaign_anomaly_evaluation_population_or_budget")
        return self

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))


class CampaignOrdinaryAnomalyCensusPlan(CampaignAnomalyCensusPlan):
    """Ordinary controls have a real Source-verification identity and no scenario."""

    version: Literal["ai09-native-ordinary-anomaly-census-plan-1.0.0"] = (
        "ai09-native-ordinary-anomaly-census-plan-1.0.0"  # type: ignore[assignment]
    )
    source_scenario_sha256: None = None  # type: ignore[assignment]
    source_verification_sha256: Sha256
    source_generation_receipt_sha256: Sha256


def _plan(
    plan: CampaignAnomalyCensusPlan | CampaignOrdinaryAnomalyCensusPlan,
) -> CampaignAnomalyCensusPlan | CampaignOrdinaryAnomalyCensusPlan:
    cls = (
        CampaignOrdinaryAnomalyCensusPlan
        if isinstance(plan, CampaignOrdinaryAnomalyCensusPlan)
        else CampaignAnomalyCensusPlan
    )
    return cls.model_validate_json(plan.model_dump_json())


class CampaignAnomalyCensusEvaluation(Contract):
    """Native numerical evidence; complete Project admission remains separate."""

    version: Literal["ai09-native-anomaly-census-evaluation-1.0.0"] = (
        "ai09-native-anomaly-census-evaluation-1.0.0"
    )
    plan: CampaignAnomalyCensusPlan
    rows: Annotated[int, Field(ge=1, le=MAX_NATIVE_EVALUATION_ROWS)]
    replay_batches: Annotated[int, Field(ge=1)]
    scoring_trace_sha256: Sha256
    native_evaluation: dict[str, JsonValue]
    native_evaluation_sha256: Sha256
    all_requested_decisions_replayed: Literal[True] = True
    parent_source_verified: FalseFlag = False
    critical_segment_inventory_complete: FalseFlag = False
    block_uncertainty_complete: FalseFlag = False
    quality_qualified: FalseFlag = False
    promotion_allowed: FalseFlag = False
    stage_ready: FalseFlag = False

    @model_validator(mode="after")
    def binding(self) -> Self:
        if (
            self.rows != self.plan.rows
            or self.replay_batches != (self.rows + REPLAY_BATCH_ROWS - 1) // REPLAY_BATCH_ROWS
            or self.native_evaluation_sha256 != canonical_sha256(self.native_evaluation)
        ):
            raise ValueError("campaign_anomaly_evaluation_result_binding")
        return self

    def content_sha256(self) -> str:
        type(self).model_validate_json(self.model_dump_json())
        return canonical_sha256(self.model_dump(mode="json"))


class CampaignOrdinaryAnomalyCensusEvaluation(CampaignAnomalyCensusEvaluation):
    """Separate ordinary wire; the planned v1 result and its schema remain intact."""

    version: Literal["ai09-native-ordinary-anomaly-census-evaluation-1.0.0"] = (
        "ai09-native-ordinary-anomaly-census-evaluation-1.0.0"  # type: ignore[assignment]
    )
    plan: CampaignOrdinaryAnomalyCensusPlan


def evaluate_anomaly_census(
    model: Model,
    scored: Iterable[AnomalyScoredRow],
    truth: Truth | OrdinaryTruth,
    plan: CampaignAnomalyCensusPlan | CampaignOrdinaryAnomalyCensusPlan,
) -> CampaignAnomalyCensusEvaluation:
    """Replay every saved score, then use unchanged native observation/episode metrics.

    Decisions must follow the complete declared series/day order. A missing,
    extra, duplicated, out-of-order or foreign decision fails the operation;
    no earlier batch is a completed evaluation. Unknown/immature truth and
    missing public features retain their native coverage and null metrics.
    Native evaluation's one-million-row limit is preserved; an over-budget
    population fails before consuming input rather than silently trimming it.
    """
    plan = _plan(plan)
    model = Model.model_validate_json(model.model_dump_json())
    truth = validate_truth(truth)
    ordinary_binding = (
        isinstance(plan, CampaignOrdinaryAnomalyCensusPlan)
        and isinstance(truth, OrdinaryTruth)
        and plan.source_verification_sha256 == truth.source_verification_sha256
        and plan.source_generation_receipt_sha256 == truth.source_generation_receipt_sha256
    ) or (
        not isinstance(plan, CampaignOrdinaryAnomalyCensusPlan)
        and not isinstance(truth, OrdinaryTruth)
    )
    if (
        not ordinary_binding
        or canonical_sha256(model.model_dump(mode="json")) != plan.model_sha256
        or canonical_sha256(truth.model_dump(mode="json")) != plan.truth_sha256
        or truth.source_dataset_id != plan.source_dataset_id
        or truth.source_scenario_sha256 != plan.source_scenario_sha256
        or any(
            scoring_origin(plan.window.start, s.event_type, Policy())
            <= model.descriptor.selection_cutoff
            for s in plan.scopes
        )
    ):
        raise ValueError("campaign_anomaly_evaluation_model_truth_or_cutoff_binding")
    decisions: list[Decision] = []
    batch: list[Decision] = []
    inputs: list[dict[str, object] | None] = []
    trace = hashlib.sha256()
    days = (plan.window.end - plan.window.start).days + 1
    batches = 0
    for index, row in enumerate(scored):
        if index >= plan.rows or not isinstance(row, AnomalyScoredRow):
            raise ValueError("campaign_anomaly_evaluation_extra_or_invalid_row")
        decision = Decision.model_validate_json(row.decision.model_dump_json())
        scope = plan.scopes[index // days]
        day = plan.window.start + timedelta(days=index % days)
        if (
            series_key(decision) != series_key(scope)
            or decision.business_date != day
            or decision.detector_id != model.detector_id
            or decision.family != plan.family
            or decision.role != plan.native_role
            or decision.scoring_origin != scoring_origin(day, scope.event_type, Policy())
            or decision.scoring_origin > plan.as_of
            or (decision.input_status == "no_declaration") != (row.public_point_sha256 is None)
            or row.public_point_sha256 is not None
            and re.fullmatch(r"[0-9a-f]{64}", row.public_point_sha256) is None
        ):
            raise ValueError("campaign_anomaly_evaluation_decision_population_or_clock")
        numerical = row.model_row.model_dump(mode="json") if row.model_row is not None else None
        trace.update(
            canonical_bytes(
                {
                    "decision": decision.model_dump(mode="json"),
                    "model_row": numerical,
                    "public_point_sha256": row.public_point_sha256,
                }
            )
            + b"\n"
        )
        decisions.append(decision)
        batch.append(decision)
        inputs.append(numerical)
        if len(batch) == REPLAY_BATCH_ROWS:
            verify_scores(model, batch, inputs)
            batches += 1
            batch, inputs = [], []
    if len(decisions) != plan.rows:
        raise ValueError("campaign_anomaly_evaluation_incomplete_census")
    if batch:
        verify_scores(model, batch, inputs)
        batches += 1
    native = evaluate(
        decisions,
        truth,
        plan.window,
        plan.as_of,
        plan.evaluation_policy,
        source_dataset_id=plan.source_dataset_id,
    )
    result_type = (
        CampaignOrdinaryAnomalyCensusEvaluation
        if isinstance(plan, CampaignOrdinaryAnomalyCensusPlan)
        else CampaignAnomalyCensusEvaluation
    )
    return result_type.model_validate_json(
        canonical_bytes(
            {
                "version": "ai09-native-ordinary-anomaly-census-evaluation-1.0.0"
                if isinstance(plan, CampaignOrdinaryAnomalyCensusPlan)
                else "ai09-native-anomaly-census-evaluation-1.0.0",
                "plan": plan.model_dump(mode="json"),
                "rows": len(decisions),
                "replay_batches": batches,
                "scoring_trace_sha256": trace.hexdigest(),
                "native_evaluation": native,
                "native_evaluation_sha256": canonical_sha256(native),
            }
        )
    )


def verify_anomaly_census_evaluation(
    result: CampaignAnomalyCensusEvaluation,
    *,
    model: Model,
    scored: Iterable[AnomalyScoredRow],
    truth: Truth | OrdinaryTruth,
    plan: CampaignAnomalyCensusPlan | CampaignOrdinaryAnomalyCensusPlan,
) -> None:
    """Recompute against independently supplied frozen plan/model/input/truth parents.

    A caller must bind those parents to its actual audited operation first.
    Resealing a metrics hash or changing this result's plan cannot bypass replay.
    This function grants no Project quality or public final-access permission.
    """
    result_type = (
        CampaignOrdinaryAnomalyCensusEvaluation
        if isinstance(result, CampaignOrdinaryAnomalyCensusEvaluation)
        else CampaignAnomalyCensusEvaluation
    )
    safe = result_type.model_validate_json(result.model_dump_json())
    if safe != evaluate_anomaly_census(model, scored, truth, plan):
        raise ValueError("campaign_anomaly_evaluation_replay_mismatch")
