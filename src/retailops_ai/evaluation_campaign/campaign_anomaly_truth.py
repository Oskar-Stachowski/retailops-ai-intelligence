"""Audited ordinary Source truth: reserve before I/O, replay all native facts, then publish."""

import os
import stat
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import JsonValue, TypeAdapter

from retailops_ai.anomaly_evaluation.contract import OrdinaryTruth, TruthWindow
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_anomaly_truth_contract import (
    CampaignOrdinaryTruthPlan,
    CampaignOrdinaryTruthReceipt,
    OrdinarySourceVerification,
)
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignCost,
    CampaignJournal,
    CampaignOperationPlan,
    CampaignSourceRecipe,
)
from retailops_ai.evaluation_campaign.campaign_export import MAX_RECEIPT_BYTES, _store_receipt
from retailops_ai.evaluation_campaign.campaign_export_contract import CampaignGeneratedParentReceipt
from retailops_ai.evaluation_campaign.campaign_fit import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_generation import (
    _environment,
    _producer_pin,
    validate_completed_generation,
)
from retailops_ai.evaluation_campaign.campaign_generation_contract import CampaignGenerationPlan
from retailops_ai.evaluation_campaign.campaign_generation_monitor import monitor, scratch_bytes
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_selection_evidence import (
    verify_completed_campaign_selection,
)
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    decode_json,
    read_bytes,
    regular_file,
)
from retailops_ai.source_snapshot.publish import fsync_tree


def _operation(ledger: CampaignJournal, operation_id: str) -> CampaignOperationPlan:
    operation = next(
        (o for o in ledger.protocol.operations if o.operation_id == operation_id), None
    )
    if operation is None or (operation.action, operation.use_case, operation.role) != (
        "source_read",
        "source",
        "all_parent_data",
    ):
        raise SnapshotError("campaign_ordinary_truth_requires_parent_read_operation")
    return operation


def _binding(
    ledger: CampaignJournal,
    operation: CampaignOperationPlan,
    plan: CampaignOrdinaryTruthPlan,
    generated: CampaignGeneratedParentReceipt,
    generation_plan: CampaignGenerationPlan,
) -> CampaignSourceRecipe:
    generation = next(
        (o for o in ledger.protocol.operations if o.operation_id == plan.generation_operation_id),
        None,
    )
    if (
        operation.execution_recipe_sha256 != plan.content_sha256()
        or operation.source_recipe_sha256 != plan.source_recipe_sha256
        or operation.phase != plan.phase
        or generated.source_recipe_sha256 != plan.source_recipe_sha256
        or generated.operation_id != plan.generation_operation_id
        or plan.generation_operation_id not in operation.prerequisites
        or generation is None
        or generation.action != "source_generate"
        or generation.execution_recipe_sha256 != generation_plan.content_sha256()
        or generation_plan.entrypoint != "cached_inventory_v2"
        or generation_plan.scenario_plan is not None
    ):
        raise SnapshotError("campaign_ordinary_truth_audited_generation_ancestry")
    source = next(
        s for s in ledger.protocol.sources if s.content_sha256() == plan.source_recipe_sha256
    )
    generation_plan.bind(source)
    if (
        source.phase != plan.phase
        or not source.history.start <= plan.window.start <= plan.window.end <= source.history.end
        or generated.source.source_parameters != generation_plan.resolved_parameters
    ):
        raise SnapshotError("campaign_ordinary_truth_frozen_source_or_window")
    return source


def _truth(
    verified: OrdinarySourceVerification,
    raw_windows: Any,
    generation_sha256: str,
) -> OrdinaryTruth:
    windows = TypeAdapter(tuple[TruthWindow, ...]).validate_json(canonical_bytes(raw_windows))
    if (
        canonical_sha256([w.model_dump(mode="json") for w in windows])
        != verified.complete_windows_sha256
        or len(windows) != verified.complete_window_count
        or sum((w.window.end - w.window.start).days + 1 for w in windows)
        != verified.complete_observation_count
        or any(
            not verified.window.start <= w.window.start <= w.window.end <= verified.window.end
            for w in windows
        )
    ):
        raise SnapshotError("campaign_ordinary_truth_native_census_binding")
    return OrdinaryTruth(
        source_dataset_id=verified.source_dataset_id,
        source_verification_sha256=verified.content_sha256(),
        source_generation_receipt_sha256=generation_sha256,
        complete_windows=windows,
    )


