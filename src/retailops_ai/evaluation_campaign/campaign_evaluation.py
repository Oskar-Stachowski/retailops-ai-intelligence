"""Reserved complete-role evaluation, sequential fresh inference and durable evidence."""

import hashlib
import json
import os
import stat
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

from pydantic import JsonValue, TypeAdapter

from retailops_ai.data_contracts.identity import canonical_bytes, canonical_sha256
from retailops_ai.evaluation_campaign import campaign_journal
from retailops_ai.evaluation_campaign.campaign_calibration import (
    verify_campaign_forecast_calibration,
)
from retailops_ai.evaluation_campaign.campaign_calibration_contract import (
    CampaignForecastCalibrationReceipt,
)
from retailops_ai.evaluation_campaign.campaign_contract import (
    CampaignCost,
    CampaignJournal,
    CampaignOperationPlan,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_configuration import (
    bind_forecast_configuration,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_contract import (
    CampaignForecastEvaluationPlan,
    CampaignForecastEvaluationPrediction,
    CampaignForecastFrozenConfiguration,
)
from retailops_ai.evaluation_campaign.campaign_evaluation_receipt import (
    CampaignForecastEvaluationReceipt,
    CampaignForecastEvaluationRecipe,
    CampaignSelectionComponents,
    trial_metrics_name,
)
from retailops_ai.evaluation_campaign.campaign_export import (
    MAX_RECEIPT_BYTES,
    _store_receipt,
    validate_completed_export,
)
from retailops_ai.evaluation_campaign.campaign_export_contract import (
    CampaignDevelopmentExportReceipt,
)
from retailops_ai.evaluation_campaign.campaign_final_contract import CampaignFinalExportReceipt
from retailops_ai.evaluation_campaign.campaign_final_export import validate_completed_final_export
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
from retailops_ai.evaluation_campaign.campaign_score import validate_completed_score
from retailops_ai.evaluation_campaign.campaign_score_contract import (
    FAMILIES,
    CampaignForecastScoreReceipt,
)
from retailops_ai.evaluation_campaign.campaign_tune import verify_campaign_forecast_selection
from retailops_ai.evaluation_campaign.campaign_tune_contract import CampaignForecastTuneReceipt
from retailops_ai.evaluation_campaign.development_contract import MODELS
from retailops_ai.evaluation_campaign.partitions import membership_key
from retailops_ai.forecasting.features_contract import InputRow
from retailops_ai.source_snapshot.files import (
    SnapshotError,
    checked_directory,
    directory_fd,
    file_hash,
    read_bytes,
    regular_file,
)
from retailops_ai.source_snapshot.publish import fsync_tree

ExportReceipt = CampaignDevelopmentExportReceipt | CampaignFinalExportReceipt
POPULATION = (
    "rows",
    "eligible_rows",
    "keys_sha256",
    "eligible_keys_sha256",
    "role_population_sha256",
)
MAX_PARENT_METADATA_BYTES = 16 * 1024**2


def _operation(ledger: CampaignJournal, operation_id: str) -> CampaignOperationPlan:
    operation = next(
        (o for o in ledger.protocol.operations if o.operation_id == operation_id), None
    )
    if operation is None or (
        operation.action != "model_score"
        or operation.use_case != "forecast"
        or operation.role
        != ("final_evaluation" if operation.phase == "final" else "development_evaluation")
    ):
        raise SnapshotError("campaign_evaluation_requires_independent_or_final_forecast_operation")
    return operation


def selection_components(
    configuration: CampaignForecastFrozenConfiguration,
    calibration: CampaignForecastCalibrationReceipt,
) -> CampaignSelectionComponents:
    """Hash the complete selected functional inventories, including retained baselines."""
    choices = {
        "mean": configuration.calibration.selection.mean,
        "median": configuration.calibration.selection.median,
    }
    encoders: dict[str, Any] = {"runtime_code_sha256": configuration.runtime_code_sha256}
    for head, choice in choices.items():
        if choice is None:
            raise SnapshotError("campaign_evaluation_selected_functional_missing")
        trial = next(
            (
                t
                for t in configuration.trials
                if t.tune_score_operation_id == choice.score_operation_id
            ),
            None,
        )
        family: Family = (
            "rf"
            if choice.model == "rf_mean"
            else "tensorflow"
            if choice.model == "tensorflow"
            else "hgb"
        )
        encoders[head] = (
            {"baseline": choice.model}
            if trial is None
            else {"encoding_sha256": trial.encoding_sha256[family]}
        )
    return CampaignSelectionComponents(
        model_artifact_sha256=canonical_sha256(
            {
                head: choice.model_dump(mode="json") if choice else None
                for head, choice in choices.items()
            }
        ),
        preprocessing_sha256=canonical_sha256(encoders),
        calibration_sha256=calibration.artifact_sha256,
        threshold_policy_sha256=configuration.quality_policy_sha256,
        feature_schema_sha256=configuration.feature_schema_sha256,
    )


def _binding(
    ledger: CampaignJournal,
    operation: CampaignOperationPlan,
    plan: CampaignForecastEvaluationPlan,
    recipe: CampaignForecastEvaluationRecipe,
    exported: ExportReceipt,
    configuration: CampaignForecastFrozenConfiguration,
    tune: CampaignForecastTuneReceipt,
    calibration: CampaignForecastCalibrationReceipt,
    fits: dict[str, CampaignForecastFitReceipt],
) -> None:
    prerequisites = {plan.export_operation_id}
    if plan.phase == "development":
        prerequisites |= {configuration.tune_operation_id, configuration.calibration_operation_id}
    if (
        operation.execution_recipe_sha256 != recipe.content_sha256()
        or recipe.resolve(configuration) != plan
        or operation.phase != plan.phase
        or operation.source_recipe_sha256 != plan.source_recipe_sha256
        or plan.frozen_configuration_sha256 != configuration.content_sha256()
        or configuration.protocol_sha256 != ledger.protocol_sha256
        or configuration.runtime_code_sha256 != ledger.protocol.runtime.code_sha256
        or configuration.worker_environment_lock_sha256 != plan.worker_environment_lock_sha256
        or configuration.quality_policy_sha256 != plan.quality_policy.content_sha256()
        or configuration.quality_policy_sha256
        != ledger.protocol.use_case_quality_policy_sha256["forecast"]
        or plan.segment_policy_sha256 != ledger.protocol.segment_policy_sha256
        or plan.uncertainty_policy_sha256 != ledger.protocol.uncertainty_policy_sha256
        or configuration.feature_schema_sha256 != canonical_sha256(InputRow.model_json_schema())
        or exported.protocol_sha256 != ledger.protocol_sha256
        or exported.runtime_code_sha256 != ledger.protocol.runtime.code_sha256
        or exported.operation_id != plan.export_operation_id
        or exported.plan.source_recipe_sha256 != plan.source_recipe_sha256
        or not prerequisites <= set(operation.prerequisites)
        or isinstance(exported, CampaignFinalExportReceipt) != (plan.phase == "final")
        or plan.phase == "development"
        and (
            exported.dataset_id != configuration.development_dataset_id
            or plan.source_recipe_sha256 != configuration.development_source_recipe_sha256
        )
        or tune.content_sha256() != configuration.tune_receipt_sha256
        or calibration.content_sha256() != configuration.calibration_receipt_sha256
        or set(fits)
        != {key for trial in configuration.trials for key in trial.fit_operation_ids.values()}
    ):
        raise SnapshotError("campaign_evaluation_frozen_plan_or_parent_binding_mismatch")
    operations = {o.operation_id: o for o in ledger.protocol.operations}
    parent = operations.get(exported.operation_id)
    if parent is None or (
        parent.action != "source_read"
        or parent.role != "all_parent_data"
        or parent.use_case != "source"
        or parent.phase != plan.phase
        or parent.source_recipe_sha256 != plan.source_recipe_sha256
    ):
        raise SnapshotError("campaign_evaluation_export_operation_mismatch")


def _parents(
    journal: Path,
    configuration: CampaignForecastFrozenConfiguration,
    plan: CampaignForecastEvaluationPlan,
    tune_bundle: Path,
    calibration_bundle: Path,
    tune: CampaignForecastTuneReceipt,
    calibration: CampaignForecastCalibrationReceipt,
    fits: dict[str, CampaignForecastFitReceipt],
    fit_bundles: dict[str, Path],
) -> tuple[dict[str, CampaignForecastScoreReceipt], dict[str, CampaignForecastScoreReceipt]]:
    if set(fit_bundles) != set(fits):
        raise SnapshotError("campaign_evaluation_fit_bundle_inventory_mismatch")
    verify_campaign_forecast_selection(tune_bundle, journal=journal, receipt=tune)
    verify_campaign_forecast_calibration(calibration_bundle, journal=journal, receipt=calibration)
    parents = json.loads(read_bytes(calibration_bundle, "parents.json", MAX_PARENT_METADATA_BYTES))
    tune_scores = {
        key: CampaignForecastScoreReceipt.model_validate_json(canonical_bytes(value))
        for key, value in parents["tune_scores"].items()
    }
    scores = {
        key: CampaignForecastScoreReceipt.model_validate_json(canonical_bytes(value))
        for key, value in parents["scores"].items()
    }
    frozen = bind_forecast_configuration(
        tune,
        calibration,
        tuple(tune_scores[key] for key in tune.plan.score_operation_ids),
        tuple(scores[key] for key in calibration.plan.score_operation_ids),
        fits,
        feature_schema_sha256=configuration.feature_schema_sha256,
        quality_policy=plan.quality_policy,
    )
    if frozen != configuration:
        raise SnapshotError("campaign_evaluation_completed_configuration_mismatch")
    for score in (*tune_scores.values(), *scores.values()):
        validate_completed_score(journal, score)
    for operation, receipt in fits.items():
        verify_campaign_forecast_bundle(fit_bundles[operation], journal=journal, receipt=receipt)
    return tune_scores, scores


def _selection(
    journal: Path,
    ledger: CampaignJournal,
    exported: CampaignFinalExportReceipt,
    configuration: CampaignForecastFrozenConfiguration,
    calibration: CampaignForecastCalibrationReceipt,
    bundles: dict[str, Path],
) -> str:
    """The metadata freeze alone cannot authorize a fresh final evaluation."""
    event = next((e for e in ledger.events if e.kind == "selection_frozen"), None)
    if (
        event is None
        or event.selection is None
        or set(bundles) != {"forecast", "anomaly", "stockout"}
    ):
        raise SnapshotError("campaign_evaluation_requires_completed_three_use_selection")
    digest = canonical_sha256(event.selection.model_dump(mode="json"))
    if exported.selection_sha256 != digest:
        raise SnapshotError("campaign_evaluation_final_selection_binding_mismatch")
    operations = {o.operation_id: o for o in ledger.protocol.operations}
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
            or (
                operation.phase != "development"
                or operation.action != "model_score"
                or operation.role != "development_evaluation"
                or operation.use_case != selected.use_case
            )
        ):
            raise SnapshotError("campaign_evaluation_selection_has_no_completed_use_evaluation")
        with regular_file(journal, "receipts/" + str(completed.reservation_id) + ".json") as stream:
            if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
                raise SnapshotError("campaign_evaluation_private_selection_receipt_required")
            raw = stream.read(MAX_RECEIPT_BYTES + 1)
        value = json.loads(raw)
        if (
            raw != canonical_bytes(value) + b"\n"
            or canonical_sha256(value) != selected.selection_evidence_sha256
            or value.get("operation_id") != operation.operation_id
            or value.get("reservation_id") != completed.reservation_id
            or value.get("use_case") != selected.use_case
            or value.get("protocol_sha256") != ledger.protocol_sha256
            or value.get("runtime_code_sha256") != ledger.protocol.runtime.code_sha256
            or value.get("critical_segment_inventory_complete") is not True
            or value.get("block_uncertainty_complete") is not True
            or value.get("final_test_accessed") is not False
            or value.get("selection_components")
            != selected.model_dump(mode="json", exclude={"use_case", "selection_evidence_sha256"})
            or completed.cost is None
            or completed.cost.artifact_bytes != value.get("artifact_bytes")
        ):
            raise SnapshotError("campaign_evaluation_selection_receipt_incomplete_or_mismatched")
        if selected.use_case == "forecast":
            receipt = CampaignForecastEvaluationReceipt.model_validate_json(raw)
            if (
                receipt.configuration != configuration
                or receipt.selection_components != selection_components(configuration, calibration)
            ):
                raise SnapshotError("campaign_evaluation_selected_forecast_configuration_mismatch")
            _verify_bundle(bundles[selected.use_case], receipt)
        else:
            files, size = _bundle_inventory(bundles[selected.use_case], plan_maximum(value))
            if (
                files != value.get("artifact_files")
                or canonical_sha256(files) != value.get("artifact_sha256")
                or size != value.get("artifact_bytes")
            ):
                raise SnapshotError("campaign_evaluation_other_use_selection_artifact_mismatch")
    return digest


def plan_maximum(receipt: dict[str, Any]) -> int:
    maximum = receipt.get("plan", {}).get("max_output_bytes")
    if type(maximum) is not int or not 1024 <= maximum <= 32 * 1024**3:
        raise SnapshotError("campaign_evaluation_selection_artifact_budget_missing")
    return maximum


def _phase(
    phase: str,
    phase_root: Path,
    root: Path,
    request: dict[str, Any],
    interpreter: str,
    plan: CampaignForecastEvaluationPlan,
    started: float,
    handle: Any,
    evidence: list[dict[str, Any]],
    peak: int | None,
) -> tuple[dict[str, Any], int | None]:
    temporary = phase_root / "tmp"
    temporary.mkdir(mode=0o700, exist_ok=True)
    checked_directory(temporary)
    if stat.S_IMODE(temporary.stat().st_mode) != 0o700:
        raise SnapshotError("campaign_evaluation_private_temporary_directory_required")
    write(phase_root / "request.json", request)
    measured = monitor(
        [
            interpreter,
            "-I",
            "-B",
            str(Path(__file__).with_name("campaign_evaluation_entry.py")),
            phase,
            str(phase_root),
        ],
        root=phase_root,
        log=phase_root / (phase + ".log"),
        env=_environment(phase_root),
        scratch=(root,),
        resources=plan.resources,
        deadline=started + plan.resources.wall_seconds,
    )
    if measured["sampled_tree_peak_rss_bytes"] is not None:
        peak = max(peak or 0, int(measured["sampled_tree_peak_rss_bytes"]))
    handle.cost = CampaignCost(
        wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
    )
    write(phase_root / (phase + "-resources.json"), measured)
    if measured["status"] != "passed":
        raise SnapshotError("campaign_evaluation_phase_failed_" + phase)
    result = read(phase_root / (phase + ".json"))
    if type(result.get("worker_peak_rss_bytes")) is not int or result["worker_peak_rss_bytes"] <= 0:
        raise SnapshotError("campaign_evaluation_worker_resource_evidence_missing")
    peak = max(peak or 0, result["worker_peak_rss_bytes"])
    handle.cost = CampaignCost(
        wall_seconds=perf_counter() - started, peak_process_tree_rss_bytes=peak
    )
    if peak > plan.resources.tree_rss_bytes:
        raise SnapshotError("campaign_evaluation_worker_peak_rss_limit")
    if result.get("phase") != phase:
        raise SnapshotError("campaign_evaluation_worker_phase_mismatch")
    evidence.append({"phase": phase, "monitor": measured, "worker": result})
    return result, peak


def evaluate_campaign_forecast(
    dataset: Path,
    fit_bundles: dict[str, Path],
    tune_bundle: Path,
    calibration_bundle: Path,
    worker_python: Path,
    output_root: Path,
    *,
    journal: Path,
    operation_id: str,
    recipe: CampaignForecastEvaluationRecipe,
    exported: ExportReceipt,
    configuration: CampaignForecastFrozenConfiguration,
    tune: CampaignForecastTuneReceipt,
    calibration: CampaignForecastCalibrationReceipt,
    fits: dict[str, CampaignForecastFitReceipt],
    selection_bundles: dict[str, Path] | None = None,
) -> tuple[Path, CampaignForecastEvaluationReceipt]:
    ledger = campaign_journal.inspect(journal)
    operation = _operation(ledger, operation_id)
    started = perf_counter()
    with campaign_journal.audited_operation(journal, operation_id) as handle:
        recipe = CampaignForecastEvaluationRecipe.model_validate_json(
            canonical_bytes(recipe.model_dump(mode="json"))
        )
        configuration = CampaignForecastFrozenConfiguration.model_validate_json(
            canonical_bytes(configuration.model_dump(mode="json"))
        )
        plan = recipe.resolve(configuration)
        exported = type(exported).model_validate_json(
            canonical_bytes(exported.model_dump(mode="json"))
        )
        tune = CampaignForecastTuneReceipt.model_validate_json(
            canonical_bytes(tune.model_dump(mode="json"))
        )
        calibration = CampaignForecastCalibrationReceipt.model_validate_json(
            canonical_bytes(calibration.model_dump(mode="json"))
        )
        fits = {
            key: CampaignForecastFitReceipt.model_validate_json(
                canonical_bytes(value.model_dump(mode="json"))
            )
            for key, value in fits.items()
        }
        _binding(ledger, operation, plan, recipe, exported, configuration, tune, calibration, fits)
        if isinstance(exported, CampaignFinalExportReceipt):
            validate_completed_final_export(journal, exported)
        else:
            validate_completed_export(journal, exported)
        tune_scores, scores = _parents(
            journal,
            configuration,
            plan,
            tune_bundle,
            calibration_bundle,
            tune,
            calibration,
            fits,
            fit_bundles,
        )
        selection = (
            _selection(
                journal, ledger, exported, configuration, calibration, selection_bundles or {}
            )
            if isinstance(exported, CampaignFinalExportReceipt)
            else None
        )
        checked_directory(output_root)
        if stat.S_IMODE(output_root.stat().st_mode) != 0o700:
            raise SnapshotError("campaign_evaluation_private_output_directory_required")
        root = output_root / str(handle.reservation.reservation_id)
        root.mkdir(mode=0o700)
        bundle = root / "bundle"
        bundle.mkdir(mode=0o700)
        (bundle / "trials").mkdir(mode=0o700)
        write(bundle / "plan.json", plan.model_dump(mode="json"))
        write(bundle / "recipe.json", recipe.model_dump(mode="json"))
        write(bundle / "configuration.json", configuration.model_dump(mode="json"))
        write(
            bundle / "parents.json",
            {
                "exported": exported.model_dump(mode="json"),
                "tune": tune.model_dump(mode="json"),
                "calibration": calibration.model_dump(mode="json"),
                "fits": {key: value.model_dump(mode="json") for key, value in fits.items()},
                "tune_scores": {
                    key: value.model_dump(mode="json") for key, value in tune_scores.items()
                },
                "calibration_scores": {
                    key: value.model_dump(mode="json") for key, value in scores.items()
                },
            },
        )
        prepared_root = root / "prepare"
        prepared_root.mkdir(mode=0o700)
        common = {
            "plan": plan.model_dump(mode="json"),
            "runtime": ledger.protocol.runtime.model_dump(mode="json"),
        }
        phases: list[dict[str, Any]] = []
        peak: int | None = None
        population, peak = _phase(
            "prepare",
            prepared_root,
            root,
            common
            | {
                "dataset": str(dataset.absolute()),
                "exported": exported.model_dump(mode="json"),
            },
            sys.executable,
            plan,
            started,
            handle,
            phases,
            peak,
        )
        if population.get("full_role_label_file_passes") != 1:
            raise SnapshotError("campaign_evaluation_preparation_role_pass_mismatch")
        write(
            bundle / "population.json",
            {
                key: population[key]
                for key in (
                    *POPULATION,
                    "inputs_sha256",
                    "actuals_sha256",
                    "full_role_label_file_passes",
                )
            },
        )
        inputs, actuals, projection = (
            prepared_root / "inputs.sqlite",
            prepared_root / "actuals.sqlite",
            root / "projection.sqlite",
        )
        traces, baseline = {}, None
        for index, trial in enumerate(configuration.trials):
            trial_root = root / f"trial-{index:03d}"
            trial_root.mkdir(mode=0o700)
            overhead = file_hash(actuals.parent, actuals.name)[0]
            if index:
                overhead += file_hash(projection.parent, projection.name)[0]
            request = common | {
                "population": population,
                "inputs": str(inputs),
                "index_overhead_bytes": overhead,
                "frozen_configuration_sha256": plan.frozen_configuration_sha256,
                "trial": trial.model_dump(mode="json"),
                "fits": {
                    f: fits[trial.fit_operation_ids[f]].model_dump(mode="json") for f in FAMILIES
                },
                "bundles": {
                    f: str(fit_bundles[trial.fit_operation_ids[f]].absolute()) for f in FAMILIES
                },
            }
            predicted, peak = _phase(
                "predict",
                trial_root,
                root,
                request,
                str(worker_python),
                plan,
                started,
                handle,
                phases,
                peak,
            )
            if (
                any(predicted[key] != population[key] for key in POPULATION)
                or predicted.get("actual_index_passes") != 0
                or predicted.get("all_models_share_all_role_keys") is not True
                or baseline is not None
                and baseline != predicted["baseline_trace_sha256"]
            ):
                raise SnapshotError(
                    "campaign_evaluation_trial_common_population_or_baseline_mismatch"
                )
            baseline = predicted["baseline_trace_sha256"]
            traces[trial.tune_score_operation_id] = predicted["prediction_trace_sha256"]
            # Consume uses a different request and a core process, after the model
            # process exits. It alone receives the actual index path.
            (trial_root / "request.json").unlink()
            consumed, peak = _phase(
                "consume",
                trial_root,
                root,
                common
                | {
                    "configuration": configuration.model_dump(mode="json"),
                    "population": population,
                    "trial": trial.model_dump(mode="json"),
                    "predicted": predicted,
                    "inputs": str(inputs),
                    "actuals": str(actuals),
                    "projection": str(projection),
                    "metrics_output": str(bundle / trial_metrics_name(index)),
                },
                sys.executable,
                plan,
                started,
                handle,
                phases,
                peak,
            )
            if (
                any(consumed[key] != population[key] for key in POPULATION)
                or consumed.get("actual_index_passes") != 1
                or consumed.get("selected_heads_written") is not True
                or consumed["prediction_trace_sha256"] != predicted["prediction_trace_sha256"]
                or consumed["baseline_trace_sha256"] != baseline
                or consumed["metrics_sha256"] != file_hash(bundle, trial_metrics_name(index))[1]
            ):
                raise SnapshotError("campaign_evaluation_consumed_trial_evidence_mismatch")
            fsync_tree(bundle / "trials")
            with regular_file(trial_root, "predictions.sqlite"), directory_fd(trial_root) as fd:
                os.unlink("predictions.sqlite", dir_fd=fd)
                os.fsync(fd)
        final_root = root / "finalize"
        final_root.mkdir(mode=0o700)
        finalized, peak = _phase(
            "finalize",
            final_root,
            root,
            common
            | {
                "configuration": configuration.model_dump(mode="json"),
                "population": population,
                "actuals": str(actuals),
                "projection": str(projection),
                "bundle": str(bundle),
            },
            sys.executable,
            plan,
            started,
            handle,
            phases,
            peak,
        )
        if (
            any(finalized[key] != population[key] for key in POPULATION)
            or finalized.get("actual_index_passes") != 1
            or finalized.get("all_frozen_trials_compared") is not True
            or finalized.get("architecture_reselected") is not False
        ):
            raise SnapshotError("campaign_evaluation_final_composition_scope_mismatch")
        files, size = _bundle_inventory(bundle, plan.max_output_bytes)
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=peak,
            artifact_bytes=size,
        )
        if not isinstance(baseline, str):
            raise SnapshotError("campaign_evaluation_baseline_trace_missing")
        metrics = read(bundle / "metrics.json")
        receipt = CampaignForecastEvaluationReceipt(
            protocol_sha256=ledger.protocol_sha256,
            operation_id=operation_id,
            reservation_id=str(handle.reservation.reservation_id),
            recipe=recipe,
            plan=plan,
            configuration=configuration,
            export_receipt_sha256=exported.content_sha256(),
            selection_sha256=selection,
            selection_components=selection_components(configuration, calibration),
            dataset_id=exported.dataset_id,
            runtime_code_sha256=ledger.protocol.runtime.code_sha256,
            **{key: population[key] for key in POPULATION},
            trial_prediction_trace_sha256=traces,
            baseline_trace_sha256=baseline,
            artifact_sha256=canonical_sha256(files),
            artifact_bytes=size,
            artifact_files=files,
            worker_evidence=TypeAdapter(dict[str, JsonValue]).validate_python({"phases": phases}),
            actual_index_passes=len(configuration.trials) + 1,
            final_test_accessed=plan.phase == "final",
            critical_segment_inventory_complete=metrics["critical_segment_inventory_complete"],
            block_uncertainty_complete=metrics["block_uncertainty_complete"],
            quality_qualified=metrics["quality_qualified"],
        )
        _verify_bundle(bundle, receipt)
        fsync_tree(root)
        if (
            perf_counter() - started > plan.resources.wall_seconds
            or scratch_bytes((root,)) > plan.resources.scratch_bytes
        ):
            raise SnapshotError("campaign_evaluation_completion_resource_limit")
        _store_receipt(journal, receipt)
        handle.evidence_sha256 = receipt.content_sha256()
        handle.cost = CampaignCost(
            wall_seconds=perf_counter() - started,
            peak_process_tree_rss_bytes=peak,
            artifact_bytes=size,
        )
    validate_completed_evaluation(journal, receipt)
    return bundle, receipt


