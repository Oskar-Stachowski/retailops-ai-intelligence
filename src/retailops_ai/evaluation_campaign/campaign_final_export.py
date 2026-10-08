"""Reserve a final parent only after selection freeze and verified generation."""

import os
import stat
from pathlib import Path
from time import perf_counter

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignCost,
    CampaignJournal,
    CampaignOperationPlan,
    CampaignSourceRecipe,
)
from retailops_ai.evaluation_campaign.campaign_export import (
    MAX_RECEIPT_BYTES,
    _producer,
    _store_receipt,
)
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_final_contract import (
    CampaignFinalExportPlan,
    CampaignFinalExportReceipt,
)
from retailops_ai.evaluation_campaign.campaign_generation import validate_completed_generation
from retailops_ai.evaluation_campaign.campaign_selection_evidence import (
    verify_completed_campaign_selection,
)
from retailops_ai.evaluation_campaign.final_forecast import (
    _build_final_forecast,
    verify_final_forecast,
)
from retailops_ai.evaluation_campaign.physical_forecast import _physical_bytes
from retailops_ai.evaluation_campaign.source_replay import (
    _open_verified_source_parent,
    physical_limits,
)
from retailops_ai.source_snapshot.files import SnapshotError, file_hash, regular_file


def _operation(ledger: CampaignJournal, operation_id: str) -> CampaignOperationPlan:
    operation = next(
        (o for o in ledger.protocol.operations if o.operation_id == operation_id), None
    )
    if operation is None or (
        operation.phase != "final"
        or operation.action != "source_read"
        or operation.use_case != "source"
        or operation.role != "all_parent_data"
    ):
        raise SnapshotError("final_export_requires_final_parent_read")
    return operation


def _selection(ledger: CampaignJournal) -> str:
    event = next((e for e in ledger.events if e.kind == "selection_frozen"), None)
    if event is None or event.selection is None:
        raise SnapshotError("final_export_requires_selection_freeze")
    return canonical_sha256(event.selection.model_dump(mode="json"))


def _binding(
    ledger: CampaignJournal,
    operation: CampaignOperationPlan,
    plan: CampaignFinalExportPlan,
    generated: CampaignGeneratedParentReceipt,
) -> CampaignSourceRecipe:
    _selection(ledger)
    if (
        operation.execution_recipe_sha256 != plan.content_sha256()
        or operation.source_recipe_sha256 != plan.source_recipe_sha256
        or generated.source_recipe_sha256 != plan.source_recipe_sha256
        or generated.protocol_sha256 != ledger.protocol_sha256
        or generated.runtime != ledger.protocol.runtime
        or generated.operation_id != plan.generation_operation_id
        or generated.operation_id not in operation.prerequisites
    ):
        raise SnapshotError("final_export_frozen_plan_or_generation_binding_mismatch")
    generation = next(
        (o for o in ledger.protocol.operations if o.operation_id == generated.operation_id), None
    )
    if (
        generation is None
        or generation.action != "source_generate"
        or generation.phase != "final"
        or generation.source_recipe_sha256 != plan.source_recipe_sha256
    ):
        raise SnapshotError("final_export_generation_operation_mismatch")
    source = next(
        s for s in ledger.protocol.sources if s.content_sha256() == plan.source_recipe_sha256
    )
    p = generated.source.source_parameters
    expected = {
        "profile": "ai-training",
        "seed": source.seed,
        "days": 730,
        "products": 200,
        "stores": 10,
        "warehouses": 4,
        "start_date": source.history.start.isoformat(),
        "end_date": source.history.end.isoformat(),
        "business_timezone": "UTC",
    }
    if (
        source.phase != "final"
        or source.exporter_lock_sha256 is None
        or source.generation_config_sha256 != canonical_sha256(p)
        or any(type(p.get(k)) is not type(v) or p.get(k) != v for k, v in expected.items())
        or source.evaluation_origins is None
        or source.evaluation_origins.model_dump() != plan.origins.model_dump()
        or source.label_delay_days != plan.label_delay_days
    ):
        raise SnapshotError("final_export_canonical_source_or_origin_mismatch")
    plan.bind(generated.source)
    return source


