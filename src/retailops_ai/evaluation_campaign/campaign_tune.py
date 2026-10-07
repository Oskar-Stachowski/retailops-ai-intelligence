"""Reserve every Tune trial pass before I/O; keep selection separate from qualification."""

import json
import os
import stat
from collections import Counter
from pathlib import Path
from time import perf_counter

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
from retailops_ai.evaluation_campaign.campaign_fit import _bundle_inventory
from retailops_ai.evaluation_campaign.campaign_generation import _environment
from retailops_ai.evaluation_campaign.campaign_generation_monitor import monitor, scratch_bytes
from retailops_ai.evaluation_campaign.campaign_generation_worker import read, write
from retailops_ai.evaluation_campaign.campaign_score import verify_campaign_forecast_scores
from retailops_ai.evaluation_campaign.campaign_score_contract import (
    FAMILIES,
    CampaignForecastScoreReceipt,
)
from retailops_ai.evaluation_campaign.campaign_tune_contract import (
    CampaignForecastTunePlan,
    CampaignForecastTuneReceipt,
    CampaignForecastTuneSelection,
)
from retailops_ai.evaluation_campaign.campaign_tune_data import choose
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
        or operation.role != "tune"
    ):
        raise SnapshotError("campaign_tune_requires_development_tune_operation")
    return operation


def _binding(
    ledger: CampaignJournal,
    operation: CampaignOperationPlan,
    plan: CampaignForecastTunePlan,
    exported: CampaignDevelopmentExportReceipt,
    scores: dict[str, CampaignForecastScoreReceipt],
) -> None:
    if (
        set(scores) != set(plan.score_operation_ids)
        or operation.operation_id in scores
        or operation.execution_recipe_sha256 != plan.content_sha256()
        or operation.source_recipe_sha256 != plan.source_recipe_sha256
        or exported.operation_id != plan.export_operation_id
        or exported.plan.source_recipe_sha256 != plan.source_recipe_sha256
        or exported.protocol_sha256 != ledger.protocol_sha256
        or exported.runtime_code_sha256 != ledger.protocol.runtime.code_sha256
        or plan.campaign_selection_policy_sha256 != ledger.protocol.selection_policy_sha256
        or plan.forecast_quality_policy_sha256
        != ledger.protocol.use_case_quality_policy_sha256["forecast"]
        or not {plan.export_operation_id, *plan.score_operation_ids} <= set(operation.prerequisites)
    ):
        raise SnapshotError("campaign_tune_frozen_plan_or_parent_binding_mismatch")
    fits: Counter[str] = Counter()
    populations = set()
    plans = {o.operation_id: o for o in ledger.protocol.operations}
    for key in plan.score_operation_ids:
        score = scores[key]
        parent_operation = plans.get(key)
        if (
            score.operation_id != key
            or score.plan.role != "tune"
            or score.plan.export_operation_id != plan.export_operation_id
            or score.plan.source_recipe_sha256 != plan.source_recipe_sha256
            or score.plan.worker_environment_lock_sha256 != plan.worker_environment_lock_sha256
            or score.export_receipt_sha256 != exported.content_sha256()
            or score.dataset_id != exported.dataset_id
            or score.protocol_sha256 != ledger.protocol_sha256
            or score.runtime_code_sha256 != ledger.protocol.runtime.code_sha256
            or score.rows > plan.max_rows
            or parent_operation is None
            or parent_operation.action != "model_score"
            or parent_operation.use_case != "forecast"
            or parent_operation.role != "tune"
            or parent_operation.execution_recipe_sha256 != score.plan.content_sha256()
        ):
            raise SnapshotError("campaign_tune_score_parent_binding_mismatch")
        populations.add(
            (
                score.rows,
                score.eligible_rows,
                score.keys_sha256,
                score.eligible_keys_sha256,
                score.role_population_sha256,
            )
        )
        for family in FAMILIES:
            fit = score.plan.fit_operation_ids[family]
            fit_operation = plans.get(fit)
            if fit_operation is None or fit_operation.forecast_family != family:
                raise SnapshotError("campaign_tune_fit_family_mismatch")
            fits[fit] += 1
    expected_fits = {
        o.operation_id
        for o in ledger.protocol.operations
        if o.phase == "development"
        and o.action == "model_fit"
        and o.use_case == "forecast"
        and o.source_recipe_sha256 == plan.source_recipe_sha256
    }
    if len(populations) != 1 or set(fits) != expected_fits or any(n != 1 for n in fits.values()):
        raise SnapshotError("campaign_tune_requires_every_frozen_trial_once_on_common_keys")


