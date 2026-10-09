"""One charged ordinary generation, then one charged native planning read."""

import os
import stat
from pathlib import Path
from time import perf_counter
from typing import Any, Literal

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost
from retailops_ai.evaluation_campaign.campaign_export import _store_receipt
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_generation import (
    _environment,
    _producer_pin,
    generate_campaign_parent,
    validate_completed_generation,
)
from retailops_ai.evaluation_campaign.campaign_generation_monitor import monitor, scratch_bytes
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.development_planning_contract import (
    NativeDevelopmentPlanningJournal,
    NativeDevelopmentPlanningReceipt,
    ResolvedNativeDevelopmentPlanningReceipt,
    parse_native_planning_receipt,
)
from retailops_ai.evaluation_campaign.development_profiles import (
    DevelopmentProfilePreparation,
    prepare_development_profile,
)
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    file_hash,
    regular_file,
)


def planning_journal(journal: Path) -> NativeDevelopmentPlanningJournal:
    ledger = campaign_journal.inspect(journal)
    if not isinstance(ledger, NativeDevelopmentPlanningJournal):
        raise SnapshotError("native_planning_requires_separate_journal")
    return ledger


def generate_ordinary_for_planning(
    *,
    journal: Path,
    producer: Path,
    producer_python: Path,
    output_root: Path,
) -> tuple[Path, Path, CampaignGeneratedParentReceipt]:
    ledger = planning_journal(journal)
    return generate_campaign_parent(
        producer,
        producer_python,
        output_root,
        journal=journal,
        operation_id=ledger.protocol.planning.generation_operation_id,
        plan=ledger.protocol.generation,
    )


def _verified_bundle(
    bundle: dict[str, Any], request: dict[str, Any]
) -> dict[Literal["demand", "physical"], str]:
    selection = request["selection"]
    plans, hashes = bundle.get("plans"), bundle.get("plan_sha256")
    if (
        bundle.get("version") != request["planning"]["producer_planner_version"]
        or bundle.get("data_class") != "simulation_truth"
        or bundle.get("recipe") != selection
        or bundle.get("recipe_sha256") != canonical_sha256(selection)
        or bundle.get("source_dataset_id") != selection["source_dataset_id"]
        or not isinstance(plans, dict)
        or set(plans) != {"demand", "physical"}
        or not isinstance(hashes, dict)
        or set(hashes) != {"demand", "physical"}
        or any(hashes[k] != canonical_sha256(plans[k]) for k in plans)
        or any(
            bundle.get(k) is not False
            for k in (
                "realized_effects_verified",
                "critical_coverage_verified",
                "model_fit_authorized",
                "final_test_access_authorized",
                "stage_ready",
            )
        )
    ):
        raise SnapshotError("native_planning_bundle_binding_mismatch")
    return {"demand": str(hashes["demand"]), "physical": str(hashes["physical"])}


