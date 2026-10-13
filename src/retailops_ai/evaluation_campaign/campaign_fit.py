"""Reserve a frozen fit before reads; complete only after measured fitting and cold reload."""

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
from retailops_ai.evaluation_campaign.campaign_fit_artifact import (
    _bundle_inventory as _bundle_inventory,
)
from retailops_ai.evaluation_campaign.campaign_fit_artifact import (
    _verify_bundle_content as _verify_bundle_content,
)
from retailops_ai.evaluation_campaign.campaign_fit_contract import (
    CampaignForecastEncoding,
    CampaignForecastFitPlan,
    CampaignForecastFitReceipt,
)
from retailops_ai.evaluation_campaign.campaign_generation import _environment
from retailops_ai.evaluation_campaign.campaign_generation_monitor import monitor, scratch_bytes
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    regular_file,
)
from retailops_ai.source_snapshot.publish import fsync_tree


def _operation(ledger: CampaignJournal, operation_id: str) -> CampaignOperationPlan:
    operation = next(
        (o for o in ledger.protocol.operations if o.operation_id == operation_id), None
    )
    if operation is None or (
        operation.phase != "development"
        or operation.action != "model_fit"
        or operation.use_case != "forecast"
        or operation.role != "train"
    ):
        raise SnapshotError("campaign_fit_requires_development_forecast_fit")
    return operation


def _binding(
    ledger: CampaignJournal,
    operation: CampaignOperationPlan,
    plan: CampaignForecastFitPlan,
    exported: CampaignDevelopmentExportReceipt,
) -> None:
    if (
        operation.execution_recipe_sha256 != plan.content_sha256()
        or operation.forecast_family != plan.family
        or operation.initialization_seed != plan.initialization_seed
        or operation.source_recipe_sha256 != plan.source_recipe_sha256
        or exported.plan.source_recipe_sha256 != plan.source_recipe_sha256
        or exported.protocol_sha256 != ledger.protocol_sha256
        or exported.runtime_code_sha256 != ledger.protocol.runtime.code_sha256
        or exported.operation_id != plan.export_operation_id
        or plan.export_operation_id not in operation.prerequisites
    ):
        raise SnapshotError("campaign_fit_frozen_plan_or_export_binding_mismatch")


