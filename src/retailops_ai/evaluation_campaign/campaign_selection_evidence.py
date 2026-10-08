"""Completed three-use selection evidence before any public final source access.

The generic journal freeze audits ordering. It alone does not prove complete
quality evidence or authorize generation, export or evaluation of final data.
This verifier reads only durable development receipts and published bundles.
"""

import json
import os
import stat
from pathlib import Path
from typing import Any

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_export import MAX_RECEIPT_BYTES
from retailops_ai.evaluation_campaign.campaign_robust_receipt import (
    ForecastEvaluationReceipt,
    parse_forecast_evaluation_receipt,
)
from retailops_ai.source_snapshot.files import SnapshotError, regular_file


def _maximum(receipt: dict[str, Any]) -> int:
    maximum = receipt.get("plan", {}).get("max_output_bytes")
    if type(maximum) is not int or not 1024 <= maximum <= 32 * 1024**3:
        raise SnapshotError("campaign_evaluation_selection_artifact_budget_missing")
    return maximum


def verify_completed_campaign_selection(
    journal: Path, bundles: dict[str, Path]
) -> tuple[str, ForecastEvaluationReceipt]:
    ledger = campaign_journal.inspect(journal)
    event = next((e for e in ledger.events if e.kind == "selection_frozen"), None)
    if (
        event is None
        or event.selection is None
        or set(bundles) != {"forecast", "anomaly", "stockout"}
    ):
        raise SnapshotError("campaign_evaluation_requires_completed_three_use_selection")
    operations = {o.operation_id: o for o in ledger.protocol.operations}
    forecast = None
    for selected in event.selection.bundles:
        completed = next(
            (
                e
                for e in ledger.events
                if e.kind == "finished"
                and e.result == "completed"
                and e.evidence_sha256 == selected.selection_evidence_sha256
                and e.sequence < event.sequence
            ),
            None,
        )
        operation = operations.get(str(completed.operation_id)) if completed else None
        if (
            completed is None
            or operation is None
            or operation.phase != "development"
            or operation.action != "model_score"
            or operation.role != "development_evaluation"
            or operation.use_case != selected.use_case
        ):
            raise SnapshotError("campaign_evaluation_selection_has_no_completed_use_evaluation")
        try:
            with regular_file(
                journal, "receipts/" + str(completed.reservation_id) + ".json"
            ) as stream:
                if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
                    raise SnapshotError("campaign_evaluation_private_selection_receipt_required")
                raw = stream.read(MAX_RECEIPT_BYTES + 1)
        except OSError:
            raise SnapshotError("campaign_evaluation_selection_receipt_unavailable") from None
        if len(raw) > MAX_RECEIPT_BYTES:
            raise SnapshotError("campaign_evaluation_selection_receipt_size_limit")
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise SnapshotError("campaign_evaluation_selection_receipt_invalid") from None
        if not isinstance(value, dict):
            raise SnapshotError("campaign_evaluation_selection_receipt_invalid")
        plan = value.get("plan")
        recipe = value.get("recipe")
        if (
            not isinstance(plan, dict)
            or not isinstance(recipe, dict)
            or raw != canonical_bytes(value) + b"\n"
            or canonical_sha256(value) != selected.selection_evidence_sha256
            or value.get("operation_id") != operation.operation_id
            or value.get("reservation_id") != completed.reservation_id
            or value.get("use_case") != selected.use_case
            or value.get("protocol_sha256") != ledger.protocol_sha256
            or value.get("runtime_code_sha256") != ledger.protocol.runtime.code_sha256
            or plan.get("phase") != "development"
            or plan.get("role") != "development_evaluation"
            or plan.get("source_recipe_sha256") != operation.source_recipe_sha256
            or canonical_sha256(recipe) != operation.execution_recipe_sha256
            or value.get("critical_segment_inventory_complete") is not True
            or value.get("block_uncertainty_complete") is not True
            or value.get("quality_qualified") is not True
            or value.get("final_test_accessed") is not False
            or value.get("selection_components")
            != selected.model_dump(mode="json", exclude={"use_case", "selection_evidence_sha256"})
            or completed.cost is None
            or completed.cost.artifact_bytes != value.get("artifact_bytes")
            or completed.cost.peak_process_tree_rss_bytes is None
        ):
            raise SnapshotError("campaign_evaluation_selection_receipt_incomplete_or_mismatched")
        if selected.use_case == "forecast":
            forecast = parse_forecast_evaluation_receipt(raw)
            # Import only at verification time: the evaluator imports final
            # export/generation, which use this shared boundary verifier.
            from retailops_ai.evaluation_campaign.campaign_evaluation import (
                verify_campaign_forecast_evaluation,
            )

            verify_campaign_forecast_evaluation(
                bundles[selected.use_case], journal=journal, receipt=forecast
            )
        else:
            expected_version = "ai09-campaign-" + selected.use_case + "-evaluation-receipt-1.0.0"
            if value.get("version") != expected_version:
                raise SnapshotError("campaign_evaluation_other_use_selection_receipt_version")
            from retailops_ai.evaluation_campaign.campaign_fit import _bundle_inventory

            files, size = _bundle_inventory(bundles[selected.use_case], _maximum(value))
            if (
                files != value.get("artifact_files")
                or canonical_sha256(files) != value.get("artifact_sha256")
                or size != value.get("artifact_bytes")
            ):
                raise SnapshotError("campaign_evaluation_other_use_selection_artifact_mismatch")
    if forecast is None:
        raise SnapshotError("campaign_evaluation_selected_forecast_missing")
    return canonical_sha256(event.selection.model_dump(mode="json")), forecast
