"""Reserve full native anomaly preparation/fit before reads and accept after cold replay."""

import os
import stat
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import JsonValue, TypeAdapter

from retailops_ai.anomaly_portfolio.model import CensusCountRateDescriptor, load
from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_anomaly_fit_contract import (
    CampaignAnomalyFitPlan,
    CampaignAnomalyFitReceipt,
)
from retailops_ai.evaluation_campaign.campaign_anomaly_fit_data import FAMILIES, MAX_MANIFEST_BYTES
from retailops_ai.evaluation_campaign.campaign_anomaly_membership import (
    CampaignAnomalyMembershipPlan,
)
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignCost,
    CampaignJournal,
    CampaignOperationPlan,
    CampaignSourceRecipe,
)
from retailops_ai.evaluation_campaign.campaign_export import (
    MAX_RECEIPT_BYTES,
    _store_receipt,
    validate_completed_export,
)
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportReceipt,
)
from retailops_ai.evaluation_campaign.campaign_fit import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_generation import _environment
from retailops_ai.evaluation_campaign.campaign_generation_monitor import monitor, scratch_bytes
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.contract import PreparationRuntime
from retailops_ai.evaluation_campaign.physical_contract import PhysicalSourceSpec
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
    if operation is None or (
        operation.phase,
        operation.action,
        operation.use_case,
        operation.role,
    ) != ("development", "model_fit", "anomaly", "train"):
        raise SnapshotError("campaign_anomaly_fit_requires_development_anomaly_train")
    return operation


def _binding(
    ledger: CampaignJournal,
    operation: CampaignOperationPlan,
    plan: CampaignAnomalyFitPlan,
    exported: CampaignDevelopmentExportReceipt,
) -> CampaignSourceRecipe:
    if (
        operation.execution_recipe_sha256 != plan.content_sha256()
        or operation.initialization_seed != plan.policy.model_seed
        or operation.source_recipe_sha256 != plan.source_recipe_sha256
        or exported.plan.source_recipe_sha256 != plan.source_recipe_sha256
        or exported.protocol_sha256 != ledger.protocol_sha256
        or exported.runtime_code_sha256 != ledger.protocol.runtime.code_sha256
        or exported.operation_id != plan.export_operation_id
        or plan.export_operation_id not in operation.prerequisites
    ):
        raise SnapshotError("campaign_anomaly_fit_frozen_plan_or_parent_binding")
    source = next(
        s for s in ledger.protocol.sources if s.content_sha256() == plan.source_recipe_sha256
    )
    if (
        source.phase != "development"
        or source.generation_config_sha256
        != canonical_sha256(exported.recipe.source.source_parameters)
        or not source.history.start <= plan.train.start <= plan.test.end <= source.history.end
    ):
        raise SnapshotError("campaign_anomaly_fit_source_recipe_or_window")
    return source