def _verify_bundle(bundle: Path, receipt: CampaignOrdinaryTruthReceipt) -> OrdinaryTruth:
    files, size = _bundle_inventory(bundle, receipt.plan.max_truth_bytes + 2 * 1024**2)
    if files != receipt.artifact_files or size != receipt.artifact_bytes:
        raise SnapshotError("campaign_ordinary_truth_bundle_inventory")
    plan = CampaignOrdinaryTruthPlan.model_validate_json(read_bytes(bundle, "plan.json"))
    verified = OrdinarySourceVerification.model_validate_json(
        read_bytes(bundle, "verification.json")
    )
    truth = OrdinaryTruth.model_validate_json(
        read_bytes(bundle, "truth.json", receipt.plan.max_truth_bytes)
    )
    expected = _truth(
        verified,
        [w.model_dump(mode="json") for w in truth.complete_windows],
        receipt.generated_parent_receipt_sha256,
    )
    if (
        plan != receipt.plan
        or verified.window != plan.window
        or truth != expected
        or truth.source_dataset_id != receipt.source_dataset_id
        or truth.source_verification_sha256 != receipt.source_verification_sha256
        or canonical_sha256(truth.model_dump(mode="json")) != receipt.truth_sha256
    ):
        raise SnapshotError("campaign_ordinary_truth_bundle_binding")
    return truth


def read_campaign_ordinary_truth(
    source_root: Path,
    source_dataset: Path,
    producer_python: Path,
    output_root: Path,
    *,
    journal: Path,
    operation_id: str,
    plan: CampaignOrdinaryTruthPlan,
    generated: CampaignGeneratedParentReceipt,
    generation_plan: CampaignGenerationPlan,
    selection_bundles: dict[str, Path] | None = None,
) -> tuple[Path, CampaignOrdinaryTruthReceipt]:
    ledger = campaign_journal.inspect(journal)
    operation = _operation(ledger, operation_id)
    started = perf_counter()
    with campaign_journal.audited_operation(journal, operation_id) as handle:
        plan = CampaignOrdinaryTruthPlan.model_validate_json(plan.model_dump_json())
        generated = CampaignGeneratedParentReceipt.model_validate_json(generated.model_dump_json())
        generation_plan = CampaignGenerationPlan.model_validate_json(
            generation_plan.model_dump_json()
        )
        source = _binding(ledger, operation, plan, generated, generation_plan)
        validate_completed_generation(journal, generated)
        selection_sha256 = None
        if plan.phase == "final":
            selection_sha256, _ = verify_completed_campaign_selection(
                journal, selection_bundles or {}
            )
        _producer_pin(source_root, source.producer_commit)
        checked_directory(source_dataset)
        checked_directory(output_root)
        root = output_root / str(handle.reservation.reservation_id)
        root.mkdir(mode=0o700)
        (root / "tmp").mkdir(mode=0o700)
        write(
            root / "request.json",
            {
                "producer_commit": source.producer_commit,
                "producer_lock_sha256": source.producer_lock_sha256,
                "exporter_lock_sha256": generation_plan.exporter_lock_sha256,
                "source_dataset_id": generated.source.parent.source_dataset_id,
                "resolved_parameters": generated.source.source_parameters,
                "window": plan.window.model_dump(mode="json"),
                "max_source_bytes": plan.max_source_bytes,
                "max_source_rows": plan.max_source_rows,
            },
        )
        worker = Path(__file__).with_name("campaign_anomaly_truth_worker.py")
        measured = monitor(
            [
                str(producer_python.absolute()),
                "-I",
                "-B",
                str(worker),
                str(source_root.absolute()),
                str(source_dataset.absolute()),
                str(root),
            ],
            root=root,
            log=root / "verification.log",
            env=_environment(root),
            scratch=(root,),
            resources=plan.resources,
            deadline=started + plan.resources.wall_seconds,
        )
        write(root / "monitor.json", measured)
        peak = measured["sampled_tree_peak_rss_bytes"]
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
        )
        if measured["status"] != "passed":
            raise SnapshotError("campaign_ordinary_truth_native_verification_failed")
        resources = read(root / "truth-worker-resources.json")
        if (
            type(resources.get("worker_peak_rss_bytes")) is not int
            or resources["worker_peak_rss_bytes"] <= 0
        ):
            raise SnapshotError("campaign_ordinary_truth_worker_memory_missing")
        peak = max(peak or 0, resources["worker_peak_rss_bytes"])
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
        )
        if peak > plan.resources.tree_rss_bytes:
            raise SnapshotError("campaign_ordinary_truth_worker_memory_budget")
        verified = OrdinarySourceVerification.model_validate_json(
            read_bytes(root, "ordinary-source-verification.json")
        )
        if (
            verified.source_dataset_id != generated.source.parent.source_dataset_id
            or verified.resolved_parameters != generated.source.source_parameters
            or verified.producer_commit != source.producer_commit
            or verified.producer_lock_sha256 != source.producer_lock_sha256
            or verified.exporter_lock_sha256 != generation_plan.exporter_lock_sha256
            or verified.window != plan.window
        ):
            raise SnapshotError("campaign_ordinary_truth_verified_source_binding")
        windows = decode_json(
            read_bytes(root, "ordinary-source-windows.json", plan.max_truth_bytes)
        )["complete_windows"]
        truth = _truth(verified, windows, generated.content_sha256())
        if len(canonical_bytes(truth.model_dump(mode="json"))) + 1 > plan.max_truth_bytes:
            raise SnapshotError("campaign_ordinary_truth_artifact_budget")
        bundle = root / "bundle"
        bundle.mkdir(mode=0o700)
        write(bundle / "truth.json", truth.model_dump(mode="json"))
        write(bundle / "verification.json", verified.model_dump(mode="json"))
        write(bundle / "plan.json", plan.model_dump(mode="json"))
        files, size = _bundle_inventory(bundle, plan.max_truth_bytes + 2 * 1024**2)
        receipt = CampaignOrdinaryTruthReceipt(
            protocol_sha256=ledger.protocol_sha256,
            operation_id=operation_id,
            reservation_id=str(handle.reservation.reservation_id),
            runtime_code_sha256=ledger.protocol.runtime.code_sha256,
            plan=plan,
            generated_parent_receipt_sha256=generated.content_sha256(),
            selection_sha256=selection_sha256,
            source_dataset_id=verified.source_dataset_id,
            source_verification_sha256=verified.content_sha256(),
            truth_sha256=canonical_sha256(truth.model_dump(mode="json")),
            artifact_files=files,
            artifact_bytes=size,
            worker_evidence=TypeAdapter(dict[str, JsonValue]).validate_python(
                {"monitor": measured, "worker": resources}
            ),
        )
        _verify_bundle(bundle, receipt)
        fsync_tree(root)
        if (
            perf_counter() - started > plan.resources.wall_seconds
            or scratch_bytes((root,)) > plan.resources.scratch_bytes
        ):
            raise SnapshotError("campaign_ordinary_truth_completion_resource_budget")
        _store_receipt(journal, receipt)
        handle.evidence_sha256 = receipt.content_sha256()
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=peak,
            artifact_bytes=size,
        )
    validate_completed_ordinary_truth(journal, receipt)
    return bundle, receipt


