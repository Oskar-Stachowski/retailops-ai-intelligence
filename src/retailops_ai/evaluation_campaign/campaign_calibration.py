"""Reserve calibration before parent/label I/O; preserve frozen Tune choice and costs."""

import json
import os
import stat
from pathlib import Path
from time import perf_counter
from typing import Literal

from pydantic import JsonValue, TypeAdapter

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_calibration_contract import (
    CampaignForecastCalibration,
    CampaignForecastCalibrationPlan,
    CampaignForecastCalibrationReceipt,
)
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
from retailops_ai.evaluation_campaign.campaign_tune import verify_campaign_forecast_selection
from retailops_ai.evaluation_campaign.campaign_tune_contract import CampaignForecastTuneReceipt
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    read_bytes,
    regular_file,
)
from retailops_ai.source_snapshot.publish import fsync_tree

POPULATION = (
    "rows",
    "eligible_rows",
    "keys_sha256",
    "eligible_keys_sha256",
    "role_population_sha256",
)


def _operation(ledger: CampaignJournal, operation_id: str) -> CampaignOperationPlan:
    operation = next(
        (o for o in ledger.protocol.operations if o.operation_id == operation_id), None
    )
    if operation is None or (
        operation.phase != "development"
        or operation.action != "calibrator_fit"
        or operation.use_case != "forecast"
        or operation.role != "calibration"
    ):
        raise SnapshotError("campaign_calibration_requires_development_calibrator_operation")
    return operation


def _selected_score(
    plan: CampaignForecastCalibrationPlan,
    exported: CampaignDevelopmentExportReceipt,
    tune: CampaignForecastTuneReceipt,
    tune_scores: dict[str, CampaignForecastScoreReceipt],
    scores: dict[str, CampaignForecastScoreReceipt],
) -> CampaignForecastScoreReceipt:
    """Match every frozen triplet, never select another architecture on Calibration."""
    if (
        set(scores) != set(plan.score_operation_ids)
        or {key: value.content_sha256() for key, value in tune_scores.items()}
        != tune.score_receipt_sha256
        or tune.operation_id != plan.tune_operation_id
        or tune.plan.source_recipe_sha256 != plan.source_recipe_sha256
        or tune.plan.export_operation_id != plan.export_operation_id
        or tune.plan.worker_environment_lock_sha256 != plan.worker_environment_lock_sha256
        or tune.plan.forecast_quality_policy_sha256 != plan.forecast_quality_policy_sha256
        or exported.operation_id != plan.export_operation_id
        or exported.plan.source_recipe_sha256 != plan.source_recipe_sha256
        or tune.export_receipt_sha256 != exported.content_sha256()
        or tune.dataset_id != exported.dataset_id
        or tune.protocol_sha256 != exported.protocol_sha256
        or tune.runtime_code_sha256 != exported.runtime_code_sha256
        or tune.selection.status != "selected_for_independent_evaluation"
        or tune.selection.median is None
    ):
        raise SnapshotError("campaign_calibration_requires_bound_completed_tune_selection")

    def triplet(score: CampaignForecastScoreReceipt) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            (
                score.plan.fit_operation_ids[f],
                score.fit_receipt_sha256[f],
                score.model_artifact_sha256[f],
            )
            for f in FAMILIES
        )

    matched = {}
    populations = set()
    for key in plan.score_operation_ids:
        score = scores[key]
        if (
            score.operation_id != key
            or score.plan.role != "calibration"
            or score.plan.source_recipe_sha256 != plan.source_recipe_sha256
            or score.plan.export_operation_id != plan.export_operation_id
            or score.plan.worker_environment_lock_sha256 != plan.worker_environment_lock_sha256
            or score.export_receipt_sha256 != exported.content_sha256()
            or score.dataset_id != exported.dataset_id
            or score.protocol_sha256 != exported.protocol_sha256
            or score.runtime_code_sha256 != exported.runtime_code_sha256
            or score.rows > plan.max_rows
        ):
            raise SnapshotError("campaign_calibration_score_parent_binding_mismatch")
        identity = triplet(score)
        if identity in matched:
            raise SnapshotError("campaign_calibration_duplicate_trial_triplet")
        matched[identity] = score
        populations.add(tuple(getattr(score, field) for field in POPULATION))
    frozen = [triplet(tune_scores[key]) for key in tune.plan.score_operation_ids]
    if len(set(frozen)) != len(frozen) or set(matched) != set(frozen) or len(populations) != 1:
        raise SnapshotError("campaign_calibration_requires_every_tune_triplet_once_on_common_keys")
    choice = tune.selection.median
    selected_id = choice.score_operation_id or tune.plan.score_operation_ids[0]
    selected = tune_scores.get(selected_id)
    if selected is None:
        raise SnapshotError("campaign_calibration_selected_tune_trial_missing")
    if choice.fit_operation_id is not None:
        family: Literal["hgb", "tensorflow"] = (
            "tensorflow" if choice.model == "tensorflow" else "hgb"
        )
        if (
            selected.plan.fit_operation_ids[family] != choice.fit_operation_id
            or selected.model_artifact_sha256[family] != choice.model_artifact_sha256
        ):
            raise SnapshotError("campaign_calibration_selected_model_binding_mismatch")
    return matched[triplet(selected)]


