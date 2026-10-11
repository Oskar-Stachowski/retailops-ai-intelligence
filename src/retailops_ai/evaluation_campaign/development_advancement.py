"""Admit the original winners to one measured50-product execution scope."""

import json
from pathlib import Path

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost, CampaignJournal
from retailops_ai.evaluation_campaign.campaign_fit_contract import Family
from retailops_ai.evaluation_campaign.campaign_score_contract import FAMILIES
from retailops_ai.evaluation_campaign.campaign_tune_contract import CampaignForecastTuneReceipt
from retailops_ai.evaluation_campaign.development_advancement_contract import (
    CompletedForecastAdvancement,
    CompletedForecastScreening,
    ForecastScaleBudget,
    MultiscaleSelectionProtocol,
    PreparedForecastAdvancementProtocol,
    advancement_operations,
    advancement_plans,
)
from retailops_ai.evaluation_campaign.development_planning_contract import (
    DevelopmentPlanningContext,
)
from retailops_ai.evaluation_campaign.development_screening import (
    verify_prepared_development_parents,
)
from retailops_ai.evaluation_campaign.development_screening_contract import (
    PreparedDevelopmentParents,
)
from retailops_ai.evaluation_campaign.development_screening_selection import _number
from retailops_ai.evaluation_campaign.development_screening_selection_contract import (
    PreparedDevelopmentSelectionProtocol,
)
from retailops_ai.source_snapshot.files import SnapshotError, read_bytes


def freeze_multiscale_selection(
    prefix: PreparedDevelopmentSelectionProtocol,
    *,
    fifty: ForecastScaleBudget,
    hundred: ForecastScaleBudget,
) -> MultiscaleSelectionProtocol:
    """Freeze all later resource and journal paths before any screening trial."""
    prefix = PreparedDevelopmentSelectionProtocol.model_validate_json(prefix.model_dump_json())
    result = MultiscaleSelectionProtocol.model_validate_json(
        canonical_bytes(
            {
                **prefix.model_dump(mode="json"),
                "future_budgets": {
                    "50": fifty.model_dump(mode="json"),
                    "100": hundred.model_dump(mode="json"),
                },
            }
        )
    )
    for name in (
        result.journal_path,
        *(p for budget in result.future_budgets.values() for p in budget.paths()),
    ):
        if Path(name).exists() or Path(name).is_symlink():
            raise SnapshotError("multiscale_budgets_and_paths_must_be_frozen_before_execution")
    verify_prepared_development_parents(result.screening.parents)
    return result


def _completed_costs(ledger: CampaignJournal, count: int) -> tuple[CampaignCost, ...]:
    events = [e for e in ledger.events if e.kind == "finished"]
    if len(events) != count or any(
        e.result != "completed"
        or e.cost is None
        or e.cost.peak_process_tree_rss_bytes is None
        or e.cost.artifact_bytes is None
        for e in events
    ):
        raise SnapshotError("advancement_all_original_completed_costs_required")
    return tuple(e.cost for e in events if e.cost is not None)


def capture_completed_screening(
    bundle: Path, *, journal: Path, selected: CampaignForecastTuneReceipt
) -> CompletedForecastScreening:
    from retailops_ai.evaluation_campaign.campaign_tune import verified_screening_family_ranking

    before = campaign_journal.inspect(journal)
    if not isinstance(before.protocol, MultiscaleSelectionProtocol):
        raise SnapshotError("advancement_requires_budgets_frozen_before_original_screening")
    ranking = verified_screening_family_ranking(bundle, journal=journal, receipt=selected)
    if ranking.status != "three_families_ranked":
        raise SnapshotError("advancement_requires_valid_original_winner_in_every_family")
    result = CompletedForecastScreening(
        protocol=before.protocol,
        journal_head_sha256=before.head_sha256,
        selection_bundle=str(bundle.resolve()),
        selected=selected,
        ranking=ranking,
        operation_costs=_completed_costs(before, 12),
    )
    if campaign_journal.inspect(journal) != before:
        raise SnapshotError("advancement_original_screening_changed_during_capture")
    return result


def verify_completed_screening(screening: CompletedForecastScreening) -> None:
    screening = CompletedForecastScreening.model_validate_json(screening.model_dump_json())
    observed = capture_completed_screening(
        Path(screening.selection_bundle),
        journal=Path(screening.protocol.journal_path),
        selected=screening.selected,
    )
    if observed != screening:
        raise SnapshotError("advancement_original_selection_recipes_or_costs_changed")