def validate_completed_ordinary_truth(journal: Path, receipt: CampaignOrdinaryTruthReceipt) -> None:
    receipt = CampaignOrdinaryTruthReceipt.model_validate_json(receipt.model_dump_json())
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
    if (
        receipt.protocol_sha256 != ledger.protocol_sha256
        or receipt.runtime_code_sha256 != ledger.protocol.runtime.code_sha256
        or operation.execution_recipe_sha256 != receipt.plan.content_sha256()
        or operation.source_recipe_sha256 != receipt.plan.source_recipe_sha256
        or operation.phase != receipt.plan.phase
        or receipt.plan.generation_operation_id not in operation.prerequisites
        or completion is None
        or completion.operation_id != receipt.operation_id
        or completion.result != "completed"
        or completion.evidence_sha256 != receipt.content_sha256()
        or completion.cost is None
        or completion.cost.artifact_bytes != receipt.artifact_bytes
    ):
        raise SnapshotError("campaign_ordinary_truth_receipt_not_completed")
    with regular_file(journal, "receipts/" + receipt.reservation_id + ".json") as stream:
        if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
            raise SnapshotError("campaign_ordinary_truth_private_receipt_required")
        raw = stream.read(MAX_RECEIPT_BYTES + 1)
    if raw != canonical_bytes(receipt.model_dump(mode="json")) + b"\n":
        raise SnapshotError("campaign_ordinary_truth_stored_receipt_mismatch")


def verify_campaign_ordinary_truth(
    bundle: Path,
    *,
    journal: Path,
    receipt: CampaignOrdinaryTruthReceipt,
    selection_bundles: dict[str, Path] | None = None,
) -> OrdinaryTruth:
    validate_completed_ordinary_truth(journal, receipt)
    if receipt.plan.phase == "final":
        selection_sha256, _ = verify_completed_campaign_selection(journal, selection_bundles or {})
        if receipt.selection_sha256 != selection_sha256:
            raise SnapshotError("campaign_ordinary_truth_selection_mismatch")
    return _verify_bundle(bundle, receipt)
