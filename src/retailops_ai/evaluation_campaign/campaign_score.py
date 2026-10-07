"""Audited full-role inference: reserve before reads, verify before durable completion."""

import hashlib
import os
import stat
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import JsonValue, TypeAdapter

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignCost,
    CampaignJournal,
    CampaignOperationPlan,
)
from retailops_ai.evaluation_campaign.campaign_export import (
    MAX_RECEIPT_BYTES,
    _store_receipt,
    validate_completed_export,
)
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportReceipt,
)
from retailops_ai.evaluation_campaign.campaign_fit import (
    _bundle_inventory,
    verify_campaign_forecast_bundle,
)
from retailops_ai.evaluation_campaign.campaign_fit_contract import (
    CampaignForecastFitReceipt,
    Family,
)
from retailops_ai.evaluation_campaign.campaign_generation import _environment
from retailops_ai.evaluation_campaign.campaign_generation_monitor import monitor, scratch_bytes
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_score_contract import (
    FAMILIES,
    CampaignForecastRawPrediction,
    CampaignForecastScorePlan,
    CampaignForecastScoreReceipt,
)
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    read_bytes,
    regular_file,
)
from retailops_ai.source_snapshot.publish import fsync_tree


def _operation(ledger: CampaignJournal, operation_id: str) -> CampaignOperationPlan:
    operation = next(
        (o for o in ledger.protocol.operations if o.operation_id == operation_id), None
    )
    if operation is None or (
        operation.phase != "development"
        or operation.action != "model_score"
        or operation.use_case != "forecast"
        or operation.role not in ("tune", "calibration")
    ):
        raise SnapshotError("campaign_score_requires_development_tune_or_calibration")
    return operation


def _binding(
    ledger: CampaignJournal,
    operation: CampaignOperationPlan,
    plan: CampaignForecastScorePlan,
    exported: CampaignDevelopmentExportReceipt,
    fits: dict[Family, CampaignForecastFitReceipt],
    bundles: dict[Family, Path],
) -> None:
    if (
        set(fits) != set(FAMILIES)
        or set(bundles) != set(FAMILIES)
        or operation.execution_recipe_sha256 != plan.content_sha256()
        or operation.role != plan.role
        or operation.source_recipe_sha256 != plan.source_recipe_sha256
        or exported.plan.source_recipe_sha256 != plan.source_recipe_sha256
        or exported.operation_id != plan.export_operation_id
        or exported.protocol_sha256 != ledger.protocol_sha256
        or exported.runtime_code_sha256 != ledger.protocol.runtime.code_sha256
        or not {plan.export_operation_id, *plan.fit_operation_ids.values()}
        <= set(operation.prerequisites)
    ):
        raise SnapshotError("campaign_score_frozen_plan_or_parent_binding_mismatch")
    for family in FAMILIES:
        fit = fits[family]
        if (
            fit.operation_id != plan.fit_operation_ids[family]
            or fit.plan.family != family
            or fit.plan.source_recipe_sha256 != plan.source_recipe_sha256
            or fit.plan.export_operation_id != plan.export_operation_id
            or fit.plan.worker_environment_lock_sha256 != plan.worker_environment_lock_sha256
            or fit.protocol_sha256 != ledger.protocol_sha256
            or fit.runtime_code_sha256 != ledger.protocol.runtime.code_sha256
            or fit.export_receipt_sha256 != exported.content_sha256()
            or fit.dataset_id != exported.dataset_id
        ):
            raise SnapshotError("campaign_score_completed_fit_binding_mismatch")
    if (
        len(
            {
                (
                    f.train_keys_sha256,
                    f.early_stopping_keys_sha256,
                    f.train_eligible_rows,
                    f.early_stopping_eligible_rows,
                )
                for f in fits.values()
            }
        )
        != 1
    ):
        raise SnapshotError("campaign_score_fit_training_populations_differ")


