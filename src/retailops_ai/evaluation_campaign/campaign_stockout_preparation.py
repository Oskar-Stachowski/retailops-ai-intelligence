"""Charge complete stockout preparation before reads and accept only verified artifacts."""

import os
import stat
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import JsonValue, TypeAdapter

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost
from retailops_ai.evaluation_campaign.campaign_export import MAX_RECEIPT_BYTES, _store_receipt
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_fit_artifact import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_generation import (
    _environment,
    validate_completed_generation,
)
from retailops_ai.evaluation_campaign.campaign_generation_monitor import monitor, scratch_bytes
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_role_protocol import (
    RoleAwareCampaignJournal,
    RoleAwareCampaignOperation,
)
from retailops_ai.evaluation_campaign.campaign_stockout_preparation_contract import (
    CampaignStockoutPreparationPlan,
    CampaignStockoutPreparationReceipt,
)
from retailops_ai.evaluation_campaign.campaign_stockout_role_contract import (
    CampaignStockoutRoleArtifactPlan,
)
from retailops_ai.source_snapshot.files import SnapshotError, checked_directory, regular_file
from retailops_ai.source_snapshot.publish import fsync_tree


def _operation(ledger: RoleAwareCampaignJournal, operation_id: str) -> RoleAwareCampaignOperation:
    operation = next(
        (o for o in ledger.protocol.operations if o.operation_id == operation_id), None
    )
    if operation is None or (
        operation.phase != "development"
        or operation.action != "source_read"
        or operation.use_case != "source"
        or operation.role != "all_parent_data"
        or operation.read_roles != ("all_parent_data",)
    ):
        raise SnapshotError("stockout_preparation_requires_development_parent_read")
    return operation


def _binding(
    ledger: RoleAwareCampaignJournal,
    operation: RoleAwareCampaignOperation,
    plan: CampaignStockoutPreparationPlan,
    generated: CampaignGeneratedParentReceipt,
) -> None:
    if (
        operation.execution_recipe_sha256 != plan.content_sha256()
        or operation.source_recipe_sha256 != plan.source_recipe_sha256
        or generated.source_recipe_sha256 != plan.source_recipe_sha256
        or generated.protocol_sha256 != ledger.protocol_sha256
        or generated.runtime != ledger.protocol.runtime
        or generated.operation_id != plan.generation_operation_id
        or plan.generation_operation_id not in operation.prerequisites
        or plan.policy.windows != ledger.protocol.development_roles.windows
    ):
        raise SnapshotError("stockout_preparation_frozen_plan_or_parent_mismatch")
    source = next(
        s for s in ledger.protocol.sources if s.content_sha256() == plan.source_recipe_sha256
    )
    p = generated.source.source_parameters
    expected = {
        "profile": source.profile,
        "seed": source.seed,
        "days": (source.history.end - source.history.start).days + 1,
        "products": source.products,
        "stores": source.selling_pairs,
        "warehouses": source.stock_locations,
        "start_date": source.history.start.isoformat(),
        "end_date": source.history.end.isoformat(),
        "business_timezone": "UTC",
    }
    if (
        source.phase != "development"
        or source.generation_config_sha256 != canonical_sha256(p)
        or any(type(p.get(k)) is not type(v) or p.get(k) != v for k, v in expected.items())
    ):
        raise SnapshotError("stockout_preparation_original_source_configuration_mismatch")
    plan.public_plan(generated)