def _verify_bundle_content(bundle: Path, receipt: CampaignAnomalyFitReceipt) -> None:
    files, size = _bundle_inventory(bundle, receipt.plan.max_artifact_bytes)
    if files != receipt.artifact_files or size != receipt.model_artifact_bytes:
        raise SnapshotError("campaign_anomaly_fit_bundle_inventory")
    plan = CampaignAnomalyFitPlan.model_validate_json(read_bytes(bundle, "plan.json"))
    manifest_raw = read_bytes(bundle, "feature-manifest.json", MAX_MANIFEST_BYTES)
    manifest = decode_json(manifest_raw)
    completion = decode_json(read_bytes(bundle, "parent-completion.json", MAX_MANIFEST_BYTES))
    if (
        manifest_raw != canonical_bytes(manifest) + b"\n"
        or manifest.get("version") != "ai09-complete-anomaly-feature-manifest-1.0.0"
    ):
        raise SnapshotError("campaign_anomaly_fit_feature_manifest_encoding")
    source = PhysicalSourceSpec.model_validate_json(canonical_bytes(manifest["source"]))
    runtime = PreparationRuntime.model_validate_json(canonical_bytes(manifest["runtime"]))
    membership = CampaignAnomalyMembershipPlan.model_validate_json(
        canonical_bytes(manifest["membership_plan"])
    )
    model = load(bundle / "model.json", files["model.json"])
    descriptor = model.descriptor
    if not isinstance(descriptor, CensusCountRateDescriptor) or (
        plan != receipt.plan
        or model.detector_id != receipt.model_id
        or descriptor.feature_manifest_sha256 != canonical_sha256(manifest)
        or descriptor.feature_manifest_sha256 != receipt.feature_manifest_sha256
        or descriptor.feature_rows_sha256 != manifest["native_points_sha256"]
        or descriptor.feature_rows_sha256 != membership.native_points_sha256
        or membership.feature_plan_sha256 != canonical_sha256(manifest["feature_plan"])
        or descriptor.source_dataset_id != source.parent.source_dataset_id
        or descriptor.policy != plan.policy
        or descriptor.event_capacities != plan.event_capacities
        or descriptor.code_sha256 != runtime.code_sha256
        or runtime.code_sha256 != receipt.runtime_code_sha256
        or descriptor.dependency_lock_sha256 != runtime.dependency_lock_sha256
        or any(
            getattr(descriptor, k) != getattr(plan, k) or getattr(membership, k) != getattr(plan, k)
            for k in ("train", "validation", "training_cutoff", "selection_cutoff")
        )
        or membership.test != plan.test
    ):
        raise SnapshotError("campaign_anomaly_fit_model_feature_binding")
    if (
        completion["parent"]["source"] != manifest["source"]
        or completion["parent"]["runtime"] != manifest["runtime"]
        or completion["parent"]["plan_sha256"] != canonical_sha256(manifest["parent_plan"])
        or completion["parent"]["physical_source_and_curated_replay_passed"] is not True
        or completion["days"]["plan_sha256"] != canonical_sha256(manifest["day_plan"])
        or completion["days"]["complete_native_day_projection_passed"] is not True
        or completion["days"]["resolved_day_count"] != manifest["resolved_days"]
        or completion["gate"]["plan_sha256"] != canonical_sha256(manifest["gate_plan"])
        or completion["gate"]["native_causal_day_queries_verified"] is not True
        or completion["gate"]["plan"] != manifest["gate_plan"]
        or manifest["gate_plan"]["native_days_sha256"] != completion["days"]["native_days_sha256"]
        or manifest["feature_plan"]["day_gate_plan_sha256"] != completion["gate"]["plan_sha256"]
        or manifest["feature_plan"]["expected_points"] != manifest["resolved_days"]
        or completion["features"]["plan_sha256"] != canonical_sha256(manifest["feature_plan"])
        or completion["features"]["native_points_sha256"] != descriptor.feature_rows_sha256
        or completion["features"]["complete_native_point_census_passed"] is not True
        or completion["replay"]["capture_sha256"] != manifest["replay_plan"]["capture_sha256"]
        or completion["replay"]["missing_parent_facts"] != 0
    ):
        raise SnapshotError("campaign_anomaly_fit_parent_completion_binding")


