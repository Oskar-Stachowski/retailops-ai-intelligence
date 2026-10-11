"""Transfer original canonical planning evidence to reuse and both native variants."""

from pathlib import Path

from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_generation import validate_completed_generation
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.canonical_development_contract import (
    CanonicalDevelopmentPlanningJournal,
    PreparedCanonicalDevelopmentParents,
    ResolvedCanonicalDevelopmentPlanningReceipt,
    ResolvedCanonicalDevelopmentPreparationProtocol,
)
from retailops_ai.evaluation_campaign.development_planning import (
    planning_journal,
    validate_completed_native_planning,
)
from retailops_ai.evaluation_campaign.development_planning_contract import (
    DevelopmentPlanningContext,
)
from retailops_ai.evaluation_campaign.development_variants_contract import (
    OrdinaryDevelopmentReusePlan,
    ReusedDevelopmentParentReceipt,
    variant_operations,
)
from retailops_ai.source_snapshot.files import SnapshotError


def capture_canonical_development_parents(
    journal: Path,
    *,
    ordinary: ReusedDevelopmentParentReceipt,
    demand: CampaignGeneratedParentReceipt,
    physical: CampaignGeneratedParentReceipt,
) -> PreparedCanonicalDevelopmentParents:
    from retailops_ai.evaluation_campaign.development_variants import (
        validate_reused_development_parent,
        variants_journal,
    )

    before = variants_journal(journal)
    if not isinstance(before.protocol, ResolvedCanonicalDevelopmentPreparationProtocol):
        raise SnapshotError("canonical_development_original_full_variant_protocol_required")
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
            raise SnapshotError(
                "canonical_development_three_completed_original_operations_required"
            )
        costs.append(events[1].cost)
    result = PreparedCanonicalDevelopmentParents(
        preparation=before.protocol,
        preparation_journal_head_sha256=before.head_sha256,
        ordinary_reuse=ordinary,
        variant_generations=(demand, physical),
        preparation_operation_costs=(costs[0], costs[1], costs[2]),
    )
    if variants_journal(journal) != before:
        raise SnapshotError("canonical_development_journal_changed_during_capture")
    return result


def verify_canonical_development_parents(parents: PreparedCanonicalDevelopmentParents) -> None:
    parents = PreparedCanonicalDevelopmentParents.model_validate_json(parents.model_dump_json())
    observed = capture_canonical_development_parents(
        Path(parents.preparation.journal_path),
        ordinary=parents.ordinary_reuse,
        demand=parents.variant_generations[0],
        physical=parents.variant_generations[1],
    )
    if observed != parents:
        raise SnapshotError("canonical_development_original_journal_or_cost_changed")


def compile_canonical_development_preparation(
    journal_path: Path,
    *,
    bootstrap_journal: Path,
    generated: CampaignGeneratedParentReceipt,
    planned: ResolvedCanonicalDevelopmentPlanningReceipt,
    reuse_resources: CampaignGenerationResources,
    maximum_total_wall_seconds: int = 10800,
) -> ResolvedCanonicalDevelopmentPreparationProtocol:
    """Keep original IDs and measured generation/planning costs; no new Source read."""
    planned = ResolvedCanonicalDevelopmentPlanningReceipt.model_validate_json(
        planned.model_dump_json()
    )
    ledger = planning_journal(bootstrap_journal)
    if not isinstance(ledger, CanonicalDevelopmentPlanningJournal):
        raise SnapshotError("canonical_development_original_full_bootstrap_required")
    validate_completed_generation(bootstrap_journal, generated)
    validate_completed_native_planning(bootstrap_journal, planned)
    planning_cost = next(
        e.cost
        for e in ledger.events
        if e.kind == "finished" and e.reservation_id == planned.reservation_id
    )
    if planning_cost is None:
        raise SnapshotError("canonical_development_original_planning_cost_missing")
    reuse = OrdinaryDevelopmentReusePlan(
        bootstrap_protocol_sha256=ledger.protocol_sha256,
        bootstrap_journal_head_sha256=ledger.head_sha256,
        generated_receipt_sha256=generated.content_sha256(),
        planning_receipt_sha256=planned.content_sha256(),
        resources=reuse_resources,
    )
    return ResolvedCanonicalDevelopmentPreparationProtocol(
        **{k: getattr(ledger.protocol, k) for k in DevelopmentPlanningContext.model_fields},
        journal_path=str(journal_path.absolute()),
        bootstrap=ledger.protocol,
        generated=generated,
        planned=planned,
        planning_cost=planning_cost,
        reuse=reuse,
        sources=planned.preparation.sources,
        operations=variant_operations(planned.preparation, reuse),
        maximum_total_wall_seconds=maximum_total_wall_seconds,
    )