def select_campaign_forecast(
    dataset: Path,
    bundles: dict[str, Path],
    worker_python: Path,
    output_root: Path,
    *,
    journal: Path,
    operation_id: str,
    plan: CampaignForecastTunePlan,
    exported: CampaignDevelopmentExportReceipt,
    scores: dict[str, CampaignForecastScoreReceipt],
) -> tuple[Path, CampaignForecastTuneReceipt]:
    ledger = campaign_journal.inspect(journal)
    operation = _operation(ledger, operation_id)
    started = perf_counter()
    with campaign_journal.audited_operation(journal, operation_id) as handle:
        plan = CampaignForecastTunePlan.model_validate_json(
            canonical_bytes(plan.model_dump(mode="json"))
        )
        exported = CampaignDevelopmentExportReceipt.model_validate_json(
            canonical_bytes(exported.model_dump(mode="json"))
        )
        scores = {
            key: CampaignForecastScoreReceipt.model_validate_json(
                canonical_bytes(score.model_dump(mode="json"))
            )
            for key, score in scores.items()
        }
        _binding(ledger, operation, plan, exported, scores)
        if set(bundles) != set(scores):
            raise SnapshotError("campaign_tune_bundle_inventory_mismatch")
        validate_completed_export(journal, exported)
        for key in plan.score_operation_ids:
            verify_campaign_forecast_scores(bundles[key], journal=journal, receipt=scores[key])
        checked_directory(output_root)
        if stat.S_IMODE(output_root.stat().st_mode) != 0o700:
            raise SnapshotError("campaign_tune_private_output_directory_required")
        root = output_root / str(handle.reservation.reservation_id)
        root.mkdir(mode=0o700)
        (root / "tmp").mkdir(mode=0o700)
        write(
            root / "request.json",
            {
                "dataset": str(dataset.absolute()),
                "bundles": {key: str(bundles[key].absolute()) for key in plan.score_operation_ids},
                "scores": {
                    key: scores[key].model_dump(mode="json") for key in plan.score_operation_ids
                },
                "plan": plan.model_dump(mode="json"),
                "exported": exported.model_dump(mode="json"),
                "runtime": ledger.protocol.runtime.model_dump(mode="json"),
            },
        )
        measured = monitor(
            [
                str(worker_python),
                "-I",
                "-B",
                str(Path(__file__).with_name("campaign_tune_entry.py")),
                str(root),
            ],
            root=root,
            log=root / "select.log",
            env=_environment(root),
            scratch=(root,),
            resources=plan.resources,
            deadline=started + plan.resources.wall_seconds,
        )
        peak = measured["sampled_tree_peak_rss_bytes"]
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
        )
        write(root / "select-resources.json", measured)
        if measured["status"] != "passed":
            raise SnapshotError("campaign_tune_worker_failed")
        result = read(root / "select.json")
        peak = max(peak or 0, result["worker_peak_rss_bytes"])
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
        )
        first = scores[plan.score_operation_ids[0]]
        population = (
            "rows",
            "eligible_rows",
            "keys_sha256",
            "eligible_keys_sha256",
            "role_population_sha256",
        )
        if (
            result["worker_peak_rss_bytes"] > plan.resources.tree_rss_bytes
            or any(result[key] != getattr(first, key) for key in population)
            or result["trial_count"] != len(scores)
            or result["full_tune_label_passes"] != len(scores)
            or result["calibration_label_passes"] != 0
            or result["independent_or_final_label_passes"] != 0
        ):
            raise SnapshotError("campaign_tune_worker_population_scope_or_peak_mismatch")
        files, size = _bundle_inventory(root / "bundle", plan.max_output_bytes)
        receipt = CampaignForecastTuneReceipt(
            protocol_sha256=ledger.protocol_sha256,
            operation_id=operation_id,
            reservation_id=str(handle.reservation.reservation_id),
            plan=plan,
            export_receipt_sha256=exported.content_sha256(),
            score_receipt_sha256={
                key: scores[key].content_sha256() for key in plan.score_operation_ids
            },
            dataset_id=exported.dataset_id,
            runtime_code_sha256=ledger.protocol.runtime.code_sha256,
            **{key: result[key] for key in (*population, "baseline_predictions_sha256")},
            selection=CampaignForecastTuneSelection.model_validate_json(
                canonical_bytes(result["selection"])
            ),
            artifact_sha256=canonical_sha256(files),
            artifact_bytes=size,
            artifact_files=files,
            worker_evidence=TypeAdapter(dict[str, JsonValue]).validate_python(
                {"monitor": measured, "worker": result}
            ),
        )
        _verify_bundle(root / "bundle", receipt)
        fsync_tree(root)
        if (
            perf_counter() - started > plan.resources.wall_seconds
            or scratch_bytes((root,)) > plan.resources.scratch_bytes
        ):
            raise SnapshotError("campaign_tune_completion_resource_limit")
        _store_receipt(journal, receipt)
        handle.evidence_sha256 = receipt.content_sha256()
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=peak,
            artifact_bytes=size,
        )
    validate_completed_tune(journal, receipt)
    return root / "bundle", receipt


