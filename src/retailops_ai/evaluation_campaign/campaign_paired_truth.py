"""Audited paired truth: freeze both parents, charge one operation, replay both in full."""

import os
import stat
from datetime import date
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import JsonValue, TypeAdapter

from retailops_ai.anomaly_evaluation.contract import Episode, PairedTruth, TruthWindow
from retailops_ai.anomaly_evaluation.paired_source_comparison import POLICY, SOURCE_TABLES
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_anomaly_truth import _operation
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignCost,
    CampaignJournal,
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
from retailops_ai.evaluation_campaign.campaign_paired_truth_contract import (
    CampaignPairedTruthPlan,
    CampaignPairedTruthReceipt,
    PairedSourceVerification,
)
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


def _parent_before(
    ledger: CampaignJournal,
    operation_id: str,
    source_recipe_sha256: str,
    receipt_sha256: str,
    sequence: int,
) -> None:
    operation = next(
        (o for o in ledger.protocol.operations if o.operation_id == operation_id), None
    )
    if (
        operation is None
        or operation.action != "source_generate"
        or operation.source_recipe_sha256 != source_recipe_sha256
        or not any(
            e.kind == "finished"
            and e.operation_id == operation_id
            and e.result == "completed"
            and e.evidence_sha256 == receipt_sha256
            and e.sequence < sequence
            for e in ledger.events
        )
    ):
        raise SnapshotError("campaign_paired_truth_parent_not_completed_before_reservation")


def _binding(
    ledger: CampaignJournal,
    operation_id: str,
    plan: CampaignPairedTruthPlan,
    ordinary: CampaignGeneratedParentReceipt,
    generated: CampaignGeneratedParentReceipt,
    ordinary_plan: CampaignGenerationPlan,
    generation_plan: CampaignGenerationPlan,
) -> CampaignSourceRecipe:
    operation = _operation(ledger, operation_id)
    sources = {s.content_sha256(): s for s in ledger.protocol.sources}
    if (
        operation.execution_recipe_sha256 != plan.content_sha256()
        or operation.source_recipe_sha256 != plan.source_recipe_sha256
        or operation.phase != plan.phase
        or plan.generation_operation_id not in operation.prerequisites
        or plan.ordinary_source_recipe_sha256 not in sources
        or plan.source_recipe_sha256 not in sources
        or ordinary_plan.entrypoint != "cached_inventory_v2"
        or ordinary_plan.scenario_plan is not None
        or generation_plan.entrypoint != "planned_anomaly"
        or generation_plan.scenario_plan is None
        or canonical_sha256(generation_plan.scenario_plan) != plan.scenario_plan_sha256
        or ordinary_plan.resolved_parameters != generation_plan.resolved_parameters
        or ordinary_plan.exporter_lock_sha256 != generation_plan.exporter_lock_sha256
    ):
        raise SnapshotError("campaign_paired_truth_frozen_pair_binding")
    source = sources[plan.source_recipe_sha256]
    baseline = sources[plan.ordinary_source_recipe_sha256]
    if (
        source.phase != baseline.phase
        or source.phase != plan.phase
        or source.producer_commit != baseline.producer_commit
        or source.producer_lock_sha256 != baseline.producer_lock_sha256
        or source.history != baseline.history
        or source.seed != baseline.seed
        or not source.history.start <= plan.window.start <= plan.window.end <= source.history.end
    ):
        raise SnapshotError("campaign_paired_truth_source_pair_or_window")
    for parent, generation, digest, identifier in (
        (
            ordinary,
            ordinary_plan,
            plan.ordinary_source_recipe_sha256,
            plan.ordinary_generation_operation_id,
        ),
        (generated, generation_plan, plan.source_recipe_sha256, plan.generation_operation_id),
    ):
        generation.bind(sources[digest])
        parent_operation = next(
            (o for o in ledger.protocol.operations if o.operation_id == identifier), None
        )
        if (
            parent.operation_id != identifier
            or parent.source_recipe_sha256 != digest
            or parent.source.source_parameters != generation.resolved_parameters
            or parent_operation is None
            or parent_operation.execution_recipe_sha256 != generation.content_sha256()
        ):
            raise SnapshotError("campaign_paired_truth_generation_ancestry")
        _parent_before(ledger, identifier, digest, parent.content_sha256(), len(ledger.events) + 1)
    return source