def _binding(
    ledger: CampaignJournal,
    operation: CampaignOperationPlan,
    plan: CampaignForecastCalibrationPlan,
    exported: CampaignDevelopmentExportReceipt,
    tune: CampaignForecastTuneReceipt,
    scores: dict[str, CampaignForecastScoreReceipt],
) -> None:
    if (
        operation.execution_recipe_sha256 != plan.content_sha256()
        or operation.source_recipe_sha256 != plan.source_recipe_sha256
        or operation.operation_id in {plan.tune_operation_id, *plan.score_operation_ids}
        or exported.protocol_sha256 != ledger.protocol_sha256
        or exported.runtime_code_sha256 != ledger.protocol.runtime.code_sha256
        or tune.protocol_sha256 != ledger.protocol_sha256
        or plan.forecast_quality_policy_sha256
        != ledger.protocol.use_case_quality_policy_sha256["forecast"]
        or not {plan.export_operation_id, plan.tune_operation_id, *plan.score_operation_ids}
        <= set(operation.prerequisites)
        or set(scores) != set(plan.score_operation_ids)
    ):
        raise SnapshotError("campaign_calibration_frozen_plan_or_prerequisite_mismatch")
    operations = {o.operation_id: o for o in ledger.protocol.operations}
    for key, score in scores.items():
        parent = operations.get(key)
        if (
            parent is None
            or parent.phase != "development"
            or parent.action != "model_score"
            or parent.use_case != "forecast"
            or parent.role != "calibration"
            or parent.execution_recipe_sha256 != score.plan.content_sha256()
            or parent.source_recipe_sha256 != plan.source_recipe_sha256
            or plan.tune_operation_id not in parent.prerequisites
        ):
            raise SnapshotError("campaign_calibration_scores_must_follow_tune_freeze")


