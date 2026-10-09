"""Verify the prepared ordinary parent, then generate both original native variants."""

import os
import stat
import sys
from pathlib import Path
from time import perf_counter
from typing import Literal

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost
from retailops_ai.evaluation_campaign.campaign_export import _store_receipt
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_generation import (
    _environment,
    generate_campaign_parent,
    validate_completed_generation,
)
from retailops_ai.evaluation_campaign.campaign_generation_contract import (
    CampaignGenerationResources,
)
from retailops_ai.evaluation_campaign.campaign_generation_monitor import monitor, scratch_bytes
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.development_planning import (
    planning_journal,
    validate_completed_native_planning,
)
from retailops_ai.evaluation_campaign.development_planning_contract import (
    DevelopmentPlanningContext,
    ResolvedNativeDevelopmentPlanningReceipt,
)
from retailops_ai.evaluation_campaign.development_variants_contract import (
    OrdinaryDevelopmentReusePlan,
    ResolvedDevelopmentPreparationJournal,
    ResolvedDevelopmentPreparationProtocol,
    ReusedDevelopmentParentReceipt,
    variant_operations,
)
from retailops_ai.evaluation_campaign.physical_contract import PhysicalSourceSpec
from retailops_ai.source_snapshot.files import SnapshotError, checked_directory, regular_file


def compile_resolved_development_preparation(
    journal_path: Path,
    *,
    bootstrap_journal: Path,
    generated: CampaignGeneratedParentReceipt,
    planned: ResolvedNativeDevelopmentPlanningReceipt,
    reuse_resources: CampaignGenerationResources,
    maximum_total_wall_seconds: int = 10800,
) -> ResolvedDevelopmentPreparationProtocol:
    """Read durable bookkeeping only; native recipes were resolved in the charged read."""
    planned = ResolvedNativeDevelopmentPlanningReceipt.model_validate_json(
        planned.model_dump_json()
    )
    ledger = planning_journal(bootstrap_journal)
    validate_completed_generation(bootstrap_journal, generated)
    validate_completed_native_planning(bootstrap_journal, planned)
    planning_cost = next(
        e.cost
        for e in ledger.events
        if e.kind == "finished" and e.reservation_id == planned.reservation_id
    )
    if planning_cost is None:
        raise SnapshotError("resolved_development_planning_cost_missing")
    reuse = OrdinaryDevelopmentReusePlan(
        bootstrap_protocol_sha256=ledger.protocol_sha256,
        bootstrap_journal_head_sha256=ledger.head_sha256,
        generated_receipt_sha256=generated.content_sha256(),
        planning_receipt_sha256=planned.content_sha256(),
        resources=reuse_resources,
    )
    context = {k: getattr(ledger.protocol, k) for k in DevelopmentPlanningContext.model_fields}
    return ResolvedDevelopmentPreparationProtocol(
        **context,
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


def variants_journal(journal: Path) -> ResolvedDevelopmentPreparationJournal:
    ledger = campaign_journal.inspect(journal)
    if not isinstance(ledger, ResolvedDevelopmentPreparationJournal):
        raise SnapshotError("resolved_development_requires_separate_journal")
    return ledger


def _bootstrap_unchanged(protocol: ResolvedDevelopmentPreparationProtocol) -> None:
    root = Path(protocol.bootstrap.journal_path)
    ledger = planning_journal(root)
    validate_completed_generation(root, protocol.generated)
    validate_completed_native_planning(root, protocol.planned)
    if (
        ledger.protocol != protocol.bootstrap
        or ledger.head_sha256 != protocol.reuse.bootstrap_journal_head_sha256
        or next(
            e.cost
            for e in ledger.events
            if e.kind == "finished" and e.reservation_id == protocol.planned.reservation_id
        )
        != protocol.planning_cost
    ):
        raise SnapshotError("resolved_development_bootstrap_bookkeeping_changed")


def remaining_wall_seconds(ledger: ResolvedDevelopmentPreparationJournal) -> float:
    completed = [e for e in ledger.events if e.kind == "finished"]
    if any(e.cost is None for e in completed):
        raise SnapshotError("resolved_development_unknown_previous_cost")
    return (
        ledger.protocol.maximum_total_wall_seconds
        - ledger.protocol.cold_wall_seconds()
        - sum(e.cost.wall_seconds for e in completed if e.cost is not None)
    )


def reuse_ordinary_development_parent(
    *,
    journal: Path,
    snapshot: Path,
    curated: Path,
    output_root: Path,
) -> ReusedDevelopmentParentReceipt:
    """Charge one full native consumer replay; never relabel it as a generation."""
    ledger = variants_journal(journal)
    protocol, operation = ledger.protocol, ledger.protocol.operations[0]
    started = perf_counter()
    with campaign_journal.audited_operation(journal, operation.operation_id) as handle:
        _bootstrap_unchanged(protocol)
        available = min(protocol.reuse.resources.wall_seconds, remaining_wall_seconds(ledger))
        if available <= 0:
            raise SnapshotError("resolved_development_total_wall_budget_exhausted")
        checked_directory(output_root)
        # The original generation verifier accepts the public import envelope,
        # whose child is named snapshot. Reject a different caller path rather
        # than verify an unintended sibling with a matching envelope.
        if snapshot.name != "snapshot":
            raise SnapshotError("resolved_development_public_snapshot_path_required")
        root = output_root / str(handle.reservation.reservation_id)
        root.mkdir(mode=0o700)
        (root / "tmp").mkdir(mode=0o700)
        write(
            root / "request.json",
            {
                "plan": protocol.bootstrap.generation.model_dump(mode="json"),
                "source": protocol.sources[0].model_dump(mode="json"),
                "runtime": protocol.runtime.model_dump(mode="json"),
            },
        )
        write(root / "import.json", {"destination": str(snapshot.absolute().parent)})
        write(root / "curation.json", {"destination": str(curated.absolute())})
        worker = Path(__file__).with_name("campaign_generation_worker.py")
        result = monitor(
            [sys.executable, "-I", "-B", str(worker), "verify", str(root), str(root)],
            root=root,
            log=root / "verify.log",
            env=_environment(root),
            scratch=(root,),
            resources=protocol.reuse.resources,
            deadline=started + available,
        )
        write(root / "verify-resources.json", result)
        peak = result["sampled_tree_peak_rss_bytes"]
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=peak,
            artifact_bytes=scratch_bytes((root,)),
        )
        if result["status"] != "passed":
            raise SnapshotError("resolved_development_reuse_worker_failed")
        verified = read(root / "verify.json")
        measured = verified["worker_peak_rss_bytes"]
        if (
            type(measured) is not int
            or measured < 1
            or measured > protocol.reuse.resources.tree_rss_bytes
        ):
            raise SnapshotError("resolved_development_reuse_worker_rss_limit")
        source = PhysicalSourceSpec.model_validate_json(canonical_bytes(verified["source"]))
        if source != protocol.generated.source:
            raise SnapshotError("resolved_development_reused_parent_changed")
        _bootstrap_unchanged(protocol)
        artifacts = scratch_bytes((root,))
        if (
            perf_counter() - started > available
            or artifacts > protocol.reuse.resources.scratch_bytes
        ):
            raise SnapshotError("resolved_development_reuse_completion_limit")
        receipt = ReusedDevelopmentParentReceipt(
            protocol_sha256=ledger.protocol_sha256,
            source_recipe_sha256=protocol.sources[0].content_sha256(),
            operation_id=operation.operation_id,
            reservation_id=str(handle.reservation.reservation_id),
            source=source,
            generated_receipt_sha256=protocol.generated.content_sha256(),
            planning_receipt_sha256=protocol.planned.content_sha256(),
            generation_cost=protocol.planned.generation_cost,
            planning_cost=protocol.planning_cost,
            verified_inventories=verified["verified_inventories"],
            runtime=protocol.runtime,
        )
        _store_receipt(journal, receipt)
        handle.evidence_sha256 = receipt.content_sha256()
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=max(peak or 0, measured),
            artifact_bytes=artifacts,
        )
    validate_reused_development_parent(journal, receipt)
    return receipt