def _segments(metrics: dict[str, Any], rows: int, eligible: int, *, raw: bool) -> None:
    segments = metrics.get("segments")
    row_field = "rows" if raw else "total_rows"
    if (
        not isinstance(segments, list)
        or len(segments) != 15
        or [s.get("horizon") for s in segments if isinstance(s, dict)]
        != ["all", *map(str, range(1, 15))]
        or any(
            type(s.get(row_field)) is not int
            or type(s.get("eligible_rows")) is not int
            or not 0 <= s["eligible_rows"] <= s[row_field]
            for s in segments
        )
        or (segments[0][row_field], segments[0]["eligible_rows"]) != (rows, eligible)
        or sum(s[row_field] for s in segments[1:]) != rows
        or sum(s["eligible_rows"] for s in segments[1:]) != eligible
        or raw
        and any(set(s.get("models", {})) != set(MODELS) for s in segments)
    ):
        raise SnapshotError("campaign_evaluation_metrics_complete_population_mismatch")


def _verify_bundle(bundle: Path, receipt: CampaignForecastEvaluationReceipt) -> None:
    configuration_sha256 = receipt.configuration.content_sha256()
    files, size = _bundle_inventory(bundle, receipt.plan.max_output_bytes)
    if files != receipt.artifact_files or size != receipt.artifact_bytes:
        raise SnapshotError("campaign_evaluation_artifact_checksum_mismatch")
    if (
        CampaignForecastEvaluationPlan.model_validate_json(read_bytes(bundle, "plan.json"))
        != receipt.plan
        or CampaignForecastEvaluationRecipe.model_validate_json(read_bytes(bundle, "recipe.json"))
        != receipt.recipe
        or CampaignForecastFrozenConfiguration.model_validate_json(
            read_bytes(bundle, "configuration.json")
        )
        != receipt.configuration
    ):
        raise SnapshotError("campaign_evaluation_artifact_configuration_or_plan_mismatch")
    parents = json.loads(read_bytes(bundle, "parents.json", MAX_PARENT_METADATA_BYTES))
    exported = (
        CampaignFinalExportReceipt
        if receipt.plan.phase == "final"
        else CampaignDevelopmentExportReceipt
    ).model_validate_json(canonical_bytes(parents["exported"]))
    tune = CampaignForecastTuneReceipt.model_validate_json(canonical_bytes(parents["tune"]))
    calibration = CampaignForecastCalibrationReceipt.model_validate_json(
        canonical_bytes(parents["calibration"])
    )
    fits = {
        key: CampaignForecastFitReceipt.model_validate_json(canonical_bytes(value))
        for key, value in parents["fits"].items()
    }
    tune_scores = {
        key: CampaignForecastScoreReceipt.model_validate_json(canonical_bytes(value))
        for key, value in parents["tune_scores"].items()
    }
    scores = {
        key: CampaignForecastScoreReceipt.model_validate_json(canonical_bytes(value))
        for key, value in parents["calibration_scores"].items()
    }
    frozen = bind_forecast_configuration(
        tune,
        calibration,
        tuple(tune_scores[key] for key in tune.plan.score_operation_ids),
        tuple(scores[key] for key in calibration.plan.score_operation_ids),
        fits,
        feature_schema_sha256=receipt.configuration.feature_schema_sha256,
        quality_policy=receipt.plan.quality_policy,
    )
    if (
        frozen != receipt.configuration
        or exported.content_sha256() != receipt.export_receipt_sha256
        or exported.dataset_id != receipt.dataset_id
        or exported.protocol_sha256 != receipt.protocol_sha256
        or exported.runtime_code_sha256 != receipt.runtime_code_sha256
        or exported.plan.source_recipe_sha256 != receipt.plan.source_recipe_sha256
        or exported.operation_id != receipt.plan.export_operation_id
        or receipt.selection_components != selection_components(frozen, calibration)
    ):
        raise SnapshotError("campaign_evaluation_artifact_parent_binding_mismatch")
    population = read(bundle / "population.json")
    if (
        any(population[key] != getattr(receipt, key) for key in POPULATION)
        or population.get("full_role_label_file_passes") != 1
    ):
        raise SnapshotError("campaign_evaluation_artifact_population_mismatch")
    metrics = read(bundle / "metrics.json")
    # Full robustness needs a complete versioned segment and uncertainty report.
    # The current worker emits only this explicit component scope; no receipt can
    # turn it into qualification by changing a flag.
    if (
        metrics.get("scope")
        != "complete_key_global_horizon_component_pending_critical_segments_and_uncertainty"
        or metrics.get("role") != receipt.plan.role
        or metrics.get("final_test_accessed") != receipt.final_test_accessed
        or any(
            metrics.get(key) is not False or getattr(receipt, key) is not False
            for key in (
                "critical_segment_inventory_complete",
                "block_uncertainty_complete",
                "quality_qualified",
                "stage_ready",
            )
        )
    ):
        raise SnapshotError("campaign_evaluation_metrics_scope_or_qualification_mismatch")
    _segments(metrics, receipt.rows, receipt.eligible_rows, raw=False)
    for index, trial in enumerate(receipt.configuration.trials):
        result = read(bundle / trial_metrics_name(index))
        if (
            result.get("trial_tune_score_operation_id") != trial.tune_score_operation_id
            or result.get("prediction_trace_sha256")
            != receipt.trial_prediction_trace_sha256[trial.tune_score_operation_id]
            or result.get("baseline_trace_sha256") != receipt.baseline_trace_sha256
            or result.get("role") != receipt.plan.role
            or result.get("final_test_accessed") != receipt.final_test_accessed
            or any(
                result.get(key) is not False
                for key in (
                    "raw_learned_intervals_calibrated",
                    "architecture_reselected",
                    "quality_qualified",
                    "stage_ready",
                )
            )
        ):
            raise SnapshotError("campaign_evaluation_trial_metrics_binding_mismatch")
        _segments(result, receipt.rows, receipt.eligible_rows, raw=True)
    keys, eligible_keys = hashlib.sha256(), hashlib.sha256()
    previous = None
    rows = eligible = 0
    with regular_file(bundle, "predictions.jsonl") as stream:
        for line in stream:
            if len(line) > 128 * 1024:
                raise SnapshotError("campaign_evaluation_prediction_record_budget")
            row = CampaignForecastEvaluationPrediction.model_validate_json(line)
            key = membership_key(row)
            if (
                row.role != receipt.plan.role
                or row.frozen_configuration_sha256 != configuration_sha256
                or previous is not None
                and key <= previous
                or line != canonical_bytes(row.model_dump(mode="json")) + b"\n"
            ):
                raise SnapshotError(
                    "campaign_evaluation_prediction_key_role_or_configuration_mismatch"
                )
            previous = key
            keys.update(key + b"\n")
            rows += 1
            if row.eligible:
                eligible += 1
                eligible_keys.update(key + b"\n")
            if rows > receipt.plan.max_rows:
                raise SnapshotError("campaign_evaluation_prediction_population_budget")
    if (rows, eligible, keys.hexdigest(), eligible_keys.hexdigest()) != (
        receipt.rows,
        receipt.eligible_rows,
        receipt.keys_sha256,
        receipt.eligible_keys_sha256,
    ):
        raise SnapshotError("campaign_evaluation_prediction_complete_population_mismatch")


