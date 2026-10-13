"""Reserve each original stockout fit and retain its actual outcome and costs."""

import os
import re
import stat
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import JsonValue, TypeAdapter

from retailops_ai.data_contracts.identity import canonical_bytes
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_contract import CampaignCost
from retailops_ai.evaluation_campaign.campaign_export import MAX_RECEIPT_BYTES, _store_receipt
from retailops_ai.evaluation_campaign.campaign_generation import _environment
from retailops_ai.evaluation_campaign.campaign_generation_monitor import monitor, scratch_bytes
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_role_protocol import (
    RoleAwareCampaignJournal,
    RoleAwareCampaignOperation,
)
from retailops_ai.evaluation_campaign.campaign_stockout_preparation import (
    validate_completed_stockout_preparation,
)
from retailops_ai.evaluation_campaign.campaign_stockout_preparation_contract import (
    CampaignStockoutPreparationReceipt,
)
from retailops_ai.evaluation_campaign.campaign_stockout_trial_artifact import verify_trial_artifact
from retailops_ai.evaluation_campaign.campaign_stockout_trial_contract import (
    CampaignStockoutTrialPlan,
    CampaignStockoutTrialReceipt,
)
from retailops_ai.source_snapshot.files import SnapshotError, checked_directory, regular_file
from retailops_ai.source_snapshot.publish import fsync_tree


def _operation(ledger: RoleAwareCampaignJournal, operation_id: str) -> RoleAwareCampaignOperation:
    operation = next(
        (o for o in ledger.protocol.operations if o.operation_id == operation_id), None
    )
    if operation is None or (
        operation.phase != "development"
        or operation.use_case != "stockout"
        or (operation.action, operation.role)
        not in (("model_fit", "train"), ("calibrator_fit", "early_stopping"))
    ):
        raise SnapshotError("stockout_trial_requires_reserved_development_fit")
    return operation


def _binding(
    ledger: RoleAwareCampaignJournal,
    operation: RoleAwareCampaignOperation,
    plan: CampaignStockoutTrialPlan,
    prepared: CampaignStockoutPreparationReceipt,
) -> None:
    conditional = plan.kind == "conditional"
    if (
        operation.execution_recipe_sha256 != plan.content_sha256()
        or operation.source_recipe_sha256 != plan.source_recipe_sha256
        or plan.source_recipe_sha256 != prepared.plan.source_recipe_sha256
        or plan.preparation_operation_id != prepared.operation_id
        or prepared.operation_id not in operation.prerequisites
        or prepared.protocol_sha256 != ledger.protocol_sha256
        or prepared.generated.runtime != ledger.protocol.runtime
        or prepared.plan.policy.windows != ledger.protocol.development_roles.windows
        or plan.policy != ledger.protocol.stockout_training_policy
        or (operation.stockout_family, operation.stockout_variant) != (plan.family, plan.variant)
        or operation.stockout_calibrator_C != plan.calibrator_C
        or operation.action != ("calibrator_fit" if conditional else "model_fit")
        or operation.read_roles != (("early_stopping",) if conditional else ("train",))
        or operation.resolved_prerequisites != ((plan.base_operation_id,) if conditional else ())
        or (not conditional and operation.initialization_seed != plan.initialization_seed)
    ):
        raise SnapshotError("stockout_trial_frozen_plan_or_preparation_mismatch")


def _base_binding(
    plan: CampaignStockoutTrialPlan,
    prepared: CampaignStockoutPreparationReceipt,
    base: CampaignStockoutTrialReceipt,
) -> None:
    if (
        plan.kind != "conditional"
        or base.plan.kind != "base"
        or base.operation_id != plan.base_operation_id
        or base.prepared != prepared
        or (base.plan.family, base.plan.variant) != (plan.family, plan.variant)
    ):
        raise SnapshotError("stockout_trial_original_completed_base_required")