def compile_forecast_advancement(
    *, screening: CompletedForecastScreening, parents: PreparedDevelopmentParents
) -> PreparedForecastAdvancementProtocol:
    """Use the only predeclared destination and derive every fit from its real winner."""
    screening = CompletedForecastScreening.model_validate_json(screening.model_dump_json())
    parents = PreparedDevelopmentParents.model_validate_json(parents.model_dump_json())
    verify_completed_screening(screening)
    verify_prepared_development_parents(parents)
    path = Path(screening.protocol.future_budgets["50"].execution_journal_path)
    if path.exists() or path.is_symlink():
        raise SnapshotError("advancement_original_execution_path_already_used")
    exports, fits, score, tune = advancement_plans(screening, parents)
    return PreparedForecastAdvancementProtocol(
        **{k: getattr(screening.protocol, k) for k in DevelopmentPlanningContext.model_fields},
        journal_path=str(path),
        screening=screening,
        parents=parents,
        sources=parents.preparation.sources,
        exports=exports,
        fits=fits,
        score=score,
        selection_plan=tune,
        operations=advancement_operations(parents.preparation.sources, exports, fits, score, tune),
    )


def verify_forecast_advancement(
    bundle: Path, *, journal: Path, selected: CampaignForecastTuneReceipt
) -> CompletedForecastAdvancement:
    """Verify all8 original50 costs, all25 ancestry, and full paired family metrics."""
    from retailops_ai.evaluation_campaign.campaign_tune import (
        _verify_original_forecast_operations,
        verify_campaign_forecast_selection,
    )

    before = campaign_journal.inspect(journal)
    if not isinstance(before.protocol, PreparedForecastAdvancementProtocol):
        raise SnapshotError("advancement_original_execution_protocol_required")
    protocol = before.protocol
    verify_campaign_forecast_selection(bundle, journal=journal, receipt=selected)
    verify_completed_screening(protocol.screening)
    verify_prepared_development_parents(protocol.parents)
    _verify_original_forecast_operations(
        bundle,
        journal=journal,
        receipt=selected,
        ledger=before,
        operations=protocol.operations[:-1],
    )
    families, result_reasons = verified_family_metrics(bundle, selected)
    result = CompletedForecastAdvancement(
        protocol=protocol,
        journal_head_sha256=before.head_sha256,
        selection_bundle=str(bundle.resolve()),
        selected=selected,
        operation_costs=_completed_costs(before, 8),
        status="not_ready" if result_reasons else "original_families_verified",
        family_reasons=families,
        reasons=result_reasons,
    )
    if campaign_journal.inspect(journal) != before:
        raise SnapshotError("advancement_journal_changed_during_verification")
    return result


def verified_family_metrics(
    bundle: Path,
    selected: CampaignForecastTuneReceipt,
) -> tuple[dict[Family, tuple[str, ...]], tuple[str, ...]]:
    """Read already verified paired Tune metrics without another data or label pass."""
    trials = json.loads(read_bytes(bundle, "metrics.json", selected.plan.max_output_bytes))[
        "trials"
    ]
    if len(trials) != 1:
        raise SnapshotError("advancement_exact_original_tune_trio_required")
    models = trials[0]["metrics"]["segments"][0]["models"]
    families: dict[Family, tuple[str, ...]] = {}
    for family in FAMILIES:
        point = models["rf_mean" if family == "rf" else family]["mean"]
        mse = _number(point["mse"], nonnegative=True)
        bias = _number(point["normalized_bias"])
        if (
            type(point["complete"]) is not bool
            or type(point["predicted_rows"]) is not int
            or point["complete"]
            != (selected.eligible_rows > 0 and point["predicted_rows"] == selected.eligible_rows)
            or not 0 <= point["predicted_rows"] <= selected.eligible_rows
        ):
            raise SnapshotError("advancement_original_mean_population_mismatch")
        reasons = []
        if not point["complete"] or mse is None:
            reasons.append("incomplete_mean_predictions")
        if (
            bias is not None
            and abs(bias) > selected.plan.policy.maximum_absolute_normalized_mean_bias
        ):
            reasons.append("normalized_mean_bias_above_limit")
        families[family] = tuple(reasons)
    result_reasons = (
        *selected.selection.reasons,
        *("invalid_" + f + "_family" for f in ("rf", "hgb", "tensorflow") if families[f]),
    )
    return families, result_reasons