def _truth(
    verified: PairedSourceVerification,
    comparison: dict[str, Any],
    raw_windows: Any,
    raw_episodes: Any,
    ordinary_sha256: str,
    generation_sha256: str,
) -> PairedTruth:
    for key, parent in (("ordinary", verified.ordinary), ("planned", verified.planned)):
        rows = comparison.get("parent_table_rows", {}).get(key, {})
        if (
            set(rows) != SOURCE_TABLES
            or any(type(value) is not int or value < 0 for value in rows.values())
            or sum(rows.values()) != parent.source_rows
        ):
            raise SnapshotError("campaign_paired_truth_comparison_inventory")
    windows = TypeAdapter(tuple[TruthWindow, ...]).validate_json(canonical_bytes(raw_windows))
    episodes = TypeAdapter(tuple[Episode, ...]).validate_json(canonical_bytes(raw_episodes))
    result = PairedTruth(
        source_dataset_id=verified.planned.source_dataset_id,
        ordinary_source_dataset_id=verified.ordinary.source_dataset_id,
        source_scenario_sha256=verified.source_scenario_sha256,
        source_verification_sha256=verified.content_sha256(),
        ordinary_generation_receipt_sha256=ordinary_sha256,
        source_generation_receipt_sha256=generation_sha256,
        comparison_policy_sha256=verified.comparison_policy_sha256,
        complete_windows=windows,
        episodes=episodes,
    )
    raw_complete = [w.model_dump(mode="json") for w in windows]
    clean = list(raw_complete)
    for episode in episodes:
        positive = {
            **episode.model_dump(
                mode="json",
                exclude={
                    "episode_id",
                    "business_type",
                    "first_evidence_available_at",
                    "label_available_at",
                },
            ),
            "available_at": episode.model_dump(mode="json")["label_available_at"],
        }
        if positive not in clean:
            raise SnapshotError("campaign_paired_truth_primary_window_binding")
        clean.remove(positive)
    if (
        canonical_sha256(raw_complete) != verified.complete_windows_sha256
        or canonical_sha256([e.model_dump(mode="json") for e in episodes])
        != verified.episodes_sha256
        or canonical_sha256(clean) != verified.clean_windows_sha256
        or len(windows) != verified.complete_window_count
        or len(clean) != verified.clean_window_count
        or len(episodes) != verified.episode_count
        or sum((w.window.end - w.window.start).days + 1 for w in windows)
        != verified.complete_observation_count
        or any(
            not verified.window.start <= w.window.start <= w.window.end <= verified.window.end
            for w in windows
        )
        or canonical_sha256(comparison) != verified.comparison_sha256
        or comparison.get("policy") != POLICY
        or any(
            (boundary := comparison["unknown_from_by_product"].get(w["product_id"])) is not None
            and date.fromisoformat(w["window"]["end"]) >= date.fromisoformat(boundary)
            for w in clean
        )
    ):
        raise SnapshotError("campaign_paired_truth_native_labels_binding")
    return result


def _verify_bundle(bundle: Path, receipt: CampaignPairedTruthReceipt) -> PairedTruth:
    files, size = _bundle_inventory(bundle, receipt.plan.max_truth_bytes + 4 * 1024**2)
    if files != receipt.artifact_files or size != receipt.artifact_bytes:
        raise SnapshotError("campaign_paired_truth_bundle_inventory")
    plan = CampaignPairedTruthPlan.model_validate_json(read_bytes(bundle, "plan.json"))
    verified = PairedSourceVerification.model_validate_json(read_bytes(bundle, "verification.json"))
    comparison = decode_json(read_bytes(bundle, "comparison.json", 2 * 1024**2))
    truth = PairedTruth.model_validate_json(read_bytes(bundle, "truth.json", plan.max_truth_bytes))
    expected = _truth(
        verified,
        comparison,
        [w.model_dump(mode="json") for w in truth.complete_windows],
        [e.model_dump(mode="json") for e in truth.episodes],
        receipt.ordinary_generation_receipt_sha256,
        receipt.generated_parent_receipt_sha256,
    )
    if (
        plan != receipt.plan
        or verified.window != plan.window
        or verified.scenario_plan_sha256 != plan.scenario_plan_sha256
        or truth != expected
        or truth.source_dataset_id != receipt.source_dataset_id
        or truth.ordinary_source_dataset_id != receipt.ordinary_source_dataset_id
        or truth.source_verification_sha256 != receipt.source_verification_sha256
        or canonical_sha256(truth.model_dump(mode="json")) != receipt.truth_sha256
    ):
        raise SnapshotError("campaign_paired_truth_bundle_binding")
    return truth