def prepare_campaign_stockout(
    snapshot: Path,
    curated: Path,
    qualification: Path,
    output_root: Path,
    *,
    journal: Path,
    operation_id: str,
    plan: CampaignStockoutPreparationPlan,
    generated: CampaignGeneratedParentReceipt,
) -> tuple[Path, CampaignStockoutPreparationReceipt]:
    ledger = campaign_journal.inspect(journal)
    if not isinstance(ledger, RoleAwareCampaignJournal):
        raise SnapshotError("stockout_preparation_requires_role_aware_journal")
    operation = _operation(ledger, operation_id)
    started = perf_counter()
    with campaign_journal.audited_operation(journal, operation_id) as handle:
        plan = CampaignStockoutPreparationPlan.model_validate(plan.model_dump(mode="python"))
        generated = CampaignGeneratedParentReceipt.model_validate(
            generated.model_dump(mode="python")
        )
        _binding(ledger, operation, plan, generated)
        validate_completed_generation(journal, generated)
        checked_directory(output_root)
        root = output_root / str(handle.reservation.reservation_id)
        root.mkdir(mode=0o700)
        (root / "tmp").mkdir(mode=0o700)
        write(
            root / "request.json",
            {
                "snapshot": str(snapshot.absolute()),
                "curated": str(curated.absolute()),
                "qualification": str(qualification.absolute()),
                "plan": plan.model_dump(mode="json"),
                "generated": generated.model_dump(mode="json"),
            },
        )
        peak = None
        phases: list[dict[str, Any]] = []
        for phase in ("public", "roles", "verify"):
            measured = monitor(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(Path(__file__).with_name("campaign_stockout_preparation_entry.py")),
                    phase,
                    str(root),
                ],
                root=root,
                log=root / (phase + ".log"),
                env=_environment(root),
                scratch=(root,),
                resources=plan.resources,
                deadline=started + plan.resources.wall_seconds,
            )
            measured_peak = measured["sampled_tree_peak_rss_bytes"]
            if measured_peak is not None:
                peak = max(peak or 0, int(measured_peak))
            write(root / (phase + "-resources.json"), measured)
            handle.cost = CampaignCost(
                wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
            )
            if measured["status"] != "passed":
                raise SnapshotError("stockout_preparation_phase_failed_" + phase)
            result = read(root / (phase + ".json"))
            worker_peak = result.get("worker_peak_rss_bytes")
            if type(worker_peak) is not int or worker_peak <= 0:
                raise SnapshotError("stockout_preparation_worker_resource_evidence_missing")
            peak = max(peak or 0, worker_peak)
            handle.cost = CampaignCost(
                wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
            )
            if peak > plan.resources.tree_rss_bytes:
                raise SnapshotError("stockout_preparation_worker_peak_rss_limit")
            phases.append({"phase": phase, "monitor": measured, "worker": result})
        result = phases[-1]["worker"]
        receipt = CampaignStockoutPreparationReceipt(
            protocol_sha256=ledger.protocol_sha256,
            operation_id=operation_id,
            reservation_id=str(handle.reservation.reservation_id),
            plan=plan,
            generated=generated,
            role_plan=CampaignStockoutRoleArtifactPlan.model_validate_json(
                canonical_bytes(result["role_plan"])
            ),
            **{
                k: result[k]
                for k in (
                    "public_dataset_id",
                    "dataset_id",
                    "manifest_sha256",
                    "artifact_bytes",
                    "artifact_files",
                )
            },
            worker_evidence=TypeAdapter(dict[str, JsonValue]).validate_python({"phases": phases}),
        )
        files, size = _bundle_inventory(root / "bundle", plan.max_role_bytes)
        if files != receipt.artifact_files or size != receipt.artifact_bytes:
            raise SnapshotError("stockout_preparation_artifact_changed_after_verification")
        fsync_tree(root)
        if (
            perf_counter() - started > plan.resources.wall_seconds
            or scratch_bytes((root,)) > plan.resources.scratch_bytes
        ):
            raise SnapshotError("stockout_preparation_completion_resource_limit")
        _store_receipt(journal, receipt)
        handle.evidence_sha256 = receipt.content_sha256()
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=peak,
            artifact_bytes=size,
        )
    validate_completed_stockout_preparation(journal, receipt)
    return root / "bundle", receipt


def validate_completed_stockout_preparation(
    journal: Path, receipt: CampaignStockoutPreparationReceipt
) -> None:
    """Check original durable ordering and evidence without opening data or labels."""
    receipt = CampaignStockoutPreparationReceipt.model_validate(receipt.model_dump(mode="python"))
    ledger = campaign_journal.inspect(journal)
    if not isinstance(ledger, RoleAwareCampaignJournal):
        raise SnapshotError("stockout_preparation_requires_role_aware_journal")
    operation = _operation(ledger, receipt.operation_id)
    _binding(ledger, operation, receipt.plan, receipt.generated)
    validate_completed_generation(journal, receipt.generated)
    completion = next(
        (
            e
            for e in ledger.events
            if e.kind == "finished" and e.reservation_id == receipt.reservation_id
        ),
        None,
    )
    if (
        ledger.protocol_sha256 != receipt.protocol_sha256
        or completion is None
        or (
            completion.operation_id != receipt.operation_id
            or completion.result != "completed"
            or completion.evidence_sha256 != receipt.content_sha256()
        )
    ):
        raise SnapshotError("stockout_preparation_receipt_not_completed")
    with regular_file(journal, "receipts/" + receipt.reservation_id + ".json") as stream:
        if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
            raise SnapshotError("stockout_preparation_private_receipt_required")
        raw = stream.read(MAX_RECEIPT_BYTES + 1)
    if raw != canonical_bytes(receipt.model_dump(mode="json")) + b"\n":
        raise SnapshotError("stockout_preparation_stored_receipt_mismatch")