def run_campaign_stockout_trial(
    dataset: Path,
    output_root: Path,
    *,
    journal: Path,
    operation_id: str,
    plan: CampaignStockoutTrialPlan,
    prepared: CampaignStockoutPreparationReceipt,
    base_bundle: Path | None = None,
    base: CampaignStockoutTrialReceipt | None = None,
) -> tuple[Path, CampaignStockoutTrialReceipt]:
    ledger = campaign_journal.inspect(journal)
    if not isinstance(ledger, RoleAwareCampaignJournal):
        raise SnapshotError("stockout_trial_requires_role_aware_journal")
    operation = _operation(ledger, operation_id)
    started = perf_counter()
    with campaign_journal.audited_operation(
        journal,
        operation_id,
        failure_codes=frozenset(
            {
                "stockout_conditional_nonpositive_raw_score_slope",
                "stockout_trial_fit_did_not_converge",
            }
        ),
    ) as handle:
        plan = CampaignStockoutTrialPlan.model_validate(plan.model_dump(mode="python"))
        prepared = CampaignStockoutPreparationReceipt.model_validate(
            prepared.model_dump(mode="python")
        )
        _binding(ledger, operation, plan, prepared)
        validate_completed_stockout_preparation(journal, prepared)
        if plan.kind == "conditional":
            if base is None or base_bundle is None:
                raise SnapshotError("stockout_trial_original_completed_base_required")
            base = CampaignStockoutTrialReceipt.model_validate(base.model_dump(mode="python"))
            _base_binding(plan, prepared, base)
            validate_completed_stockout_trial(journal, base)
        elif base is not None or base_bundle is not None:
            raise SnapshotError("stockout_trial_unrelated_base_artifact")
        checked_directory(output_root)
        root = output_root / str(handle.reservation.reservation_id)
        root.mkdir(mode=0o700)
        (root / "tmp").mkdir(mode=0o700)
        write(
            root / "request.json",
            dict(
                dataset=str(dataset.absolute()),
                plan=plan.model_dump(mode="json"),
                prepared=prepared.model_dump(mode="json"),
                operation_id=operation_id,
                reservation_id=str(handle.reservation.reservation_id),
                base=base.model_dump(mode="json") if base else None,
                base_bundle=str(base_bundle.absolute()) if base_bundle else None,
            ),
        )
        peak = None
        phases: list[dict[str, Any]] = []
        for phase in ("fit", "reload"):
            measured = monitor(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(Path(__file__).with_name("campaign_stockout_trial_entry.py")),
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
            observed = measured["sampled_tree_peak_rss_bytes"]
            if observed is not None:
                peak = max(peak or 0, int(observed))
            write(root / (phase + "-resources.json"), measured)
            handle.cost = CampaignCost(
                wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
            )
            if measured["status"] != "passed":
                error_path = root / (phase + "-error.json")
                if measured.get("reason") == "worker_exit" and error_path.exists():
                    error = read(error_path)
                    worker_peak = error.get("worker_peak_rss_bytes")
                    code = error.get("error_code")
                    if (
                        type(worker_peak) is not int
                        or worker_peak <= 0
                        or not isinstance(code, str)
                        or re.fullmatch(r"(?:stockout|campaign_stockout)_[a-z0-9_]{1,100}", code)
                        is None
                    ):
                        raise SnapshotError("stockout_trial_worker_error_evidence_invalid")
                    peak = max(peak or 0, worker_peak)
                    handle.cost = CampaignCost(
                        wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
                    )
                    raise SnapshotError(code)
                raise SnapshotError("stockout_trial_phase_failed_" + phase)
            result = read(root / (phase + ".json"))
            worker_peak = result.get("worker_peak_rss_bytes")
            if type(worker_peak) is not int or worker_peak <= 0:
                raise SnapshotError("stockout_trial_worker_resource_evidence_missing")
            peak = max(peak or 0, worker_peak)
            handle.cost = CampaignCost(
                wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
            )
            if peak > plan.resources.tree_rss_bytes:
                raise SnapshotError("stockout_trial_worker_peak_rss_limit")
            receipt = CampaignStockoutTrialReceipt.model_validate_json(
                canonical_bytes(result.pop("receipt"))
            )
            # Keep the complete ancestry once in the final receipt. The phase
            # records bind that same receipt by hash and retain every measurement.
            result["receipt_sha256"] = receipt.content_sha256()
            phases.append(dict(phase=phase, monitor=measured, worker=result))
        result = phases[-1]["worker"]
        if (
            result.get("cold_reload_passed") is not True
            or result.get("full_role_rows") != receipt.role_file.rows
            or phases[0]["worker"]["receipt_sha256"] != receipt.content_sha256()
            or receipt.plan != plan
            or receipt.prepared != prepared
            or receipt.operation_id != operation_id
            or receipt.reservation_id != str(handle.reservation.reservation_id)
            or receipt.base_receipt_sha256 != (base.content_sha256() if base else None)
            or receipt.base_pipeline_sha256 != (base.pipeline_sha256 if base else None)
        ):
            raise SnapshotError("stockout_trial_worker_receipt_mismatch")
        receipt = CampaignStockoutTrialReceipt.model_validate(
            {
                **receipt.model_dump(mode="python"),
                "worker_evidence": TypeAdapter(dict[str, JsonValue]).validate_python(
                    {"phases": phases}
                ),
            }
        )
        verify_trial_artifact(root / "bundle", receipt)
        fsync_tree(root)
        if (
            perf_counter() - started > plan.resources.wall_seconds
            or scratch_bytes((root,)) > plan.resources.scratch_bytes
        ):
            raise SnapshotError("stockout_trial_completion_resource_limit")
        _store_receipt(journal, receipt)
        handle.evidence_sha256 = receipt.content_sha256()
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=peak,
            artifact_bytes=receipt.model_artifact_bytes,
        )
    validate_completed_stockout_trial(journal, receipt)
    return root / "bundle", receipt