def fit_campaign_forecast(
    dataset: Path,
    worker_python: Path,
    output_root: Path,
    *,
    journal: Path,
    operation_id: str,
    plan: CampaignForecastFitPlan,
    exported: CampaignDevelopmentExportReceipt,
) -> tuple[Path, CampaignForecastFitReceipt]:
    ledger = campaign_journal.inspect(journal)
    operation = _operation(ledger, operation_id)
    started = perf_counter()
    with campaign_journal.audited_operation(journal, operation_id) as handle:
        plan = CampaignForecastFitPlan.model_validate_json(
            canonical_bytes(plan.model_dump(mode="json"))
        )
        exported = CampaignDevelopmentExportReceipt.model_validate_json(
            canonical_bytes(exported.model_dump(mode="json"))
        )
        _binding(ledger, operation, plan, exported)
        validate_completed_export(journal, exported)
        checked_directory(output_root)
        root = output_root / str(handle.reservation.reservation_id)
        root.mkdir(mode=0o700)
        (root / "tmp").mkdir(mode=0o700)
        write(
            root / "request.json",
            {
                "dataset": str(dataset.absolute()),
                "plan": plan.model_dump(mode="json"),
                "exported": exported.model_dump(mode="json"),
            },
        )
        worker = Path(__file__).with_name("campaign_fit_worker.py")
        peak = None
        phases: list[dict[str, Any]] = []
        for phase in ("prepare", "fit", "reload"):
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
            write(root / (phase + "-resources.json"), measured)
            handle.cost = CampaignCost(
                wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
            )
            if measured["status"] != "passed":
                raise SnapshotError("campaign_fit_phase_failed_" + phase)
            result = read(root / (phase + ".json"))
            if result["worker_peak_rss_bytes"] > plan.resources.tree_rss_bytes:
                raise SnapshotError("campaign_fit_worker_peak_rss_limit")
            phases.append({"phase": phase, "monitor": measured, "worker": result})
        prepared, fitted, reloaded = (p["worker"] for p in phases)
        train, validation = prepared["matrices"]["train"], prepared["matrices"]["early_stopping"]
        train_rows = train["eligible_rows"] if plan.family == "tensorflow" else train["rows"]
        validation_rows = (
            validation["eligible_rows"] if plan.family == "tensorflow" else validation["rows"]
        )
        if (
            reloaded["reload_verified_eligible_rows"] != validation_rows
            or reloaded["reload_all_early_stopping_keys_verified"] is not True
        ):
            raise SnapshotError("campaign_fit_reload_population_mismatch")
        encoding = CampaignForecastEncoding.model_validate_json(
            canonical_bytes(read(root / "bundle" / "encoding.json"))
        )
        if (
            encoding.content_sha256() != prepared["encoding_sha256"]
            or encoding.train_keys_sha256 != train["keys_sha256"]
            or encoding.train_rows != train_rows
            or CampaignForecastFitPlan.model_validate_json(
                canonical_bytes(read(root / "bundle" / "plan.json"))
            )
            != plan
        ):
            raise SnapshotError("campaign_fit_encoding_population_mismatch")
        files, size = _bundle_inventory(root / "bundle", plan.max_artifact_bytes)
        receipt = CampaignForecastFitReceipt(
            protocol_sha256=ledger.protocol_sha256,
            operation_id=operation_id,
            reservation_id=str(handle.reservation.reservation_id),
            plan=plan,
            export_receipt_sha256=exported.content_sha256(),
            dataset_id=exported.dataset_id,
            runtime_code_sha256=ledger.protocol.runtime.code_sha256,
            train_keys_sha256=train["keys_sha256"],
            early_stopping_keys_sha256=validation["keys_sha256"],
            train_eligible_rows=train_rows,
            early_stopping_eligible_rows=validation_rows,
            encoding_sha256=encoding.content_sha256(),
            model_artifact_sha256=canonical_sha256(files),
            model_artifact_bytes=size,
            artifact_files=files,
            worker_evidence=TypeAdapter(dict[str, JsonValue]).validate_python(
                {"phases": phases, "fit": fitted, "reload": reloaded}
            ),
        )
        _verify_bundle_content(root / "bundle", receipt)
        fsync_tree(root)
        total = scratch_bytes((root,))
        if (
            perf_counter() - started > plan.resources.wall_seconds
            or total > plan.resources.scratch_bytes
        ):
            raise SnapshotError("campaign_fit_completion_resource_limit")
        _store_receipt(journal, receipt)
        handle.evidence_sha256 = receipt.content_sha256()
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=peak,
            artifact_bytes=size,
        )
    validate_completed_fit(journal, receipt)
    return root / "bundle", receipt


def verify_campaign_forecast_bundle(
    bundle: Path, *, journal: Path, receipt: CampaignForecastFitReceipt
) -> None:
    """Check the entire immutable artifact against its completed fit, without labels or refits."""
    validate_completed_fit(journal, receipt)
    _verify_bundle_content(bundle, receipt)


def validate_completed_fit(journal: Path, receipt: CampaignForecastFitReceipt) -> None:
    """Validate local completion evidence; no grant for another fit or model score."""
    receipt = CampaignForecastFitReceipt.model_validate_json(
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
        ledger.protocol_sha256 != receipt.protocol_sha256
        or operation.execution_recipe_sha256 != receipt.plan.content_sha256()
        or operation.forecast_family != receipt.plan.family
        or operation.initialization_seed != receipt.plan.initialization_seed
        or operation.source_recipe_sha256 != receipt.plan.source_recipe_sha256
        or receipt.plan.export_operation_id not in operation.prerequisites
        or receipt.runtime_code_sha256 != ledger.protocol.runtime.code_sha256
        or completion is None
        or completion.operation_id != receipt.operation_id
        or completion.result != "completed"
        or completion.evidence_sha256 != receipt.content_sha256()
        or completion.cost is None
        or completion.cost.artifact_bytes != receipt.model_artifact_bytes
    ):
        raise SnapshotError("campaign_fit_receipt_not_completed")
    try:
        with regular_file(journal, "receipts/" + receipt.reservation_id + ".json") as stream:
            if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
                raise SnapshotError("campaign_fit_private_receipt_required")
            raw = stream.read(MAX_RECEIPT_BYTES + 1)
    except OSError:
        raise SnapshotError("campaign_fit_receipt_unavailable") from None
    if raw != canonical_bytes(receipt.model_dump(mode="json")) + b"\n":
        raise SnapshotError("campaign_fit_stored_receipt_mismatch")