def fit_campaign_anomaly(
    snapshot: Path,
    curated: Path,
    output_root: Path,
    *,
    journal: Path,
    operation_id: str,
    plan: CampaignAnomalyFitPlan,
    exported: CampaignDevelopmentExportReceipt,
) -> tuple[Path, CampaignAnomalyFitReceipt]:
    ledger = campaign_journal.inspect(journal)
    operation = _operation(ledger, operation_id)
    started = perf_counter()
    with campaign_journal.audited_operation(journal, operation_id) as handle:
        plan = CampaignAnomalyFitPlan.model_validate_json(plan.model_dump_json())
        exported = CampaignDevelopmentExportReceipt.model_validate_json(exported.model_dump_json())
        source = _binding(ledger, operation, plan, exported)
        validate_completed_export(journal, exported)
        checked_directory(output_root)
        root = output_root / str(handle.reservation.reservation_id)
        root.mkdir(mode=0o700)
        (root / "tmp").mkdir(mode=0o700)
        write(
            root / "request.json",
            {
                "snapshot": str(snapshot.absolute()),
                "curated": str(curated.absolute()),
                "plan": plan.model_dump(mode="json"),
                "source": exported.recipe.source.model_dump(mode="json"),
                "runtime": ledger.protocol.runtime.model_dump(mode="json"),
                "producer": {
                    "commit": source.producer_commit,
                    "lock": source.producer_lock_sha256,
                    "exporter_lock": source.exporter_lock_sha256,
                },
            },
        )
        worker = Path(__file__).with_name("campaign_anomaly_fit_worker.py")
        phases: dict[str, Any] = {}
        peak = None
        for phase in ("fit", "reload"):
            measured = monitor(
                [sys.executable, "-I", "-B", str(worker), phase, str(root)],
                root=root,
                log=root / (phase + ".log"),
                env=_environment(root),
                scratch=(root,),
                resources=plan.resources,
                deadline=started + plan.resources.wall_seconds,
            )
            if measured["sampled_tree_peak_rss_bytes"] is not None:
                peak = max(peak or 0, int(measured["sampled_tree_peak_rss_bytes"]))
            write(root / (phase + "-resources.json"), measured)
            handle.cost = CampaignCost(
                wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
            )
            if measured["status"] != "passed":
                raise SnapshotError("campaign_anomaly_fit_phase_failed_" + phase)
            result = read(root / (phase + ".json"))
            peak = max(peak or 0, result["conservative_worker_tree_peak_rss_bytes"])
            handle.cost = CampaignCost(
                wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
            )
            if peak > plan.resources.tree_rss_bytes:
                raise SnapshotError("campaign_anomaly_fit_worker_peak_limit")
            phases[phase] = {"monitor": measured, "worker": result}
        fitted, reloaded = phases["fit"]["worker"], phases["reload"]["worker"]
        if (
            fitted["all_parent_contexts_completed"] is not True
            or reloaded["all_validation_rows_replayed"] is not True
            or reloaded["model_id"] != fitted["model_id"]
            or set(fitted["validation_files"])
            != {"validation-" + family + ".jsonl" for family in FAMILIES}
            or any(
                type(item["rows"]) is not int or item["rows"] <= 0
                for item in fitted["validation_files"].values()
            )
            or reloaded["rows"]
            != {name: item["rows"] for name, item in fitted["validation_files"].items()}
        ):
            raise SnapshotError("campaign_anomaly_fit_incomplete_parent_or_reload")
        files, size = _bundle_inventory(root / "bundle", plan.max_artifact_bytes)
        receipt = CampaignAnomalyFitReceipt(
            protocol_sha256=ledger.protocol_sha256,
            operation_id=operation_id,
            reservation_id=str(handle.reservation.reservation_id),
            plan=plan,
            export_receipt_sha256=exported.content_sha256(),
            runtime_code_sha256=ledger.protocol.runtime.code_sha256,
            model_id=fitted["model_id"],
            feature_manifest_sha256=fitted["feature_manifest_sha256"],
            model_artifact_sha256=canonical_sha256(files),
            model_artifact_bytes=size,
            artifact_files=files,
            worker_evidence=TypeAdapter(dict[str, JsonValue]).validate_python(phases),
        )
        _verify_bundle_content(root / "bundle", receipt)
        fsync_tree(root)
        if (
            perf_counter() - started > plan.resources.wall_seconds
            or scratch_bytes((root,)) > plan.resources.scratch_bytes
        ):
            raise SnapshotError("campaign_anomaly_fit_completion_resource_budget")
        _store_receipt(journal, receipt)
        handle.evidence_sha256 = receipt.content_sha256()
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=peak,
            artifact_bytes=size,
        )
    validate_completed_anomaly_fit(journal, receipt)
    return root / "bundle", receipt


def validate_completed_anomaly_fit(journal: Path, receipt: CampaignAnomalyFitReceipt) -> None:
    receipt = CampaignAnomalyFitReceipt.model_validate_json(receipt.model_dump_json())
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
        or operation.initialization_seed != receipt.plan.policy.model_seed
        or receipt.plan.export_operation_id not in operation.prerequisites
        or completion is None
        or completion.operation_id != receipt.operation_id
        or completion.result != "completed"
        or completion.evidence_sha256 != receipt.content_sha256()
        or completion.cost is None
        or completion.cost.artifact_bytes != receipt.model_artifact_bytes
    ):
        raise SnapshotError("campaign_anomaly_fit_receipt_not_completed")
    with regular_file(journal, "receipts/" + receipt.reservation_id + ".json") as stream:
        if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
            raise SnapshotError("campaign_anomaly_fit_private_receipt_required")
        raw = stream.read(MAX_RECEIPT_BYTES + 1)
    if raw != canonical_bytes(receipt.model_dump(mode="json")) + b"\n":
        raise SnapshotError("campaign_anomaly_fit_stored_receipt_mismatch")


def verify_campaign_anomaly_bundle(
    bundle: Path, *, journal: Path, receipt: CampaignAnomalyFitReceipt
) -> None:
    """Bind every artifact to its actually completed fit; no outcome reads/refits."""
    validate_completed_anomaly_fit(journal, receipt)
    _verify_bundle_content(bundle, receipt)