def score_campaign_forecast(
    dataset: Path,
    bundles: dict[Family, Path],
    worker_python: Path,
    output_root: Path,
    *,
    journal: Path,
    operation_id: str,
    plan: CampaignForecastScorePlan,
    exported: CampaignDevelopmentExportReceipt,
    fits: dict[Family, CampaignForecastFitReceipt],
) -> tuple[Path, CampaignForecastScoreReceipt]:
    ledger = campaign_journal.inspect(journal)
    operation = _operation(ledger, operation_id)
    started = perf_counter()
    with campaign_journal.audited_operation(journal, operation_id) as handle:
        plan = CampaignForecastScorePlan.model_validate_json(
            canonical_bytes(plan.model_dump(mode="json"))
        )
        exported = CampaignDevelopmentExportReceipt.model_validate_json(
            canonical_bytes(exported.model_dump(mode="json"))
        )
        fits = {
            f: CampaignForecastFitReceipt.model_validate_json(
                canonical_bytes(v.model_dump(mode="json"))
            )
            for f, v in fits.items()
        }
        _binding(ledger, operation, plan, exported, fits, bundles)
        validate_completed_export(journal, exported)
        for family in FAMILIES:
            verify_campaign_forecast_bundle(bundles[family], journal=journal, receipt=fits[family])
        checked_directory(output_root)
        if stat.S_IMODE(output_root.stat().st_mode) != 0o700:
            raise SnapshotError("campaign_score_private_output_directory_required")
        root = output_root / str(handle.reservation.reservation_id)
        root.mkdir(mode=0o700)
        (root / "tmp").mkdir(mode=0o700)
        write(
            root / "request.json",
            {
                "dataset": str(dataset.absolute()),
                "bundles": {f: str(bundles[f].absolute()) for f in FAMILIES},
                "fits": {f: fits[f].model_dump(mode="json") for f in FAMILIES},
                "plan": plan.model_dump(mode="json"),
                "exported": exported.model_dump(mode="json"),
            },
        )
        worker = Path(__file__).with_name("campaign_score_entry.py")
        phases: list[dict[str, Any]] = []
        peak = None
        for phase in ("prepare", "predict"):
            interpreter = sys.executable if phase == "prepare" else str(worker_python)
            measured = monitor(
                [interpreter, "-I", "-B", str(worker), phase, str(root)],
                root=root,
                log=root / (phase + ".log"),
                env=_environment(root),
                scratch=(root,),
                resources=plan.resources,
                deadline=started + plan.resources.wall_seconds,
            )
            if measured["sampled_tree_peak_rss_bytes"] is not None:
                peak = max(peak or 0, int(measured["sampled_tree_peak_rss_bytes"]))
            handle.cost = CampaignCost(
                wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
            )
            write(root / (phase + "-resources.json"), measured)
            if measured["status"] != "passed":
                raise SnapshotError("campaign_score_phase_failed_" + phase)
            result = read(root / (phase + ".json"))
            if result["worker_peak_rss_bytes"] > plan.resources.tree_rss_bytes:
                raise SnapshotError("campaign_score_worker_peak_rss_limit")
            phases.append({"phase": phase, "monitor": measured, "worker": result})
        prepared, predicted = (p["worker"] for p in phases)
        population = (
            "rows",
            "eligible_rows",
            "keys_sha256",
            "eligible_keys_sha256",
            "role_population_sha256",
        )
        if (
            any(prepared[k] != predicted[k] for k in population)
            or predicted["all_models_share_all_role_keys"] is not True
        ):
            raise SnapshotError("campaign_score_prediction_population_mismatch")
        files, size = _bundle_inventory(root / "bundle", plan.max_output_bytes)
        receipt = CampaignForecastScoreReceipt(
            protocol_sha256=ledger.protocol_sha256,
            operation_id=operation_id,
            reservation_id=str(handle.reservation.reservation_id),
            plan=plan,
            export_receipt_sha256=exported.content_sha256(),
            fit_receipt_sha256={f: fits[f].content_sha256() for f in FAMILIES},
            model_artifact_sha256={f: fits[f].model_artifact_sha256 for f in FAMILIES},
            dataset_id=exported.dataset_id,
            runtime_code_sha256=ledger.protocol.runtime.code_sha256,
            rows=predicted["rows"],
            eligible_rows=predicted["eligible_rows"],
            keys_sha256=predicted["keys_sha256"],
            eligible_keys_sha256=predicted["eligible_keys_sha256"],
            role_population_sha256=predicted["role_population_sha256"],
            artifact_sha256=canonical_sha256(files),
            artifact_bytes=size,
            artifact_files=files,
            worker_evidence=TypeAdapter(dict[str, JsonValue]).validate_python({"phases": phases}),
        )
        _verify_bundle(root / "bundle", receipt)
        fsync_tree(root)
        if (
            perf_counter() - started > plan.resources.wall_seconds
            or scratch_bytes((root,)) > plan.resources.scratch_bytes
        ):
            raise SnapshotError("campaign_score_completion_resource_limit")
        _store_receipt(journal, receipt)
        handle.evidence_sha256 = receipt.content_sha256()
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=peak,
            artifact_bytes=size,
        )
    validate_completed_score(journal, receipt)
    return root / "bundle", receipt