def plan_native_development_scenarios(
    *,
    journal: Path,
    generated: CampaignGeneratedParentReceipt,
    raw_source: Path,
    producer: Path,
    producer_python: Path,
    output_root: Path,
) -> tuple[Path, ResolvedNativeDevelopmentPlanningReceipt]:
    """Reserve before any producer, raw Source or output access; never auto-retry.

    The original generation's verified identity and complete cold cost stay in
    the same journal. Its elapsed compute is subtracted from the total budget.
    Neither this read nor its private output grants trials or final access.
    """
    ledger = planning_journal(journal)
    protocol, planning = ledger.protocol, ledger.protocol.planning
    operation = protocol.operations[1]
    started = perf_counter()
    with campaign_journal.audited_operation(journal, operation.operation_id) as handle:
        validate_completed_generation(journal, generated)
        if generated.operation_id != planning.generation_operation_id:
            raise SnapshotError("native_planning_wrong_generation_operation")
        generation_cost = next(
            e.cost
            for e in ledger.events
            if e.kind == "finished" and e.reservation_id == generated.reservation_id
        )
        if (
            generation_cost is None
            or generation_cost.peak_process_tree_rss_bytes is None
            or generation_cost.artifact_bytes is None
        ):
            raise SnapshotError("native_planning_generation_cost_missing")
        available_seconds = min(
            planning.resources.wall_seconds,
            protocol.maximum_total_wall_seconds - generation_cost.wall_seconds,
        )
        if available_seconds <= 0:
            raise SnapshotError("native_planning_total_wall_budget_exhausted")
        source = protocol.sources[0]
        _producer_pin(producer, source.producer_commit)
        checked_directory(raw_source)
        checked_directory(output_root)
        root = output_root / str(handle.reservation.reservation_id)
        root.mkdir(mode=0o700)
        (root / "tmp").mkdir(mode=0o700)
        selection = {
            "version": planning.producer_planner_version,
            "source_dataset_id": generated.source.parent.source_dataset_id,
            "generation_sha256": source.generation_config_sha256,
            "roles": planning.scenario_roles(),
            "minimum_history_observations": 28,
            "selection": "native_ordinary_source_only_before_model_trials",
        }
        request = {
            "source": source.model_dump(mode="json"),
            "generation": protocol.generation.model_dump(mode="json"),
            "planning": planning.model_dump(mode="json"),
            "selection": selection,
            "raw_source": str(raw_source.absolute()),
        }
        write(root / "request.json", request)
        result = monitor(
            [
                str(producer_python),
                "-I",
                "-B",
                str(Path(__file__).with_name("development_planning_worker.py")),
                str(producer),
                str(root),
            ],
            root=root,
            log=root / "planning.log",
            env=_environment(root),
            scratch=(root,),
            resources=planning.resources,
            deadline=started + available_seconds,
        )
        write(root / "planning-resources.json", result)
        peak = result["sampled_tree_peak_rss_bytes"]
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=peak,
            artifact_bytes=scratch_bytes((root,)),
        )
        if result["status"] != "passed":
            raise SnapshotError("native_planning_worker_failed")
        reported_peak = read(root / "result.json")["worker_peak_rss_bytes"]
        if (
            type(reported_peak) is not int
            or reported_peak < 1
            or reported_peak > planning.resources.tree_rss_bytes
        ):
            raise SnapshotError("native_planning_worker_peak_rss_limit")
        peak = max(peak or 0, reported_peak)
        bundle_size, bundle_sha256 = file_hash(root, "native-plans.json")
        if bundle_size > planning.max_bundle_bytes:
            raise SnapshotError("native_planning_bundle_size_limit")
        bundle = read(root / "native-plans.json")
        hashes = _verified_bundle(bundle, request)
        resolved = prepare_development_profile(
            source.development_profile,
            producer_commit=source.producer_commit,
            producer_lock_sha256=source.producer_lock_sha256,
            exporter_lock_sha256=source.exporter_lock_sha256,
            scenario_plans=bundle["plans"],
            resources=protocol.generation.resources,
            label_delay_days=source.label_delay_days,
        )
        resolved = DevelopmentProfilePreparation.model_validate_json(
            resolved.model_copy(
                update={
                    "generations": tuple(
                        g.model_copy(
                            update={
                                "parent_budget": protocol.generation.parent_budget,
                                "chunk_rows": protocol.generation.chunk_rows,
                            }
                        )
                        for g in resolved.generations
                    )
                }
            ).model_dump_json()
        )
        if resolved.generations[0] != protocol.generation:
            raise SnapshotError("native_planning_resolved_ordinary_generation_changed")
        _producer_pin(producer, source.producer_commit)
        artifact_bytes = scratch_bytes((root,))
        if (
            perf_counter() - started > available_seconds
            or artifact_bytes > planning.resources.scratch_bytes
        ):
            raise SnapshotError("native_planning_completion_resource_limit")
        receipt = ResolvedNativeDevelopmentPlanningReceipt(
            protocol_sha256=ledger.protocol_sha256,
            source_recipe_sha256=source.content_sha256(),
            operation_id=operation.operation_id,
            reservation_id=str(handle.reservation.reservation_id),
            source_dataset_id=generated.source.parent.source_dataset_id,
            generation_receipt_sha256=generated.content_sha256(),
            generation_cost=generation_cost,
            planning_sha256=planning.content_sha256(),
            producer_planner_sha256=planning.producer_planner_sha256,
            bundle_sha256=bundle_sha256,
            bundle_bytes=bundle_size,
            plan_sha256=hashes,
            runtime=protocol.runtime,
            preparation=resolved,
        )
        _store_receipt(journal, receipt)
        handle.evidence_sha256 = receipt.content_sha256()
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=peak,
            artifact_bytes=artifact_bytes,
        )
    validate_completed_native_planning(journal, receipt)
    return root / "native-plans.json", receipt


def validate_completed_native_planning(
    journal: Path, receipt: NativeDevelopmentPlanningReceipt
) -> None:
    """Metadata-only validation of a stored receipt; new data reuse needs a new charge."""
    receipt = parse_native_planning_receipt(receipt)
    ledger = planning_journal(journal)
    operation = ledger.protocol.operations[1]
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
            and e.operation_id == ledger.protocol.planning.generation_operation_id
        ),
        None,
    )
    if (
        receipt.protocol_sha256 != ledger.protocol_sha256
        or receipt.runtime != ledger.protocol.runtime
        or receipt.operation_id != operation.operation_id
        or receipt.source_recipe_sha256 != operation.source_recipe_sha256
        or receipt.planning_sha256 != operation.execution_recipe_sha256
        or receipt.producer_planner_sha256 != ledger.protocol.planning.producer_planner_sha256
        or completion is None
        or completion.operation_id != receipt.operation_id
        or completion.result != "completed"
        or completion.evidence_sha256 != receipt.content_sha256()
        or generation is None
        or generation.result != "completed"
        or generation.evidence_sha256 != receipt.generation_receipt_sha256
        or generation.cost != receipt.generation_cost
    ):
        raise SnapshotError("native_planning_receipt_not_completed")
    with regular_file(journal, "receipts/" + str(generation.reservation_id) + ".json") as stream:
        parent_raw = stream.read(512 * 1024 + 1)
    generated = CampaignGeneratedParentReceipt.model_validate_json(parent_raw)
    validate_completed_generation(journal, generated)
    if generated.source.parent.source_dataset_id != receipt.source_dataset_id:
        raise SnapshotError("native_planning_generation_source_identity_mismatch")
    with regular_file(journal, "receipts/" + receipt.reservation_id + ".json") as stream:
        if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
            raise SnapshotError("native_planning_private_receipt_required")
        raw = stream.read(512 * 1024 + 1)
    if raw != canonical_bytes(receipt.model_dump(mode="json")) + b"\n":
        raise SnapshotError("native_planning_stored_receipt_mismatch")