def validate_reused_development_parent(
    journal: Path, receipt: ReusedDevelopmentParentReceipt
) -> None:
    """Validate original bookkeeping and stored evidence without another facts read."""
    receipt = ReusedDevelopmentParentReceipt.model_validate_json(receipt.model_dump_json())
    ledger = variants_journal(journal)
    protocol = ledger.protocol
    _bootstrap_unchanged(protocol)
    finished = next(
        (
            e
            for e in ledger.events
            if e.kind == "finished" and e.reservation_id == receipt.reservation_id
        ),
        None,
    )
    if (
        receipt.protocol_sha256 != ledger.protocol_sha256
        or receipt.operation_id != protocol.operations[0].operation_id
        or receipt.source_recipe_sha256 != protocol.sources[0].content_sha256()
        or receipt.runtime != protocol.runtime
        or receipt.source != protocol.generated.source
        or receipt.generated_receipt_sha256 != protocol.generated.content_sha256()
        or receipt.planning_receipt_sha256 != protocol.planned.content_sha256()
        or receipt.generation_cost != protocol.planned.generation_cost
        or receipt.planning_cost != protocol.planning_cost
        or finished is None
        or finished.operation_id != receipt.operation_id
        or finished.result != "completed"
        or finished.evidence_sha256 != receipt.content_sha256()
    ):
        raise SnapshotError("resolved_development_reuse_receipt_not_completed")
    with regular_file(journal, "receipts/" + receipt.reservation_id + ".json") as stream:
        if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
            raise SnapshotError("resolved_development_private_reuse_receipt_required")
        raw = stream.read(512 * 1024 + 1)
    if raw != canonical_bytes(receipt.model_dump(mode="json")) + b"\n":
        raise SnapshotError("resolved_development_stored_reuse_receipt_mismatch")


def generate_resolved_development_variant(
    *,
    journal: Path,
    variant: Literal["demand", "physical"],
    producer: Path,
    producer_python: Path,
    output_root: Path,
) -> tuple[Path, Path, CampaignGeneratedParentReceipt]:
    ledger = variants_journal(journal)
    position = {"demand": 1, "physical": 2}.get(variant)
    if position is None:
        raise SnapshotError("resolved_development_ordinary_must_be_reused")
    return generate_campaign_parent(
        producer,
        producer_python,
        output_root,
        journal=journal,
        operation_id=ledger.protocol.operations[position].operation_id,
        plan=ledger.protocol.planned.preparation.generations[position],
    )


def resolved_generation_allowance(journal: Path) -> float:
    """Called by the original generator after reservation and before producer I/O.

    Reload the journal inside the reservation: an earlier inspection could
    precede the predecessor's completion and miss its actual cost.
    """
    ledger = variants_journal(journal)
    _bootstrap_unchanged(ledger.protocol)
    event = next(
        (
            e
            for e in ledger.events
            if e.kind == "finished" and e.operation_id == ledger.protocol.operations[0].operation_id
        ),
        None,
    )
    if event is None or event.result != "completed":
        raise SnapshotError("resolved_development_completed_reuse_required")
    with regular_file(journal, "receipts/" + str(event.reservation_id) + ".json") as stream:
        receipt = ReusedDevelopmentParentReceipt.model_validate_json(stream.read(512 * 1024 + 1))
    validate_reused_development_parent(journal, receipt)
    return remaining_wall_seconds(ledger)