def validate_completed_stockout_trial(journal: Path, receipt: CampaignStockoutTrialReceipt) -> None:
    """Validate original durable evidence without reopening role payloads or models."""
    receipt = CampaignStockoutTrialReceipt.model_validate(receipt.model_dump(mode="python"))
    ledger = campaign_journal.inspect(journal)
    if not isinstance(ledger, RoleAwareCampaignJournal):
        raise SnapshotError("stockout_trial_requires_role_aware_journal")
    _binding(ledger, _operation(ledger, receipt.operation_id), receipt.plan, receipt.prepared)
    validate_completed_stockout_preparation(journal, receipt.prepared)
    completion = next(
        (
            e
            for e in ledger.events
            if e.kind == "finished" and e.reservation_id == receipt.reservation_id
        ),
        None,
    )
    if completion is None or (
        completion.operation_id != receipt.operation_id
        or completion.result != "completed"
        or completion.evidence_sha256 != receipt.content_sha256()
    ):
        raise SnapshotError("stockout_trial_receipt_not_completed")
    with regular_file(journal, "receipts/" + receipt.reservation_id + ".json") as stream:
        if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
            raise SnapshotError("stockout_trial_private_receipt_required")
        raw = stream.read(MAX_RECEIPT_BYTES + 1)
    if raw != canonical_bytes(receipt.model_dump(mode="json")) + b"\n":
        raise SnapshotError("stockout_trial_stored_receipt_mismatch")
    if receipt.plan.kind == "conditional":
        event = next(
            (
                e
                for e in ledger.events
                if e.kind == "finished" and e.operation_id == receipt.plan.base_operation_id
            ),
            None,
        )
        if (
            event is None
            or event.result != "completed"
            or event.evidence_sha256 != receipt.base_receipt_sha256
        ):
            raise SnapshotError("stockout_trial_base_receipt_not_completed")
        with regular_file(journal, "receipts/" + str(event.reservation_id) + ".json") as stream:
            stored = stream.read(MAX_RECEIPT_BYTES + 1)
        base = CampaignStockoutTrialReceipt.model_validate_json(stored)
        _base_binding(receipt.plan, receipt.prepared, base)
        if (
            base.content_sha256() != receipt.base_receipt_sha256
            or base.pipeline_sha256 != receipt.base_pipeline_sha256
        ):
            raise SnapshotError("stockout_trial_stored_base_receipt_mismatch")
        validate_completed_stockout_trial(journal, base)