def validate_completed_evaluation(
    journal: Path, receipt: CampaignForecastEvaluationReceipt
) -> None:
    receipt = CampaignForecastEvaluationReceipt.model_validate_json(
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
    selection = next((e for e in ledger.events if e.kind == "selection_frozen"), None)
    if (
        receipt.protocol_sha256 != ledger.protocol_sha256
        or receipt.runtime_code_sha256 != ledger.protocol.runtime.code_sha256
        or operation.execution_recipe_sha256 != receipt.recipe.content_sha256()
        or operation.source_recipe_sha256 != receipt.plan.source_recipe_sha256
        or operation.phase != receipt.plan.phase
        or completion is None
        or completion.operation_id != receipt.operation_id
        or completion.result != "completed"
        or completion.evidence_sha256 != receipt.content_sha256()
        or completion.cost is None
        or completion.cost.artifact_bytes != receipt.artifact_bytes
        or completion.cost.peak_process_tree_rss_bytes is None
        or receipt.plan.phase == "final"
        and (
            selection is None
            or selection.selection is None
            or canonical_sha256(selection.selection.model_dump(mode="json"))
            != receipt.selection_sha256
        )
    ):
        raise SnapshotError("campaign_evaluation_receipt_not_completed")
    try:
        with regular_file(journal, "receipts/" + receipt.reservation_id + ".json") as stream:
            if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o600:
                raise SnapshotError("campaign_evaluation_private_receipt_required")
            raw = stream.read(MAX_RECEIPT_BYTES + 1)
    except OSError:
        raise SnapshotError("campaign_evaluation_receipt_unavailable") from None
    if raw != canonical_bytes(receipt.model_dump(mode="json")) + b"\n":
        raise SnapshotError("campaign_evaluation_stored_receipt_mismatch")


def verify_campaign_forecast_evaluation(
    bundle: Path, *, journal: Path, receipt: CampaignForecastEvaluationReceipt
) -> None:
    """Verify committed predictions and metadata without reading original outcomes."""
    validate_completed_evaluation(journal, receipt)
    _verify_bundle(bundle, receipt)