def fit_campaign_forecast_calibration(
    dataset: Path,
    tune_bundle: Path,
    bundles: dict[str, Path],
    worker_python: Path,
    output_root: Path,
    *,
    journal: Path,
    operation_id: str,
    plan: CampaignForecastCalibrationPlan,
    exported: CampaignDevelopmentExportReceipt,
    tune: CampaignForecastTuneReceipt,
    scores: dict[str, CampaignForecastScoreReceipt],
) -> tuple[Path, CampaignForecastCalibrationReceipt]:
    ledger = campaign_journal.inspect(journal)
    operation = _operation(ledger, operation_id)
    started = perf_counter()
    with campaign_journal.audited_operation(journal, operation_id) as handle:
        plan = CampaignForecastCalibrationPlan.model_validate_json(
            canonical_bytes(plan.model_dump(mode="json"))
        )
        exported = CampaignDevelopmentExportReceipt.model_validate_json(
            canonical_bytes(exported.model_dump(mode="json"))
        )
        tune = CampaignForecastTuneReceipt.model_validate_json(
            canonical_bytes(tune.model_dump(mode="json"))
        )
        scores = {
            key: CampaignForecastScoreReceipt.model_validate_json(
                canonical_bytes(value.model_dump(mode="json"))
            )
            for key, value in scores.items()
        }
        _binding(ledger, operation, plan, exported, tune, scores)
        if set(bundles) != set(scores):
            raise SnapshotError("campaign_calibration_bundle_inventory_mismatch")
        validate_completed_export(journal, exported)
        verify_campaign_forecast_selection(tune_bundle, journal=journal, receipt=tune)
        tune_scores = {
            key: CampaignForecastScoreReceipt.model_validate_json(canonical_bytes(value))
            for key, value in json.loads(
                read_bytes(tune_bundle, "parents.json", tune.plan.max_output_bytes)
            )["scores"].items()
        }
        selected = _selected_score(plan, exported, tune, tune_scores, scores)
        for key in plan.score_operation_ids:
            verify_campaign_forecast_scores(bundles[key], journal=journal, receipt=scores[key])
        checked_directory(output_root)
        if stat.S_IMODE(output_root.stat().st_mode) != 0o700:
            raise SnapshotError("campaign_calibration_private_output_directory_required")
        root = output_root / str(handle.reservation.reservation_id)
        root.mkdir(mode=0o700)
        (root / "tmp").mkdir(mode=0o700)
        write(
            root / "request.json",
            {
                "dataset": str(dataset.absolute()),
                "bundle": str(bundles[selected.operation_id].absolute()),
                "scores": {key: value.model_dump(mode="json") for key, value in scores.items()},
                "tune_scores": {
                    key: value.model_dump(mode="json") for key, value in tune_scores.items()
                },
                "tune": tune.model_dump(mode="json"),
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
                str(Path(__file__).with_name("campaign_calibration_entry.py")),
                str(root),
            ],
            root=root,
            log=root / "calibrate.log",
            env=_environment(root),
            scratch=(root,),
            resources=plan.resources,
            deadline=started + plan.resources.wall_seconds,
        )
        peak = measured["sampled_tree_peak_rss_bytes"]
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
        )
        write(root / "calibrate-resources.json", measured)
        if measured["status"] != "passed":
            raise SnapshotError("campaign_calibration_worker_failed")
        result = read(root / "calibrate.json")
        peak = max(peak or 0, result["worker_peak_rss_bytes"])
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
        )
        if (
            result["worker_peak_rss_bytes"] > plan.resources.tree_rss_bytes
            or any(result[key] != getattr(selected, key) for key in POPULATION)
            or result["full_calibration_label_passes"] != 1
            or result["tune_label_passes"] != 0
            or result["independent_or_final_label_passes"] != 0
            or result["architecture_reselected"] is not False
        ):
            raise SnapshotError("campaign_calibration_worker_population_scope_or_peak_mismatch")
        files, size = _bundle_inventory(root / "bundle", plan.max_output_bytes)
        receipt = CampaignForecastCalibrationReceipt(
            protocol_sha256=ledger.protocol_sha256,
            operation_id=operation_id,
            reservation_id=str(handle.reservation.reservation_id),
            plan=plan,
            export_receipt_sha256=exported.content_sha256(),
            tune_receipt_sha256=tune.content_sha256(),
            score_receipt_sha256={key: value.content_sha256() for key, value in scores.items()},
            dataset_id=exported.dataset_id,
            runtime_code_sha256=ledger.protocol.runtime.code_sha256,
            **{key: result[key] for key in POPULATION},
            calibration=CampaignForecastCalibration.model_validate_json(
                canonical_bytes(result["calibration"])
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
            raise SnapshotError("campaign_calibration_completion_resource_limit")
        _store_receipt(journal, receipt)
        handle.evidence_sha256 = receipt.content_sha256()
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=peak,
            artifact_bytes=size,
        )
    validate_completed_calibration(journal, receipt)
    return root / "bundle", receipt


def _verify_bundle(bundle: Path, receipt: CampaignForecastCalibrationReceipt) -> None:
    files, size = _bundle_inventory(bundle, receipt.plan.max_output_bytes)
    if files != receipt.artifact_files or size != receipt.artifact_bytes:
        raise SnapshotError("campaign_calibration_artifact_checksum_mismatch")
    if (
        CampaignForecastCalibrationPlan.model_validate_json(read_bytes(bundle, "plan.json"))
        != receipt.plan
    ):
        raise SnapshotError("campaign_calibration_artifact_plan_mismatch")
    parents = json.loads(read_bytes(bundle, "parents.json", receipt.plan.max_output_bytes))
    exported = CampaignDevelopmentExportReceipt.model_validate_json(
        canonical_bytes(parents["exported"])
    )
    tune = CampaignForecastTuneReceipt.model_validate_json(canonical_bytes(parents["tune"]))
    tune_scores = {
        key: CampaignForecastScoreReceipt.model_validate_json(canonical_bytes(value))
        for key, value in parents["tune_scores"].items()
    }
    scores = {
        key: CampaignForecastScoreReceipt.model_validate_json(canonical_bytes(value))
        for key, value in parents["scores"].items()
    }
    selected = _selected_score(receipt.plan, exported, tune, tune_scores, scores)
    if (
        exported.content_sha256() != receipt.export_receipt_sha256
        or tune.content_sha256() != receipt.tune_receipt_sha256
        or {key: score.content_sha256() for key, score in scores.items()}
        != receipt.score_receipt_sha256
        or receipt.dataset_id != exported.dataset_id
        or receipt.protocol_sha256 != exported.protocol_sha256
        or receipt.runtime_code_sha256 != exported.runtime_code_sha256
        or receipt.calibration.selection != tune.selection
        or receipt.calibration.calibration_score_operation_id != selected.operation_id
    ):
        raise SnapshotError("campaign_calibration_artifact_parent_mismatch")
    population = json.loads(read_bytes(bundle, "population.json", receipt.plan.max_output_bytes))
    if (
        any(
            population[key] != getattr(receipt, key) or population[key] != getattr(selected, key)
            for key in POPULATION
        )
        or population["selected_prediction_file_sha256"]
        != selected.artifact_files["predictions.jsonl"]
        or population["full_calibration_label_passes"] != 1
        or population["tune_label_passes"] != 0
        or population["independent_or_final_label_passes"] != 0
        or population["architecture_reselected"] is not False
    ):
        raise SnapshotError("campaign_calibration_artifact_population_or_scope_mismatch")
    if (
        CampaignForecastCalibration.model_validate_json(read_bytes(bundle, "calibration.json"))
        != receipt.calibration
    ):
        raise SnapshotError("campaign_calibration_artifact_result_mismatch")


def validate_completed_calibration(
    journal: Path, receipt: CampaignForecastCalibrationReceipt
) -> None:
    receipt = CampaignForecastCalibrationReceipt.model_validate_json(
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
        or receipt.plan.forecast_quality_policy_sha256
        != ledger.protocol.use_case_quality_policy_sha256["forecast"]
        or not {
            receipt.plan.export_operation_id,
            receipt.plan.tune_operation_id,
            *receipt.plan.score_operation_ids,
        }
        <= set(operation.prerequisites)
        or completion is None
        or completion.operation_id != receipt.operation_id
        or completion.result != "completed"
        or completion.evidence_sha256 != receipt.content_sha256()
        or completion.cost is None
        or completion.cost.artifact_bytes != receipt.artifact_bytes
    ):
        raise SnapshotError("campaign_calibration_receipt_not_completed")
    try:
        with regular_file(journal, "receipts/" + receipt.reservation_id + ".json") as stream:
            if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
                raise SnapshotError("campaign_calibration_private_receipt_required")
            raw = stream.read(MAX_RECEIPT_BYTES + 1)
    except OSError:
        raise SnapshotError("campaign_calibration_receipt_unavailable") from None
    if raw != canonical_bytes(receipt.model_dump(mode="json")) + b"\n":
        raise SnapshotError("campaign_calibration_stored_receipt_mismatch")


def verify_campaign_forecast_calibration(
    bundle: Path, *, journal: Path, receipt: CampaignForecastCalibrationReceipt
) -> None:
    """Verify completed frozen calibration without another label read or fitting grant."""
    validate_completed_calibration(journal, receipt)
    _verify_bundle(bundle, receipt)
