"""Bind the predeclared canonical run to original 25/50 winners and full Source parents."""

from pathlib import Path

from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_tune_contract import CampaignForecastTuneReceipt
from retailops_ai.evaluation_campaign.canonical_development import (
    verify_canonical_development_parents,
)
from retailops_ai.evaluation_campaign.canonical_development_contract import (
    PreparedCanonicalDevelopmentParents,
)
from retailops_ai.evaluation_campaign.canonical_forecast_contract import (
    CompletedCanonicalForecast,
    PreparedCanonicalForecastProtocol,
)
from retailops_ai.evaluation_campaign.development_advancement import (
    _completed_costs,
    verified_family_metrics,
    verify_forecast_advancement,
)
from retailops_ai.evaluation_campaign.development_advancement_contract import (
    CompletedForecastAdvancement,
    advancement_operations,
    advancement_plans,
)
from retailops_ai.evaluation_campaign.development_planning_contract import (
    DevelopmentPlanningContext,
)
from retailops_ai.source_snapshot.files import SnapshotError


def verify_completed_advancement(advancement: CompletedForecastAdvancement) -> None:
    advancement = CompletedForecastAdvancement.model_validate_json(advancement.model_dump_json())
    observed = verify_forecast_advancement(
        Path(advancement.selection_bundle),
        journal=Path(advancement.protocol.journal_path),
        selected=advancement.selected,
    )
    if observed != advancement:
        raise SnapshotError("canonical_forecast_original_fifty_evidence_or_cost_changed")
    if observed.status != "original_families_verified":
        raise SnapshotError("canonical_forecast_original_fifty_not_ready")


def compile_canonical_forecast(
    *, advancement: CompletedForecastAdvancement, parents: PreparedCanonicalDevelopmentParents
) -> PreparedCanonicalForecastProtocol:
    """Preserve original hyperparameters and the paths/budgets frozen before the first fit."""
    advancement = CompletedForecastAdvancement.model_validate_json(advancement.model_dump_json())
    parents = PreparedCanonicalDevelopmentParents.model_validate_json(parents.model_dump_json())
    verify_completed_advancement(advancement)
    verify_canonical_development_parents(parents)
    screening = advancement.protocol.screening
    path = Path(screening.protocol.future_budgets["100"].execution_journal_path)
    if path.exists() or path.is_symlink():
        raise SnapshotError("canonical_forecast_original_execution_path_already_used")
    exports, fits, score, tune = advancement_plans(screening, parents, products=100)
    return PreparedCanonicalForecastProtocol(
        **{k: getattr(screening.protocol, k) for k in DevelopmentPlanningContext.model_fields},
        journal_path=str(path),
        advancement=advancement,
        parents=parents,
        sources=parents.preparation.sources,
        exports=exports,
        fits=fits,
        score=score,
        selection_plan=tune,
        operations=advancement_operations(
            parents.preparation.sources, exports, fits, score, tune, products=100
        ),
    )


def verify_canonical_forecast(
    bundle: Path, *, journal: Path, selected: CampaignForecastTuneReceipt
) -> CompletedCanonicalForecast:
    """Verify all original 25/50/100 costs and results without opening final labels."""
    from retailops_ai.evaluation_campaign.campaign_tune import (
        _verify_original_forecast_operations,
        verify_campaign_forecast_selection,
    )

    before = campaign_journal.inspect(journal)
    if not isinstance(before.protocol, PreparedCanonicalForecastProtocol):
        raise SnapshotError("canonical_forecast_original_execution_protocol_required")
    protocol = before.protocol
    verify_campaign_forecast_selection(bundle, journal=journal, receipt=selected)
    verify_completed_advancement(protocol.advancement)
    verify_canonical_development_parents(protocol.parents)
    _verify_original_forecast_operations(
        bundle,
        journal=journal,
        receipt=selected,
        ledger=before,
        operations=protocol.operations[:-1],
    )
    families, reasons = verified_family_metrics(bundle, selected)
    result = CompletedCanonicalForecast(
        protocol=protocol,
        journal_head_sha256=before.head_sha256,
        selection_bundle=str(bundle.resolve()),
        selected=selected,
        operation_costs=_completed_costs(before, 8),
        status="not_ready" if reasons else "original_families_verified",
        family_reasons=families,
        reasons=reasons,
    )
    if campaign_journal.inspect(journal) != before:
        raise SnapshotError("canonical_forecast_journal_changed_during_verification")
    return result