def validate_completed_final_export(journal: Path, receipt: CampaignFinalExportReceipt) -> None:
    """Validate durable local completion; this grants no new parent/test read."""
    receipt = CampaignFinalExportReceipt.model_validate_json(
        canonical_bytes(receipt.model_dump(mode="json"))
    )
    ledger = campaign_journal.inspect(journal)
    operation = _operation(ledger, receipt.operation_id)
    completion = next(
        (
            e
            for e in ledger.events
            if e.kind == "finished" and e.reservation_id == receipt.reservation_id
        ),
        None,
    )
    generation = next(
        (
            e
            for e in ledger.events
            if e.kind == "finished"
            and e.operation_id == receipt.plan.generation_operation_id
            and e.result == "completed"
        ),
        None,
    )
    if (
        ledger.protocol_sha256 != receipt.protocol_sha256
        or operation.execution_recipe_sha256 != receipt.plan.content_sha256()
        or operation.source_recipe_sha256 != receipt.plan.source_recipe_sha256
        or ledger.protocol.runtime.code_sha256 != receipt.runtime_code_sha256
        or receipt.recipe.source.resource_qualified is not False
        or _selection(ledger) != receipt.selection_sha256
        or generation is None
        or generation.evidence_sha256 != receipt.generated_parent_receipt_sha256
        or completion is None
        or completion.operation_id != receipt.operation_id
        or completion.result != "completed"
        or completion.evidence_sha256 != receipt.content_sha256()
        or completion.cost is None
        or completion.cost.artifact_bytes != receipt.artifact_bytes
    ):
        raise SnapshotError("final_export_receipt_not_completed")
    try:
        with regular_file(journal, "receipts/" + receipt.reservation_id + ".json") as stream:
            if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
                raise SnapshotError("final_export_private_receipt_required")
            raw = stream.read(MAX_RECEIPT_BYTES + 1)
    except OSError:
        raise SnapshotError("final_export_receipt_unavailable") from None
    if raw != canonical_bytes(receipt.model_dump(mode="json")) + b"\n":
        raise SnapshotError("final_export_stored_receipt_mismatch")


def export_final_forecast(
    snapshot: Path,
    curated: Path,
    output_root: Path,
    *,
    journal: Path,
    operation_id: str,
    plan: CampaignFinalExportPlan,
    generated: CampaignGeneratedParentReceipt,
    selection_bundles: dict[str, Path] | None = None,
) -> tuple[Path, CampaignFinalExportReceipt]:
    ledger = campaign_journal.inspect(journal)
    operation = _operation(ledger, operation_id)
    started = perf_counter()
    with campaign_journal.audited_operation(journal, operation_id) as handle:
        plan = CampaignFinalExportPlan.model_validate_json(
            canonical_bytes(plan.model_dump(mode="json"))
        )
        generated = CampaignGeneratedParentReceipt.model_validate_json(
            canonical_bytes(generated.model_dump(mode="json"))
        )
        source = _binding(ledger, operation, plan, generated)
        try:
            validate_completed_generation(journal, generated)
        except OSError:
            raise SnapshotError("final_export_generation_receipt_unavailable") from None
        digest, _ = verify_completed_campaign_selection(journal, selection_bundles or {})
        if digest != _selection(ledger):
            raise SnapshotError("final_export_completed_selection_binding_mismatch")
        recipe = plan.bind(generated.source)
        with _open_verified_source_parent(
            snapshot,
            curated,
            generated.source,
            limits=physical_limits(generated.source),
            runtime=ledger.protocol.runtime,
        ) as replay:
            _producer(replay, source)
            destination = _build_final_forecast(replay, recipe, output_root)
            manifest = verify_final_forecast(destination)
            descriptor = manifest.descriptor
            if (
                descriptor.recipe != recipe
                or descriptor.runtime != ledger.protocol.runtime
                or descriptor.snapshot_inventory_sha256 != replay.snapshot_inventory_sha256
                or descriptor.curated_inventory_sha256 != replay.curated_inventory_sha256
                or descriptor.logical_curated_sha256 != replay.logical_curated_sha256
            ):
                raise SnapshotError("final_export_manifest_replay_binding_mismatch")
            receipt = CampaignFinalExportReceipt(
                protocol_sha256=ledger.protocol_sha256,
                operation_id=operation_id,
                reservation_id=str(handle.reservation.reservation_id),
                plan=plan,
                generated_parent_receipt_sha256=generated.content_sha256(),
                selection_sha256=_selection(ledger),
                recipe=recipe,
                dataset_id=manifest.dataset_id,
                manifest_sha256=file_hash(destination, "manifest.json")[1],
                runtime_code_sha256=ledger.protocol.runtime.code_sha256,
                snapshot_inventory_sha256=replay.snapshot_inventory_sha256,
                curated_inventory_sha256=replay.curated_inventory_sha256,
                logical_curated_sha256=replay.logical_curated_sha256,
                population_rows=descriptor.population.row_count,
                artifact_bytes=_physical_bytes(destination, plan.max_artifact_bytes),
            )
            replay.check_parents()
        _store_receipt(journal, receipt)
        handle.evidence_sha256 = receipt.content_sha256()
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started, artifact_bytes=receipt.artifact_bytes
        )
    validate_completed_final_export(journal, receipt)
    return destination, receipt