def read_campaign_paired_truth(
    source_root: Path,
    ordinary_dataset: Path,
    source_dataset: Path,
    producer_python: Path,
    output_root: Path,
    *,
    journal: Path,
    operation_id: str,
    plan: CampaignPairedTruthPlan,
    ordinary: CampaignGeneratedParentReceipt,
    generated: CampaignGeneratedParentReceipt,
    ordinary_plan: CampaignGenerationPlan,
    generation_plan: CampaignGenerationPlan,
    selection_bundles: dict[str, Path] | None = None,
) -> tuple[Path, CampaignPairedTruthReceipt]:
    ledger = campaign_journal.inspect(journal)
    _operation(ledger, operation_id)
    started = perf_counter()
    # This single frozen operation explicitly contains two full native Source
    # reads. One actual total cost includes both, with no hidden or free read.
    with campaign_journal.audited_operation(journal, operation_id) as handle:
        plan = CampaignPairedTruthPlan.model_validate_json(plan.model_dump_json())
        ordinary = CampaignGeneratedParentReceipt.model_validate_json(ordinary.model_dump_json())
        generated = CampaignGeneratedParentReceipt.model_validate_json(generated.model_dump_json())
        ordinary_plan = CampaignGenerationPlan.model_validate_json(ordinary_plan.model_dump_json())
        generation_plan = CampaignGenerationPlan.model_validate_json(
            generation_plan.model_dump_json()
        )
        source = _binding(
            ledger, operation_id, plan, ordinary, generated, ordinary_plan, generation_plan
        )
        validate_completed_generation(journal, ordinary)
        validate_completed_generation(journal, generated)
        selection_sha256 = None
        if plan.phase == "final":
            selection_sha256, _ = verify_completed_campaign_selection(
                journal, selection_bundles or {}
            )
        _producer_pin(source_root, source.producer_commit)
        for directory in (ordinary_dataset, source_dataset, output_root):
            checked_directory(directory)
        root = output_root / str(handle.reservation.reservation_id)
        root.mkdir(mode=0o700)
        (root / "tmp").mkdir(mode=0o700)
        write(
            root / "request.json",
            {
                "producer_commit": source.producer_commit,
                "producer_lock_sha256": source.producer_lock_sha256,
                "exporter_lock_sha256": generation_plan.exporter_lock_sha256,
                "ordinary_source_dataset_id": ordinary.source.parent.source_dataset_id,
                "planned_source_dataset_id": generated.source.parent.source_dataset_id,
                "resolved_parameters": generated.source.source_parameters,
                "scenario_plan": generation_plan.scenario_plan,
                "window": plan.window.model_dump(mode="json"),
                "max_source_bytes": plan.max_source_bytes,
                "max_source_rows": plan.max_source_rows,
            },
        )
        worker = Path(__file__).with_name("campaign_paired_truth_worker.py")
        measured = monitor(
            [
                str(producer_python.absolute()),
                "-I",
                "-B",
                str(worker),
                str(source_root.absolute()),
                str(ordinary_dataset.absolute()),
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
            raise SnapshotError("campaign_paired_truth_native_verification_failed")
        resources = read(root / "truth-worker-resources.json")
        if (
            type(resources.get("worker_peak_rss_bytes")) is not int
            or resources["worker_peak_rss_bytes"] <= 0
        ):
            raise SnapshotError("campaign_paired_truth_worker_memory_missing")
        peak = max(peak or 0, resources["worker_peak_rss_bytes"])
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
        )
        if peak > plan.resources.tree_rss_bytes:
            raise SnapshotError("campaign_paired_truth_worker_memory_budget")
        verified = PairedSourceVerification.model_validate_json(
            read_bytes(root, "paired-source-verification.json")
        )
        if (
            verified.ordinary.source_dataset_id != ordinary.source.parent.source_dataset_id
            or verified.planned.source_dataset_id != generated.source.parent.source_dataset_id
            or verified.resolved_parameters != generated.source.source_parameters
            or verified.producer_commit != source.producer_commit
            or verified.producer_lock_sha256 != source.producer_lock_sha256
            or verified.exporter_lock_sha256 != generation_plan.exporter_lock_sha256
            or verified.window != plan.window
            or verified.scenario_plan_sha256 != plan.scenario_plan_sha256
        ):
            raise SnapshotError("campaign_paired_truth_verified_parents_binding")
        comparison = decode_json(read_bytes(root, "paired-source-comparison.json", 2 * 1024**2))
        labels = decode_json(read_bytes(root, "paired-source-labels.json", plan.max_truth_bytes))
        truth = _truth(
            verified,
            comparison,
            labels["complete_windows"],
            labels["episodes"],
            ordinary.content_sha256(),
            generated.content_sha256(),
        )
        if len(canonical_bytes(truth.model_dump(mode="json"))) + 1 > plan.max_truth_bytes:
            raise SnapshotError("campaign_paired_truth_artifact_budget")
        bundle = root / "bundle"
        bundle.mkdir(mode=0o700)
        for name, document in (
            ("truth.json", truth.model_dump(mode="json")),
            ("verification.json", verified.model_dump(mode="json")),
            ("comparison.json", comparison),
            ("plan.json", plan.model_dump(mode="json")),
        ):
            write(bundle / name, document)
        files, size = _bundle_inventory(bundle, plan.max_truth_bytes + 4 * 1024**2)
        receipt = CampaignPairedTruthReceipt(
            protocol_sha256=ledger.protocol_sha256,
            operation_id=operation_id,
            reservation_id=str(handle.reservation.reservation_id),
            runtime_code_sha256=ledger.protocol.runtime.code_sha256,
            plan=plan,
            generated_parent_receipt_sha256=generated.content_sha256(),
            ordinary_generation_receipt_sha256=ordinary.content_sha256(),
            selection_sha256=selection_sha256,
            source_dataset_id=verified.planned.source_dataset_id,
            ordinary_source_dataset_id=verified.ordinary.source_dataset_id,
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
            raise SnapshotError("campaign_paired_truth_completion_resource_budget")
        _store_receipt(journal, receipt)
        handle.evidence_sha256 = receipt.content_sha256()
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=peak,
            artifact_bytes=size,
        )
    validate_completed_paired_truth(journal, receipt)
    return bundle, receipt


def validate_completed_paired_truth(journal: Path, receipt: CampaignPairedTruthReceipt) -> None:
    receipt = CampaignPairedTruthReceipt.model_validate_json(receipt.model_dump_json())
    ledger = campaign_journal.inspect(journal)
    operation = _operation(ledger, receipt.operation_id)
    completed = next(
        (
            e
            for e in ledger.events
            if e.kind == "finished" and e.reservation_id == receipt.reservation_id
        ),
        None,
    )
    reserved = next(
        (
            e
            for e in ledger.events
            if e.kind == "reserved" and e.reservation_id == receipt.reservation_id
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
        or reserved is None
        or completed is None
        or completed.operation_id != receipt.operation_id
        or completed.result != "completed"
        or completed.evidence_sha256 != receipt.content_sha256()
        or completed.cost is None
        or completed.cost.artifact_bytes != receipt.artifact_bytes
    ):
        raise SnapshotError("campaign_paired_truth_receipt_not_completed")
    for identifier, digest, parent_sha in (
        (
            receipt.plan.ordinary_generation_operation_id,
            receipt.plan.ordinary_source_recipe_sha256,
            receipt.ordinary_generation_receipt_sha256,
        ),
        (
            receipt.plan.generation_operation_id,
            receipt.plan.source_recipe_sha256,
            receipt.generated_parent_receipt_sha256,
        ),
    ):
        _parent_before(ledger, identifier, digest, parent_sha, reserved.sequence)
    with regular_file(journal, "receipts/" + receipt.reservation_id + ".json") as stream:
        if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
            raise SnapshotError("campaign_paired_truth_private_receipt_required")
        raw = stream.read(MAX_RECEIPT_BYTES + 1)
    if raw != canonical_bytes(receipt.model_dump(mode="json")) + b"\n":
        raise SnapshotError("campaign_paired_truth_stored_receipt_mismatch")


def verify_campaign_paired_truth(
    bundle: Path,
    *,
    journal: Path,
    receipt: CampaignPairedTruthReceipt,
    selection_bundles: dict[str, Path] | None = None,
) -> PairedTruth:
    validate_completed_paired_truth(journal, receipt)
    if receipt.plan.phase == "final":
        selection_sha256, _ = verify_completed_campaign_selection(journal, selection_bundles or {})
        if receipt.selection_sha256 != selection_sha256:
            raise SnapshotError("campaign_paired_truth_selection_mismatch")
    return _verify_bundle(bundle, receipt)