def _verify_bundle(bundle: Path, receipt: CampaignForecastTuneReceipt) -> None:
    files, size = _bundle_inventory(bundle, receipt.plan.max_output_bytes)
    if files != receipt.artifact_files or size != receipt.artifact_bytes:
        raise SnapshotError("campaign_tune_artifact_checksum_mismatch")
    if (
        CampaignForecastTunePlan.model_validate_json(read_bytes(bundle, "plan.json"))
        != receipt.plan
    ):
        raise SnapshotError("campaign_tune_artifact_plan_mismatch")
    parents = json.loads(read_bytes(bundle, "parents.json", receipt.plan.max_output_bytes))
    scores = {
        key: CampaignForecastScoreReceipt.model_validate_json(canonical_bytes(value))
        for key, value in parents["scores"].items()
    }
    if (
        parents["export_receipt_sha256"] != receipt.export_receipt_sha256
        or {key: score.content_sha256() for key, score in scores.items()}
        != receipt.score_receipt_sha256
    ):
        raise SnapshotError("campaign_tune_artifact_parent_mismatch")
    trials = json.loads(read_bytes(bundle, "metrics.json", receipt.plan.max_output_bytes))["trials"]
    population = (
        "rows",
        "eligible_rows",
        "keys_sha256",
        "eligible_keys_sha256",
        "role_population_sha256",
        "baseline_predictions_sha256",
    )
    if any(any(trial[key] != getattr(receipt, key) for key in population) for trial in trials):
        raise SnapshotError("campaign_tune_artifact_population_mismatch")
    selection = CampaignForecastTuneSelection.model_validate_json(
        read_bytes(bundle, "selection.json")
    )
    if selection != receipt.selection or selection != choose(trials, scores, receipt.plan):
        raise SnapshotError("campaign_tune_artifact_selection_mismatch")


def validate_completed_tune(journal: Path, receipt: CampaignForecastTuneReceipt) -> None:
    receipt = CampaignForecastTuneReceipt.model_validate_json(
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
        or receipt.plan.campaign_selection_policy_sha256 != ledger.protocol.selection_policy_sha256
        or receipt.plan.forecast_quality_policy_sha256
        != ledger.protocol.use_case_quality_policy_sha256["forecast"]
        or not {receipt.plan.export_operation_id, *receipt.plan.score_operation_ids}
        <= set(operation.prerequisites)
        or completion is None
        or completion.operation_id != receipt.operation_id
        or completion.result != "completed"
        or completion.evidence_sha256 != receipt.content_sha256()
        or completion.cost is None
        or completion.cost.artifact_bytes != receipt.artifact_bytes
    ):
        raise SnapshotError("campaign_tune_receipt_not_completed")
    try:
        with regular_file(journal, "receipts/" + receipt.reservation_id + ".json") as stream:
            if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
                raise SnapshotError("campaign_tune_private_receipt_required")
            raw = stream.read(MAX_RECEIPT_BYTES + 1)
    except OSError:
        raise SnapshotError("campaign_tune_receipt_unavailable") from None
    if raw != canonical_bytes(receipt.model_dump(mode="json")) + b"\n":
        raise SnapshotError("campaign_tune_stored_receipt_mismatch")


def verify_campaign_forecast_selection(
    bundle: Path, *, journal: Path, receipt: CampaignForecastTuneReceipt
) -> None:
    """Check completed selection evidence without labels, calibration or new permission."""
    validate_completed_tune(journal, receipt)
    _verify_bundle(bundle, receipt)