def _verify_bundle(bundle: Path, receipt: CampaignForecastScoreReceipt) -> None:
    files, size = _bundle_inventory(bundle, receipt.plan.max_output_bytes)
    if files != receipt.artifact_files or size != receipt.artifact_bytes:
        raise SnapshotError("campaign_score_artifact_checksum_mismatch")
    if (
        CampaignForecastScorePlan.model_validate_json(read_bytes(bundle, "plan.json"))
        != receipt.plan
    ):
        raise SnapshotError("campaign_score_artifact_plan_mismatch")
    if read(bundle / "parents.json") != {
        "export_receipt_sha256": receipt.export_receipt_sha256,
        "fit_receipt_sha256": receipt.fit_receipt_sha256,
        "model_artifact_sha256": receipt.model_artifact_sha256,
    }:
        raise SnapshotError("campaign_score_artifact_parent_mismatch")
    metrics = read(bundle / "metrics.json")
    if metrics.get("scope") != "raw_development_diagnostic_not_qualification" or any(
        metrics.get(k) is not False
        for k in ("calibration_fitted", "quality_qualified", "final_test_accessed", "stage_ready")
    ):
        raise SnapshotError("campaign_score_raw_metrics_scope_mismatch")
    segments = metrics.get("segments")
    if (
        not isinstance(segments, list)
        or len(segments) != 15
        or [s.get("horizon") for s in segments if isinstance(s, dict)]
        != ["all", *map(str, range(1, 15))]
        or segments[0].get("rows") != receipt.rows
        or segments[0].get("eligible_rows") != receipt.eligible_rows
        or any(
            type(s.get("rows")) is not int
            or type(s.get("eligible_rows")) is not int
            or not 0 <= s["eligible_rows"] <= s["rows"]
            for s in segments
        )
        or sum(s["rows"] for s in segments[1:]) != receipt.rows
        or sum(s["eligible_rows"] for s in segments[1:]) != receipt.eligible_rows
    ):
        raise SnapshotError("campaign_score_metrics_complete_population_mismatch")
    keys, eligible_keys = hashlib.sha256(), hashlib.sha256()
    previous = None
    rows = eligible = 0
    with regular_file(bundle, "predictions.jsonl") as stream:
        while line := stream.readline(65537):
            if len(line) > 65536 or not line.endswith(b"\n"):
                raise SnapshotError("campaign_score_prediction_record_limit")
            row = CampaignForecastRawPrediction.model_validate_json(line)
            key = membership_key(row)
            if (
                row.role != receipt.plan.role
                or (previous is not None and key <= previous)
                or line != canonical_bytes(row.model_dump(mode="json")) + b"\n"
            ):
                raise SnapshotError("campaign_score_prediction_key_or_role_mismatch")
            previous = key
            rows += 1
            keys.update(key + b"\n")
            if row.eligible:
                eligible += 1
                eligible_keys.update(key + b"\n")
            if rows > receipt.plan.max_rows:
                raise SnapshotError("campaign_score_prediction_population_budget")
    if (rows, eligible, keys.hexdigest(), eligible_keys.hexdigest()) != (
        receipt.rows,
        receipt.eligible_rows,
        receipt.keys_sha256,
        receipt.eligible_keys_sha256,
    ):
        raise SnapshotError("campaign_score_prediction_complete_population_mismatch")


def validate_completed_score(journal: Path, receipt: CampaignForecastScoreReceipt) -> None:
    receipt = CampaignForecastScoreReceipt.model_validate_json(
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
    if (
        receipt.protocol_sha256 != ledger.protocol_sha256
        or receipt.runtime_code_sha256 != ledger.protocol.runtime.code_sha256
        or operation.execution_recipe_sha256 != receipt.plan.content_sha256()
        or operation.source_recipe_sha256 != receipt.plan.source_recipe_sha256
        or operation.role != receipt.plan.role
        or not {receipt.plan.export_operation_id, *receipt.plan.fit_operation_ids.values()}
        <= set(operation.prerequisites)
        or completion is None
        or completion.operation_id != receipt.operation_id
        or completion.result != "completed"
        or completion.evidence_sha256 != receipt.content_sha256()
        or completion.cost is None
        or completion.cost.artifact_bytes != receipt.artifact_bytes
    ):
        raise SnapshotError("campaign_score_receipt_not_completed")
    try:
        with regular_file(journal, "receipts/" + receipt.reservation_id + ".json") as stream:
            if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
                raise SnapshotError("campaign_score_private_receipt_required")
            raw = stream.read(MAX_RECEIPT_BYTES + 1)
    except OSError:
        raise SnapshotError("campaign_score_receipt_unavailable") from None
    if raw != canonical_bytes(receipt.model_dump(mode="json")) + b"\n":
        raise SnapshotError("campaign_score_stored_receipt_mismatch")


def verify_campaign_forecast_scores(
    bundle: Path, *, journal: Path, receipt: CampaignForecastScoreReceipt
) -> None:
    """Validate durable prediction evidence without reopening outcomes or granting a score."""
    validate_completed_score(journal, receipt)
    _verify_bundle(bundle, receipt)
