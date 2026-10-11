"""Transfer completed preparation evidence into charged exports and frozen trials."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignCost,
    CampaignJournal,
    CampaignProtocol,
)
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportPlan,
    CampaignGeneratedParentReceipt,
)
from retailops_ai.evaluation_campaign.campaign_fit_contract import CampaignForecastFitPlan
from retailops_ai.evaluation_campaign.campaign_generation import validate_completed_generation
from retailops_ai.evaluation_campaign.campaign_score_contract import CampaignForecastScorePlan
from retailops_ai.evaluation_campaign.development_planning_contract import (
    DevelopmentPlanningContext,
)
from retailops_ai.evaluation_campaign.development_screening_contract import (
    PreparedDevelopmentParents,
    PreparedDevelopmentScreeningProtocol,
    screening_operations,
)
from retailops_ai.evaluation_campaign.development_variants import (
    validate_reused_development_parent,
    variants_journal,
)
from retailops_ai.evaluation_campaign.development_variants_contract import (
    ResolvedDevelopmentPreparationProtocol,
    ReusedDevelopmentParentReceipt,
)
from retailops_ai.source_snapshot.files import SnapshotError

if TYPE_CHECKING:
    from retailops_ai.evaluation_campaign.canonical_forecast_contract import (
        PreparedCanonicalForecastProtocol,
    )
    from retailops_ai.evaluation_campaign.development_advancement_contract import (
        PreparedForecastAdvancementProtocol,
    )


def capture_prepared_development_parents(
    journal: Path,
    *,
    ordinary: ReusedDevelopmentParentReceipt,
    demand: CampaignGeneratedParentReceipt,
    physical: CampaignGeneratedParentReceipt,
) -> PreparedDevelopmentParents:
    """Inspect original durable receipts only, without touching Source facts."""
    before = variants_journal(journal)
    if not isinstance(before.protocol, ResolvedDevelopmentPreparationProtocol):
        raise SnapshotError("prepared_development_small_profile_parents_required")
    validate_reused_development_parent(journal, ordinary)
    validate_completed_generation(journal, demand)
    validate_completed_generation(journal, physical)
    costs: list[CampaignCost] = []
    for operation in before.protocol.operations:
        events = [e for e in before.events if e.operation_id == operation.operation_id]
        if (
            len(events) != 2
            or events[0].kind != "reserved"
            or events[1].kind != "finished"
            or events[1].result != "completed"
            or events[1].cost is None
        ):
            raise SnapshotError("prepared_development_requires_three_completed_operations")
        costs.append(events[1].cost)
    result = PreparedDevelopmentParents(
        preparation=before.protocol,
        preparation_journal_head_sha256=before.head_sha256,
        ordinary_reuse=ordinary,
        variant_generations=(demand, physical),
        preparation_operation_costs=(costs[0], costs[1], costs[2]),
    )
    if variants_journal(journal) != before:
        raise SnapshotError("prepared_development_journal_changed_during_capture")
    return result


def verify_prepared_development_parents(parents: PreparedDevelopmentParents) -> None:
    parents = PreparedDevelopmentParents.model_validate_json(parents.model_dump_json())
    observed = capture_prepared_development_parents(
        Path(parents.preparation.journal_path),
        ordinary=parents.ordinary_reuse,
        demand=parents.variant_generations[0],
        physical=parents.variant_generations[1],
    )
    if observed != parents:
        raise SnapshotError("prepared_development_original_journal_or_cost_changed")


def compile_prepared_development_screening(
    path: Path,
    *,
    parents: PreparedDevelopmentParents,
    exports: tuple[CampaignDevelopmentExportPlan, ...],
    fits: tuple[CampaignForecastFitPlan, ...],
    scores: tuple[CampaignForecastScorePlan, CampaignForecastScorePlan],
) -> PreparedDevelopmentScreeningProtocol:
    """Freeze actual recipes before exports/fits; no generation or fit occurs here."""
    parents = PreparedDevelopmentParents.model_validate_json(parents.model_dump_json())
    verify_prepared_development_parents(parents)
    p = parents.preparation
    return PreparedDevelopmentScreeningProtocol(
        **{k: getattr(p, k) for k in DevelopmentPlanningContext.model_fields},
        journal_path=str(path.absolute()),
        parents=parents,
        sources=p.sources,
        exports=exports,
        fits=fits,
        scores=scores,
        operations=screening_operations(p.sources, exports, fits, scores),
    )


def prepared_screening_prefix(
    protocol: CampaignProtocol,
) -> (
    PreparedDevelopmentScreeningProtocol
    | PreparedForecastAdvancementProtocol
    | PreparedCanonicalForecastProtocol
    | None
):
    from retailops_ai.evaluation_campaign.canonical_forecast_contract import (
        PreparedCanonicalForecastProtocol,
    )
    from retailops_ai.evaluation_campaign.development_advancement_contract import (
        PreparedForecastAdvancementProtocol,
    )
    from retailops_ai.evaluation_campaign.development_screening_selection_contract import (
        PreparedDevelopmentSelectionProtocol,
    )

    if isinstance(
        protocol, (PreparedForecastAdvancementProtocol, PreparedCanonicalForecastProtocol)
    ):
        return protocol
    if isinstance(protocol, PreparedDevelopmentSelectionProtocol):
        return protocol.screening
    return protocol if isinstance(protocol, PreparedDevelopmentScreeningProtocol) else None


def bind_prepared_screening_export(
    ledger: CampaignJournal,
    operation_id: str,
    plan: CampaignDevelopmentExportPlan,
    generated: CampaignGeneratedParentReceipt,
) -> None:
    """Called inside the reservation, before any snapshot, curated or Source I/O."""
    p = prepared_screening_prefix(ledger.protocol)
    if p is None:
        raise SnapshotError("prepared_screening_explicit_protocol_required")
    position = next(
        (i for i, o in enumerate(p.operations[:3]) if o.operation_id == operation_id), None
    )
    if (
        position is None
        or plan != p.exports[position]
        or generated != p.parents.generations[position]
    ):
        raise SnapshotError("prepared_screening_frozen_export_and_original_parent_binding")
    from retailops_ai.evaluation_campaign.canonical_forecast_contract import (
        PreparedCanonicalForecastProtocol,
    )

    if isinstance(p, PreparedCanonicalForecastProtocol):
        from retailops_ai.evaluation_campaign.canonical_development import (
            verify_canonical_development_parents,
        )
        from retailops_ai.evaluation_campaign.canonical_forecast import verify_completed_advancement

        verify_canonical_development_parents(p.parents)
        verify_completed_advancement(p.advancement)
        return
    verify_prepared_development_parents(p.parents)
    from retailops_ai.evaluation_campaign.development_advancement_contract import (
        PreparedForecastAdvancementProtocol,
    )

    if isinstance(p, PreparedForecastAdvancementProtocol):
        from retailops_ai.evaluation_campaign.development_advancement import (
            verify_completed_screening,
        )

        verify_completed_screening(p.screening)
